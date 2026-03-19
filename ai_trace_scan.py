"""
ai-trace-scan — Detect AI/agentic authorship fingerprints in a codebase.

Scans git history, file names, and source comments for traces left by
AI coding assistants (Copilot, Claude, Cursor, Aider, etc).
"""

import argparse
import os
import re
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

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

BRANCH_PATTERNS = [
    r"^copilot/",
    r"^claude/",
    r"^ai[-/]",
    r"^cursor[-/]",
    r"^aider[-/]",
    r"^gemini[-/]",
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
    ".py", ".js", ".ts", ".jsx", ".tsx", ".vue", ".rb", ".go", ".rs",
    ".java", ".kt", ".scala", ".c", ".cpp", ".h", ".hpp", ".cs",
    ".sh", ".bash", ".zsh", ".fish", ".ps1",
    ".yml", ".yaml", ".toml", ".json", ".xml",
    ".md", ".rst", ".txt", ".adoc",
    ".tf", ".hcl", ".j2", ".jinja2", ".tpl",
    ".sql", ".graphql", ".proto",
    ".css", ".scss", ".less", ".html", ".svelte",
    ".r", ".R", ".lua", ".php", ".pl", ".pm",
    ".swift", ".m", ".mm",
    ".dockerfile", ".containerfile",
    ".cfg", ".ini", ".conf", ".env",
    ".gradle", ".cmake", ".makefile",
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
# Scanners
# ---------------------------------------------------------------------------

def scan_commits(cwd, rev_range, max_commits):
    findings = []
    fmt = "%H%n%s%n%b%n---END---"
    out = git("log", f"--max-count={max_commits}", f"--format={fmt}", rev_range, cwd=cwd)
    if not out:
        return findings

    for block in out.split("---END---"):
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        sha = lines[0][:12]
        body = "\n".join(lines[1:])

        for pattern, label in TRAILER_PATTERNS + COMMIT_MSG_PATTERNS:
            if re.search(pattern, body, re.IGNORECASE):
                subject = lines[1][:60] if len(lines) > 1 else "(no subject)"
                findings.append(Finding(
                    severity="high",
                    category="git-history",
                    location=f"commit {sha} ({subject})",
                    message=label,
                ))
    return findings


def scan_branches(cwd):
    findings = []
    out = git("branch", "-a", "--format=%(refname:short)", cwd=cwd)
    if not out:
        return findings

    for branch in out.strip().split("\n"):
        branch = branch.strip()
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


def scan_config_files(root):
    findings = []
    for name in AGENT_CONFIG_FILES:
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
            rel = match.relative_to(root)
            if str(rel) not in AGENT_CONFIG_FILES:
                findings.append(Finding(
                    severity="medium",
                    category="config-file",
                    location=str(rel),
                    message="AI assistant config file/directory present",
                ))
    return findings


def should_scan_file(path):
    if path.name in TEXT_FILENAMES:
        return True
    return path.suffix.lower() in TEXT_EXTENSIONS


def scan_file_comments(filepath, root):
    findings = []
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for lineno, line in enumerate(f, 1):
                for pattern, label in COMMENT_PATTERNS:
                    if re.search(pattern, line, re.IGNORECASE):
                        rel = filepath.relative_to(root)
                        findings.append(Finding(
                            severity="medium",
                            category="source-comment",
                            location=f"{rel}:{lineno}",
                            message=label,
                        ))
    except (OSError, UnicodeDecodeError):
        pass
    return findings


def scan_source_tree(root):
    findings = []
    skip_self = root == SELF_DIR or SELF_DIR.is_relative_to(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            filepath = Path(dirpath) / name
            if skip_self and filepath.resolve().is_relative_to(SELF_DIR):
                continue
            if should_scan_file(filepath):
                findings.extend(scan_file_comments(filepath, root))
    return findings


def scan_staged(cwd):
    findings = []
    out = git("diff", "--cached", "--unified=0", cwd=cwd)
    if not out:
        return findings

    current_file = None
    for line in out.split("\n"):
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
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


def print_findings(findings, use_color):
    if not findings:
        mark = "\033[92m✓\033[0m" if use_color else "✓"
        print(f"\n  {mark} No AI authorship traces found.\n")
        return

    print(f"\n  Found {len(findings)} finding(s):\n")

    by_category = {}
    for f in findings:
        by_category.setdefault(f.category, []).append(f)

    for cat, items in by_category.items():
        header = cat.replace("-", " ").title()
        if use_color:
            print(f"  {BOLD}{header}{RESET}")
        else:
            print(f"  {header}")

        for item in items:
            sev = item.severity.upper()
            if use_color:
                color = SEVERITY_COLORS.get(item.severity, "")
                print(f"    {color}[{sev}]{RESET} {item.location}")
                print(f"           {item.message}")
            else:
                print(f"    [{sev}] {item.location}")
                print(f"           {item.message}")
        print()


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
    parser.add_argument("--no-color", action="store_true",
                        help="Disable colored output")
    parser.add_argument("--quiet", action="store_true",
                        help="Only print findings, no banner")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    use_color = supports_color() and not args.no_color

    if not root.is_dir():
        print(f"Error: {root} is not a directory", file=sys.stderr)
        sys.exit(2)

    if not args.quiet:
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
        findings.extend(scan_staged(root))
        print_findings(findings, use_color)
        sys.exit(1 if findings else 0)

    if has_git:
        if args.branch:
            default = get_default_branch(root)
            rev_range = f"{default}..{args.branch}" if default else args.branch
        else:
            rev_range = "HEAD"

        findings.extend(scan_commits(root, rev_range, args.commits))
        findings.extend(scan_branches(root))

    findings.extend(scan_config_files(root))
    findings.extend(scan_source_tree(root))

    print_findings(findings, use_color)
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
