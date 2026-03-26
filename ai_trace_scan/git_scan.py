"""Git operations and git-related scanners."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from . import Finding
from .patterns import (
    BOT_AUTHOR_PATTERNS,
    BRANCH_PATTERNS,
    COMMENT_PATTERNS,
    COMMIT_MSG_PATTERNS,
    TRAILER_PATTERNS,
)


def git(*args: str, cwd: str | Path | None = None) -> str | None:
    try:
        r = subprocess.run(
            ["git", "--no-pager", *list(args)],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
        return r.stdout if r.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def is_git_repo(path: str | Path) -> bool:
    return git("rev-parse", "--git-dir", cwd=path) is not None


def get_default_branch(cwd: str | Path) -> str | None:
    for name in ("main", "master"):
        if git("rev-parse", "--verify", name, cwd=cwd) is not None:
            return name
    out = git("symbolic-ref", "refs/remotes/origin/HEAD", cwd=cwd)
    if out:
        return out.strip().split("/")[-1]
    return None


def get_unpushed_range(cwd: str | Path) -> str | None:
    """Return a rev range covering only unpushed commits.

    Tries (in order):
    1. @{upstream}..HEAD — if current branch tracks a remote
    2. origin/<default>..HEAD — if origin exists with main/master
    3. None — no remote baseline found
    """
    # Try upstream tracking ref
    upstream = git("rev-parse", "--abbrev-ref", "@{upstream}", cwd=cwd)
    if upstream and upstream.strip():
        return f"{upstream.strip()}..HEAD"

    # Try origin/<default>
    default = get_default_branch(cwd)
    if default:
        origin_ref = f"origin/{default}"
        if git("rev-parse", "--verify", origin_ref, cwd=cwd) is not None:
            return f"{origin_ref}..HEAD"

    return None


def scan_commits(
    cwd: str | Path,
    rev_range: str,
    max_commits: int,
    exclude_fn: Callable[[str], bool],
) -> list[Finding]:
    findings: list[Finding] = []
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
                findings.append(
                    Finding(
                        severity="high",
                        category="git-history",
                        location=f"commit {sha} ({subject})",
                        message=f"{label}: {author_email}",
                    )
                )

        for pattern, label in TRAILER_PATTERNS + COMMIT_MSG_PATTERNS:
            if re.search(pattern, body, re.IGNORECASE):
                findings.append(
                    Finding(
                        severity="high",
                        category="git-history",
                        location=f"commit {sha} ({subject})",
                        message=label,
                    )
                )
    return findings


def scan_tags(cwd: str | Path, exclude_fn: Callable[[str], bool]) -> list[Finding]:
    findings: list[Finding] = []
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
                findings.append(
                    Finding(
                        severity="high",
                        category="git-tag",
                        location=f"tag {tag}",
                        message=label,
                    )
                )
    return findings


def scan_branches(cwd: str | Path, exclude_fn: Callable[[str], bool]) -> list[Finding]:
    findings: list[Finding] = []
    out = git("branch", "-a", "--format=%(refname:short)", cwd=cwd)
    if not out:
        return findings

    for branch in out.strip().split("\n"):
        branch = branch.strip()
        if exclude_fn(branch):
            continue
        # Strip remote prefix (e.g. "origin/copilot/fix" -> "copilot/fix")
        if branch.startswith("origin/") or branch.startswith("upstream/"):
            short = branch.split("/", 1)[-1]
        else:
            short = branch
        for pattern in BRANCH_PATTERNS:
            if re.search(pattern, short, re.IGNORECASE):
                findings.append(
                    Finding(
                        severity="medium",
                        category="branch-name",
                        location=branch,
                        message=f"Branch name matches AI tool pattern: {pattern}",
                    )
                )
    return findings


def scan_staged(cwd: str | Path, exclude_fn: Callable[[str], bool]) -> list[Finding]:
    findings: list[Finding] = []
    out = git("diff", "--cached", "--unified=0", cwd=cwd)
    if not out:
        return findings

    current_file: str | None = None
    for line in out.split("\n"):
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            if current_file and exclude_fn(current_file):
                continue
            added = line[1:]
            for pattern, label in TRAILER_PATTERNS + COMMENT_PATTERNS:
                if re.search(pattern, added, re.IGNORECASE):
                    findings.append(
                        Finding(
                            severity="high" if "trailer" in label.lower() else "medium",
                            category="staged-change",
                            location=current_file or "(unknown file)",
                            message=label,
                        )
                    )
    return findings
