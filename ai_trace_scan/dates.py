"""Git history date normalization."""

import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone


def _git(*args, cwd=None):
    r = subprocess.run(
        ["git", "--no-pager"] + list(args),
        capture_output=True, text=True, cwd=cwd, timeout=30,
    )
    return r.stdout if r.returncode == 0 else None


def _detect_clustering(dates, threshold_minutes=5):
    """Check if commits are suspiciously clustered in time."""
    if len(dates) < 2:
        return False
    total_span = (dates[-1] - dates[0]).total_seconds() / 60
    avg_gap = total_span / (len(dates) - 1) if len(dates) > 1 else 0
    return avg_gap < threshold_minutes


def scan_dates(cwd, rev_range, max_commits, threshold_minutes=5):
    """Scan for suspiciously tight commit clustering."""
    from . import Finding

    findings = []
    out = _git("log", f"--max-count={max_commits}", "--format=%H %aI", rev_range, cwd=cwd)
    if not out:
        return findings

    entries = []
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        sha, datestr = line.split(" ", 1)
        dt = datetime.fromisoformat(datestr)
        entries.append((sha[:12], dt))

    if len(entries) < 2:
        return findings

    entries.reverse()  # oldest first
    dates = [dt for _, dt in entries]

    if not _detect_clustering(dates, threshold_minutes):
        return findings

    total_minutes = (dates[-1] - dates[0]).total_seconds() / 60
    findings.append(Finding(
        severity="medium",
        category="commit-timing",
        location=f"{len(entries)} commits in {total_minutes:.0f} minutes",
        message=f"Commits are clustered within {threshold_minutes}min avg gap (possible automation)",
    ))

    for i in range(1, len(entries)):
        gap = (dates[i] - dates[i - 1]).total_seconds()
        if gap < threshold_minutes * 60:
            findings.append(Finding(
                severity="low",
                category="commit-timing",
                location=f"commit {entries[i][0]}",
                message=f"{gap:.0f}s after previous commit",
            ))

    return findings


def fix_dates(cwd, rev_range, spread_hours=3.0, jitter_minutes=15.0):
    """Rewrite commit timestamps to spread over a realistic time range.

    Args:
        cwd: repository path
        rev_range: git rev range (e.g. "HEAD", "main..feature")
        spread_hours: total time span to distribute commits over
        jitter_minutes: random +/- variance per commit to avoid uniform spacing
    """
    out = _git("log", "--format=%H %aI", rev_range, cwd=cwd)
    if not out:
        print("  No commits found.", file=sys.stderr)
        return False

    entries = []
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        sha, datestr = line.split(" ", 1)
        dt = datetime.fromisoformat(datestr)
        entries.append((sha, dt))

    if len(entries) < 2:
        print("  Only one commit, nothing to spread.", file=sys.stderr)
        return False

    entries.reverse()  # oldest first
    base_time = entries[0][1]
    count = len(entries)

    # Calculate even intervals + jitter
    interval = timedelta(hours=spread_hours) / (count - 1) if count > 1 else timedelta()
    new_dates = {}

    for i, (sha, _) in enumerate(entries):
        target = base_time + (interval * i)
        jitter = timedelta(minutes=random.uniform(-jitter_minutes, jitter_minutes))
        # Don't jitter first or last commit as much
        if i == 0:
            jitter = timedelta(0)
        elif i == count - 1:
            jitter = timedelta(minutes=random.uniform(0, jitter_minutes))
        new_date = target + jitter
        new_dates[sha] = new_date.isoformat()

    # Build the env-filter case statement
    cases = []
    for sha, datestr in new_dates.items():
        cases.append(
            f"  {sha})\n"
            f"    export GIT_AUTHOR_DATE='{datestr}'\n"
            f"    export GIT_COMMITTER_DATE='{datestr}'\n"
            f"    ;;"
        )
    case_block = "\n".join(cases)

    env_filter = f'case "$GIT_COMMIT" in\n{case_block}\nesac'

    result = subprocess.run(
        ["git", "filter-branch", "-f", "--env-filter", env_filter, "--", "--all"],
        capture_output=True, text=True, cwd=cwd, timeout=120,
    )

    if result.returncode != 0:
        print(f"  filter-branch failed: {result.stderr}", file=sys.stderr)
        return False

    # Cleanup backup refs
    subprocess.run(
        ["rm", "-rf", f"{cwd}/.git/refs/original"],
        capture_output=True, cwd=cwd,
    )

    # Show results
    out = _git("log", "--format=%h  %aI  %s", cwd=cwd)
    if out:
        print(f"\n  Rewrote {count} commits over ~{spread_hours}h:\n")
        for line in out.strip().split("\n"):
            print(f"    {line}")
        print()

    return True
