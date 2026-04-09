"""Source tree and file comment scanners."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Generator
from pathlib import Path

import pathspec
from pygments import lexers
from pygments.lexer import Lexer

from . import Finding
from .patterns import (
    AGENT_CONFIG_FILES,
    AGENT_CONFIG_GLOBS,
    COMMENT_PATTERNS,
    PROSE_PATTERNS,
    WORKFLOW_PATTERNS,
    CompiledPatterns,
)

TEXT_EXTENSIONS: set[str] = {".md", ".rst", ".txt", ".adoc"}

TEXT_FILENAMES: set[str] = {
    "Makefile",
    "Dockerfile",
    "Containerfile",
    "Jenkinsfile",
    "Vagrantfile",
    "Rakefile",
    "Gemfile",
    "Procfile",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
}

SKIP_DIRS: set[str] = {
    ".git",
    "node_modules",
    "vendor",
    "venv",
    ".venv",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".terraform",
    ".tofu",
    "target",
    "bin",
    "obj",
}

SELF_DIR: Path = Path(__file__).resolve().parent

# Skip files larger than 1 MB to avoid OOM on generated/minified files
_MAX_FILE_SIZE: int = 1_048_576


def _is_plain_text(path: Path) -> bool:
    if path.name in TEXT_FILENAMES:
        return True
    return path.suffix.lower() in TEXT_EXTENSIONS


def _get_lexer(filepath: Path) -> Lexer | None:
    try:
        lexer = lexers.get_lexer_for_filename(filepath.name)
        # TextLexer doesn't parse comments -- treat as plain text
        if lexer.__class__.__name__ == "TextLexer":
            return None
        return lexer
    except lexers.ClassNotFound:
        return None


def _extract_comments(
    filepath: Path, lexer: Lexer | None = None
) -> Generator[tuple[int, str], None, None]:
    try:
        if filepath.stat().st_size > _MAX_FILE_SIZE:
            return
        source = filepath.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return

    if lexer is None:
        lexer = _get_lexer(filepath)
    if not lexer:
        return

    lineno: int = 1
    for ttype, value in lexer.get_tokens(source):
        line_start = lineno
        lineno += value.count("\n")

        if str(ttype).startswith("Token.Comment"):
            yield line_start, value


def _match_patterns(
    line: str,
    patterns: CompiledPatterns,
    category: str,
    location: str,
) -> list[Finding]:
    """Check a line against a list of compiled regex patterns, returning any matches."""
    return [
        Finding(severity="medium", category=category, location=location, message=label)
        for pattern, label in patterns
        if pattern.search(line)
    ]


def _scan_file(filepath: Path, root: Path, lexer: Lexer | None = None) -> list[Finding]:
    findings: list[Finding] = []
    rel = str(filepath.relative_to(root))

    if _is_plain_text(filepath):
        try:
            if filepath.stat().st_size > _MAX_FILE_SIZE:
                return findings
            with open(filepath, encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, 1):
                    loc = f"{rel}:{lineno}"
                    findings.extend(_match_patterns(line, COMMENT_PATTERNS, "source-comment", loc))
                    findings.extend(_match_patterns(line, PROSE_PATTERNS, "prose-content", loc))
        except OSError:
            pass
    elif lexer is not None or _get_lexer(filepath):
        for lineno, comment_text in _extract_comments(filepath, lexer):
            loc = f"{rel}:{lineno}"
            findings.extend(_match_patterns(comment_text, COMMENT_PATTERNS, "source-comment", loc))

    return findings


def _git_visible_files(root: Path) -> set[str] | None:
    """Return files visible to git (tracked + untracked-not-ignored).

    Returns *None* if *root* is not inside a git repository so the caller
    can fall back to the root-only ``.gitignore`` heuristic.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        return set(result.stdout.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        return None


def _load_gitignore(root: Path) -> pathspec.PathSpec | None:
    gitignore_path = root / ".gitignore"
    if not gitignore_path.is_file():
        return None
    try:
        lines = gitignore_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return pathspec.PathSpec.from_lines("gitignore", lines)
    except OSError:
        return None


def scan_config_files(root: Path, exclude_fn: Callable[[str], bool]) -> list[Finding]:
    findings: list[Finding] = []
    for name in AGENT_CONFIG_FILES:
        if exclude_fn(name):
            continue
        path = root / name
        if path.exists():
            findings.append(
                Finding(
                    severity="high" if name.endswith(".md") else "medium",
                    category="config-file",
                    location=str(name),
                    message="AI assistant config file present",
                )
            )

    for pattern in AGENT_CONFIG_GLOBS:
        for match in root.glob(pattern):
            rel = str(match.relative_to(root))
            if rel not in AGENT_CONFIG_FILES and not exclude_fn(rel):
                findings.append(
                    Finding(
                        severity="medium",
                        category="config-file",
                        location=rel,
                        message="AI assistant config file/directory present",
                    )
                )
    return findings


def scan_workflows(root: Path, exclude_fn: Callable[[str], bool]) -> list[Finding]:
    """Scan .github/workflows/ YAML files for AI tool invocations."""
    findings: list[Finding] = []
    workflows_dir = root / ".github" / "workflows"
    if not workflows_dir.is_dir():
        return findings

    git_files = _git_visible_files(root)
    ignore_spec = _load_gitignore(root) if git_files is None else None

    for filepath in sorted(workflows_dir.rglob("*.y*ml")):
        if filepath.suffix.lower() not in (".yml", ".yaml"):
            continue

        rel_str = str(filepath.relative_to(root))
        if git_files is not None:
            if rel_str not in git_files:
                continue
        elif ignore_spec and ignore_spec.match_file(rel_str):
            continue
        if exclude_fn(rel_str):
            continue

        try:
            with open(filepath, encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, 1):
                    findings.extend(
                        _match_patterns(line, WORKFLOW_PATTERNS, "workflow", f"{rel_str}:{lineno}")
                    )
        except OSError:
            pass

    return findings


def scan_source_tree(root: Path, exclude_fn: Callable[[str], bool]) -> list[Finding]:
    findings: list[Finding] = []
    skip_self: bool = root == SELF_DIR or SELF_DIR.is_relative_to(root)
    git_files = _git_visible_files(root)
    ignore_spec = _load_gitignore(root) if git_files is None else None

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            filepath = Path(dirpath) / name
            if skip_self and filepath.resolve().is_relative_to(SELF_DIR):
                continue

            rel_str = str(filepath.relative_to(root))
            if git_files is not None:
                if rel_str not in git_files:
                    continue
            elif ignore_spec and ignore_spec.match_file(rel_str):
                continue
            if exclude_fn(rel_str):
                continue

            lexer = _get_lexer(filepath)
            if lexer or _is_plain_text(filepath):
                findings.extend(_scan_file(filepath, root, lexer))

    return findings
