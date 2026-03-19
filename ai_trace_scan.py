"""
ai-trace-scan — Detect AI/agentic authorship fingerprints in a codebase.

Scans git history, file names, and source comments for traces left by
AI coding assistants (Copilot, Claude, Cursor, Aider, etc).
"""

import argparse
import json
import os
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import pathspec
from pygments import lexers
from pygments.token import Token

Finding = namedtuple("Finding", ["severity", "category", "location", "message"])

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

TRAILER_PATTERNS = [
    (r"Co-authored-by:.*(?:Copilot|copilot|GitHub\sCopilot)", "Co-authored-by Copilot trailer"),
    (r"Co-authored-by:.*(?:Claude|Anthropic)", "Co-authored-by Claude trailer"),
    (r"Co-authored-by:.*(?:GPT|OpenAI|ChatGPT)", "Co-authored-by GPT/OpenAI trailer"),
    (r"Co-authored-by:.*(?:Cursor|Aider|Codeium|Tabnine|Gemini)", "Co-authored-by AI tool trailer"),
]

COMMIT_MSG_PATTERNS = [
    (r"\b(?:as an AI|as a language model|per your instructions)\b", "Agentic language in commit message"),
    (r"\breview:\s*Copilot\b", "Copilot review marker"),
    (r"\bgenerated (?:by|with|using) (?:Copilot|Claude|GPT|AI|Cursor|Aider|Gemini)\b",
     "AI generation attribution"),
    (r"\b(?:copilot|claude|cursor|aider)\s+(?:suggested|generated|wrote|created)\b",
     "AI tool attribution"),
]

BOT_AUTHOR_PATTERNS = [
    (r"copilot\[bot\]", "Copilot bot author"),
    (r"github-actions\[bot\].*copilot", "GitHub Actions Copilot bot"),
    (r"\+Copilot@users\.noreply\.github\.com", "Copilot noreply email"),
    (r"devin\[bot\]", "Devin bot author"),
    (r"sweep\[bot\]", "Sweep bot author"),
]

BRANCH_PATTERNS = [
    r"^copilot/",
    r"^claude/",
    r"^ai[-/]",
    r"^cursor[-/]",
    r"^aider[-/]",
    r"^gemini[-/]",
    r"^devin[-/]",
    r"^sweep[-/]",
]

AGENT_CONFIG_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    "GEMINI.md",
    ".cursorrules",
    ".cursorignore",
    ".aider.conf.yml",
    ".aider.input.history",
    ".aider.chat.history.md",
    ".aider.tags.cache.v3",
    ".github/copilot-instructions.md",
    ".github/copilot-review-instructions.md",
]

AGENT_CONFIG_GLOBS = [
    ".cursor/",
    ".aider*",
    ".copilot/",
]

COMMENT_PATTERNS = [
    (r"\bgenerated (?:by|with|using) (?:copilot|claude|gpt|chatgpt|ai|cursor|aider|gemini)\b",
     "AI generation attribution in comment"),
    (r"\bcopilot[- ]generated\b", "Copilot-generated marker"),
    (r"\b(?:claude|gpt-?4|gpt-?3|chatgpt)\s+(?:wrote|generated|suggested|created)\b",
     "AI tool attribution in comment"),
    (r"@generated\s+by\s+(?:ai|copilot|claude)", "Generated-by annotation"),
]

TEXT_EXTENSIONS = {
    ".md", ".rst", ".txt", ".adoc",
}

TEXT_FILENAMES = {
    "Makefile", "Dockerfile", "Containerfile", "Jenkinsfile",
    "Vagrantfile", "Rakefile", "Gemfile", "Procfile",
    ".gitignore", ".dockerignore", ".editorconfig",
}

SKIP_DIRS = {
    ".git", "node_modules", "vendor", "venv", ".venv", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    ".terraform", ".tofu", "target", "bin", "obj",
}

SELF_DIR = Path(__file__).resolve().parent

CONFIG_FILENAME = ".ai-trace-scan.yml"


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------

def load_config(root):
    """Load .ai-trace-scan.yml from repo root if present."""
    config_path = root / CONFIG_FILENAME
    if not config_path.is_file():
        return {}
    try:
        try:
            import yaml
            with open(config_path) as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            pass

        # Fallback: parse simple key: [val, val] lines without PyYAML
        config = {}
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    key, _, val = line.partition(":")
                    val = val.strip()
                    if val.startswith("[") and val.endswith("]"):
                        val = [v.strip().strip("'\"") for v in val[1:-1].split(",")]
                    config[key.strip()] = val
        return config
    except OSError:
        return {}


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------

def git(*args, cwd=None):
    try:
        r = subprocess.run(
            ["git", "--no-pager"] + list(args),
            capture_output=True, text=True, cwd=cwd, timeout=30,
        )
        return r.stdout if r.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def is_git_repo(path):
    return git("rev-parse", "--git-dir", cwd=path) is not None


def get_default_branch(cwd):
    for name in ("main", "master"):
        if git("rev-parse", "--verify", name, cwd=cwd) is not None:
            return name
    out = git("symbolic-ref", "refs/remotes/origin/HEAD", cwd=cwd)
    if out:
        return out.strip().split("/")[-1]
    return None


# ---------------------------------------------------------------------------
# Exclude filter
# ---------------------------------------------------------------------------

def make_exclude_filter(patterns):
    """Return a function that checks if a string matches any exclude pattern."""
    if not patterns:
        return lambda _: False
    compiled = [re.compile(p) for p in patterns]
    return lambda s: any(r.search(s) for r in compiled)


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------

def scan_commits(cwd, rev_range, max_commits, exclude_fn):
    findings = []
    # %aE = author email
    fmt = "%H%n%aE%n%s%n%b%n---END---"
    out = git("log", f"--max-count={max_commits}", f"--format={fmt}", rev_range, cwd=cwd)
    if not out:
        return findings

    for block in out.split("---END---"):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        if len(lines) < 3:
            continue
        sha = lines[0][:12]
        author_email = lines[1]
        subject = lines[2][:60]
        body = "\n".join(lines[2:])

        if exclude_fn(f"commit {sha}"):
            continue

        for pattern, label in BOT_AUTHOR_PATTERNS:
            if re.search(pattern, author_email, re.IGNORECASE):
                findings.append(Finding(
                    severity="high",
                    category="git-history",
                    location=f"commit {sha} ({subject})",
                    message=f"{label}: {author_email}",
                ))

        for pattern, label in TRAILER_PATTERNS + COMMIT_MSG_PATTERNS:
            if re.search(pattern, body, re.IGNORECASE):
                findings.append(Finding(
                    severity="high",
                    category="git-history",
                    location=f"commit {sha} ({subject})",
                    message=label,
                ))
    return findings


def scan_tags(cwd, exclude_fn):
    """Scan annotated tag messages for AI traces."""
    findings = []
    out = git("tag", "-l", cwd=cwd)
    if not out:
        return findings

    for tag in out.strip().split("\n"):
        tag = tag.strip()
        if not tag or exclude_fn(tag):
            continue
        msg = git("tag", "-l", "--format=%(contents)", tag, cwd=cwd)
        if not msg:
            continue
        for pattern, label in TRAILER_PATTERNS + COMMIT_MSG_PATTERNS:
            if re.search(pattern, msg, re.IGNORECASE):
                findings.append(Finding(
                    severity="high",
                    category="git-tag",
                    location=f"tag {tag}",
                    message=label,
                ))
    return findings


def scan_branches(cwd, exclude_fn):
    findings = []
    out = git("branch", "-a", "--format=%(refname:short)", cwd=cwd)
    if not out:
        return findings

    for branch in out.strip().split("\n"):
        branch = branch.strip()
        if exclude_fn(branch):
            continue
        short = branch.split("/", 1)[-1] if "/" in branch else branch
        for pattern in BRANCH_PATTERNS:
            if re.search(pattern, short, re.IGNORECASE):
                findings.append(Finding(
                    severity="medium",
                    category="branch-name",
                    location=branch,
                    message=f"Branch name matches AI tool pattern: {pattern}",
                ))
    return findings


def scan_config_files(root, exclude_fn):
    findings = []
    for name in AGENT_CONFIG_FILES:
        if exclude_fn(name):
            continue
        path = root / name
        if path.exists():
            findings.append(Finding(
                severity="high" if name.endswith(".md") else "medium",
                category="config-file",
                location=str(name),
                message="AI assistant config file present",
            ))

    for pattern in AGENT_CONFIG_GLOBS:
        for match in root.glob(pattern):
            rel = str(match.relative_to(root))
            if rel not in AGENT_CONFIG_FILES and not exclude_fn(rel):
                findings.append(Finding(
                    severity="medium",
                    category="config-file",
                    location=rel,
                    message="AI assistant config file/directory present",
                ))
    return findings


def should_scan_file(path):
    """Check if file is a plain-text format that pygments won't handle."""
    if path.name in TEXT_FILENAMES:
        return True
    return path.suffix.lower() in TEXT_EXTENSIONS


def get_lexer(filepath):
    """Try to get a pygments lexer for the file. Returns None if unsupported."""
    try:
        return lexers.get_lexer_for_filename(filepath.name)
    except lexers.ClassNotFound:
        return None


def extract_comments(filepath):
    """Use pygments to extract only comment tokens with line numbers."""
    try:
        source = filepath.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return

    lexer = get_lexer(filepath)
    if not lexer:
        return

    lineno = 1
    for ttype, value in lexer.get_tokens(source):
        line_start = lineno
        lineno += value.count("\n")

        if ttype in Token.Comment or ttype in Token.Comment.Single \
                or ttype in Token.Comment.Multiline or ttype in Token.Comment.Special \
                or ttype is Token.Comment.Hashbang \
                or str(ttype).startswith("Token.Comment"):
            yield line_start, value


def scan_file_comments(filepath, root):
    """Scan a file for AI patterns — pygments for code, regex for plain text."""
    findings = []
    rel = filepath.relative_to(root)
    lexer = get_lexer(filepath)

    if lexer:
        for lineno, comment_text in extract_comments(filepath):
            for pattern, label in COMMENT_PATTERNS:
                if re.search(pattern, comment_text, re.IGNORECASE):
                    findings.append(Finding(
                        severity="medium",
                        category="source-comment",
                        location=f"{rel}:{lineno}",
                        message=label,
                    ))
    elif should_scan_file(filepath):
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, 1):
                    for pattern, label in COMMENT_PATTERNS:
                        if re.search(pattern, line, re.IGNORECASE):
                            findings.append(Finding(
                                severity="medium",
                                category="source-comment",
                                location=f"{rel}:{lineno}",
                                message=label,
                            ))
        except OSError:
            pass

    return findings


def load_gitignore(root):
    """Load .gitignore patterns if present."""
    gitignore_path = root / ".gitignore"
    if not gitignore_path.is_file():
        return None
    try:
        lines = gitignore_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return pathspec.PathSpec.from_lines("gitwildmatch", lines)
    except OSError:
        return None


def scan_source_tree(root, exclude_fn):
    findings = []
    skip_self = root == SELF_DIR or SELF_DIR.is_relative_to(root)
    ignore_spec = load_gitignore(root)

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            filepath = Path(dirpath) / name
            if skip_self and filepath.resolve().is_relative_to(SELF_DIR):
                continue

            rel_str = str(filepath.relative_to(root))
            if ignore_spec and ignore_spec.match_file(rel_str):
                continue
            if exclude_fn(rel_str):
                continue

            if get_lexer(filepath) or should_scan_file(filepath):
                findings.extend(scan_file_comments(filepath, root))

    return findings


def scan_staged(cwd, exclude_fn):
    findings = []
    out = git("diff", "--cached", "--unified=0", cwd=cwd)
    if not out:
        return findings

    current_file = None
    for line in out.split("\n"):
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            if current_file and exclude_fn(current_file):
                continue
            added = line[1:]
            for pattern, label in TRAILER_PATTERNS + COMMENT_PATTERNS:
                if re.search(pattern, added, re.IGNORECASE):
                    findings.append(Finding(
                        severity="high" if "trailer" in label.lower() else "medium",
                        category="staged-change",
                        location=current_file or "(unknown file)",
                        message=label,
                    ))
    return findings


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

SEVERITY_COLORS = {"high": "\033[91m", "medium": "\033[93m", "low": "\033[90m"}
RESET = "\033[0m"
BOLD = "\033[1m"


def supports_color():
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def format_text(findings, use_color):
    lines = []
    if not findings:
        mark = "\033[92m✓\033[0m" if use_color else "✓"
        lines.append(f"\n  {mark} No AI authorship traces found.\n")
        return "\n".join(lines)

    lines.append(f"\n  Found {len(findings)} finding(s):\n")

    by_category = {}
    for f in findings:
        by_category.setdefault(f.category, []).append(f)

    for cat, items in by_category.items():
        header = cat.replace("-", " ").title()
        if use_color:
            lines.append(f"  {BOLD}{header}{RESET}")
        else:
            lines.append(f"  {header}")

        for item in items:
            sev = item.severity.upper()
            if use_color:
                color = SEVERITY_COLORS.get(item.severity, "")
                lines.append(f"    {color}[{sev}]{RESET} {item.location}")
                lines.append(f"           {item.message}")
            else:
                lines.append(f"    [{sev}] {item.location}")
                lines.append(f"           {item.message}")
        lines.append("")

    return "\n".join(lines)


def format_json(findings):
    return json.dumps([f._asdict() for f in findings], indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="ai-trace-scan",
        description="Detect AI/agentic authorship fingerprints in a codebase.",
    )
    parser.add_argument("path", nargs="?", default=".",
                        help="Repository path to scan (default: current directory)")
    parser.add_argument("--staged", action="store_true",
                        help="Scan only staged changes (pre-commit hook mode)")
    parser.add_argument("--branch", metavar="REF",
                        help="Scan commits in REF not in main/master")
    parser.add_argument("--commits", type=int, default=50, metavar="N",
                        help="Max commits to scan (default: 50)")
    parser.add_argument("--exclude", action="append", default=[], metavar="PATTERN",
                        help="Exclude findings matching regex (repeatable)")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        dest="output_format",
                        help="Output format (default: text)")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable colored output")
    parser.add_argument("--quiet", action="store_true",
                        help="Only print findings, no banner")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    use_color = supports_color() and not args.no_color and args.output_format == "text"

    if not root.is_dir():
        print(f"Error: {root} is not a directory", file=sys.stderr)
        sys.exit(2)

    # Merge excludes from config file and CLI
    config = load_config(root)
    excludes = list(args.exclude)
    config_excludes = config.get("exclude", [])
    if isinstance(config_excludes, list):
        excludes.extend(config_excludes)
    elif isinstance(config_excludes, str):
        excludes.append(config_excludes)
    exclude_fn = make_exclude_filter(excludes)

    if not args.quiet and args.output_format == "text":
        name = root.name
        if use_color:
            print(f"\n  {BOLD}ai-trace-scan{RESET} — {name}")
        else:
            print(f"\n  ai-trace-scan — {name}")

    findings = []
    has_git = is_git_repo(root)

    if args.staged:
        if not has_git:
            print("Error: --staged requires a git repository", file=sys.stderr)
            sys.exit(2)
        findings.extend(scan_staged(root, exclude_fn))
    else:
        if has_git:
            if args.branch:
                default = get_default_branch(root)
                rev_range = f"{default}..{args.branch}" if default else args.branch
            else:
                rev_range = "HEAD"

            findings.extend(scan_commits(root, rev_range, args.commits, exclude_fn))
            findings.extend(scan_branches(root, exclude_fn))
            findings.extend(scan_tags(root, exclude_fn))

        findings.extend(scan_config_files(root, exclude_fn))
        findings.extend(scan_source_tree(root, exclude_fn))

    if args.output_format == "json":
        print(format_json(findings))
    else:
        print(format_text(findings, use_color))

    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
