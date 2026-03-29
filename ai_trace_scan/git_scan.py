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
    DIFF_PATTERNS,
    TRAILER_PATTERNS,
)


def git(*args: str, cwd: str | Path | None = None) -> str | None:
    try:
        r = subprocess.run(
            ["git", "--no-pager", *args],
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


def _match_any(
    text: str,
    patterns: list[tuple[str, str]],
    severity: str,
    category: str,
    location: str,
) -> list[Finding]:
    """Match text against a list of (regex, label) patterns."""
    return [
        Finding(severity=severity, category=category, location=location, message=label)
        for pattern, label in patterns
        if re.search(pattern, text, re.IGNORECASE)
    ]


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

        loc = f"commit {sha} ({subject})"
        for pattern, label in BOT_AUTHOR_PATTERNS:
            if re.search(pattern, author_email, re.IGNORECASE):
                findings.append(
                    Finding(
                        severity="high",
                        category="git-history",
                        location=loc,
                        message=f"{label}: {author_email}",
                    )
                )
        findings.extend(
            _match_any(body, TRAILER_PATTERNS + COMMIT_MSG_PATTERNS, "high", "git-history", loc)
        )
    return findings


def scan_commit_diffs(
    cwd: str | Path,
    rev_range: str,
    max_commits: int,
    exclude_fn: Callable[[str], bool],
) -> list[Finding]:
    """Scan actual code diffs in commits for AI traces in added lines."""
    findings: list[Finding] = []
    out = git(
        "log",
        f"--max-count={max_commits}",
        "-p",
        "--diff-filter=AM",
        rev_range,
        cwd=cwd,
    )
    if not out:
        return findings

    all_patterns = COMMENT_PATTERNS + DIFF_PATTERNS
    current_sha: str = ""
    current_file: str | None = None
    diff_line_no: int = 0

    for line in out.split("\n"):
        if line.startswith("commit "):
            current_sha = line.split()[1][:12]
            current_file = None
            diff_line_no = 0
        elif line.startswith("diff --git"):
            current_file = None
            diff_line_no = 0
        elif line.startswith("+++ b/"):
            current_file = line[6:]
            diff_line_no = 0
        elif line.startswith("@@ "):
            # Parse hunk header for approximate line number
            # Format: @@ -old,count +new,count @@
            match = re.search(r"\+(\d+)", line)
            diff_line_no = int(match.group(1)) if match else 0
        elif line.startswith("+") and not line.startswith("+++"):
            if current_file and exclude_fn(current_file):
                diff_line_no += 1
                continue
            added = line[1:]
            loc = f"commit {current_sha}"
            if current_file:
                loc += f" ({current_file}:{diff_line_no})"
            findings.extend(_match_any(added, all_patterns, "medium", "commit-diff", loc))
            diff_line_no += 1
        elif not line.startswith("-"):
            # Context lines also advance the line counter
            diff_line_no += 1

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
        findings.extend(
            _match_any(msg, TRAILER_PATTERNS + COMMIT_MSG_PATTERNS, "high", "git-tag", f"tag {tag}")
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
            loc = current_file or "(unknown file)"
            for pattern, label in TRAILER_PATTERNS + COMMENT_PATTERNS:
                if re.search(pattern, added, re.IGNORECASE):
                    severity = "high" if "trailer" in label.lower() else "medium"
                    findings.append(
                        Finding(
                            severity=severity, category="staged-change", location=loc, message=label
                        )
                    )
    return findings
