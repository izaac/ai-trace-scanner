"""Source tree and file comment scanners."""

import os
import re
from pathlib import Path

import pathspec
from pygments import lexers
from pygments.token import Token

from . import Finding
from .patterns import AGENT_CONFIG_FILES, AGENT_CONFIG_GLOBS, COMMENT_PATTERNS

TEXT_EXTENSIONS = {".md", ".rst", ".txt", ".adoc"}

TEXT_FILENAMES = {
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

SKIP_DIRS = {
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

SELF_DIR = Path(__file__).resolve().parent


def _is_plain_text(path):
    if path.name in TEXT_FILENAMES:
        return True
    return path.suffix.lower() in TEXT_EXTENSIONS


def _get_lexer(filepath):
    try:
        lexer = lexers.get_lexer_for_filename(filepath.name)
        # TextLexer doesn't parse comments — treat as plain text
        if lexer.__class__.__name__ == "TextLexer":
            return None
        return lexer
    except lexers.ClassNotFound:
        return None


def _extract_comments(filepath):
    try:
        source = filepath.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return

    lexer = _get_lexer(filepath)
    if not lexer:
        return

    lineno = 1
    for ttype, value in lexer.get_tokens(source):
        line_start = lineno
        lineno += value.count("\n")

        if (
            ttype in Token.Comment
            or ttype in Token.Comment.Single
            or ttype in Token.Comment.Multiline
            or ttype in Token.Comment.Special
            or ttype is Token.Comment.Hashbang
            or str(ttype).startswith("Token.Comment")
        ):
            yield line_start, value


def _scan_file(filepath, root):
    findings = []
    rel = filepath.relative_to(root)
    lexer = _get_lexer(filepath)

    if lexer:
        for lineno, comment_text in _extract_comments(filepath):
            for pattern, label in COMMENT_PATTERNS:
                if re.search(pattern, comment_text, re.IGNORECASE):
                    findings.append(
                        Finding(
                            severity="medium",
                            category="source-comment",
                            location=f"{rel}:{lineno}",
                            message=label,
                        )
                    )
    elif _is_plain_text(filepath):
        try:
            with open(filepath, encoding="utf-8", errors="ignore") as f:
                for lineno, line in enumerate(f, 1):
                    for pattern, label in COMMENT_PATTERNS:
                        if re.search(pattern, line, re.IGNORECASE):
                            findings.append(
                                Finding(
                                    severity="medium",
                                    category="source-comment",
                                    location=f"{rel}:{lineno}",
                                    message=label,
                                )
                            )
        except OSError:
            pass

    return findings


def _load_gitignore(root):
    gitignore_path = root / ".gitignore"
    if not gitignore_path.is_file():
        return None
    try:
        lines = gitignore_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return pathspec.PathSpec.from_lines("gitignore", lines)
    except OSError:
        return None


def scan_config_files(root, exclude_fn):
    findings = []
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


def scan_source_tree(root, exclude_fn):
    findings = []
    skip_self = root == SELF_DIR or SELF_DIR.is_relative_to(root)
    ignore_spec = _load_gitignore(root)

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

            if _get_lexer(filepath) or _is_plain_text(filepath):
                findings.extend(_scan_file(filepath, root))

    return findings
