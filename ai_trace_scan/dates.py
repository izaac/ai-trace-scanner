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


def fix_dates(cwd, rev_range, spread_hours=3.0, jitter_minutes=15.0, burst=None):
    """Rewrite commit timestamps to spread over a realistic time range.

    Args:
        cwd: repository path
        rev_range: git rev range (e.g. "HEAD", "main..feature")
        spread_hours: total time span per work session
        jitter_minutes: random +/- variance per commit
        burst: if set, "sessions,gap_days" — split commits into N work
               sessions separated by gap_days idle days between them
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
    new_dates = {}

    if burst:
        sessions, gap_days = burst
        # Split commits into roughly equal sessions
        per_session = max(1, count // sessions)
        chunks = []
        for i in range(0, count, per_session):
            chunks.append(entries[i:i + per_session])
        # Merge trailing remainder into last chunk
        if len(chunks) > sessions:
            chunks[-2].extend(chunks[-1])
            chunks.pop()

        cursor = base_time
        for ci, chunk in enumerate(chunks):
            if ci > 0:
                # Idle gap between sessions (with some variance)
                gap = timedelta(days=gap_days + random.uniform(-0.5, 0.5))
                cursor = cursor + gap
            # Spread this chunk within one session
            session_len = timedelta(hours=spread_hours)
            interval = session_len / max(len(chunk) - 1, 1)

            for j, (sha, _) in enumerate(chunk):
                target = cursor + (interval * j)
                if j == 0 and ci == 0:
                    jitter = timedelta(0)
                else:
                    jitter = timedelta(minutes=random.uniform(-jitter_minutes, jitter_minutes))
                new_dates[sha] = (target + jitter).isoformat()

            # Advance cursor past this session
            cursor = cursor + session_len
    else:
        # Single session — even spread + jitter
        interval = timedelta(hours=spread_hours) / (count - 1) if count > 1 else timedelta()

        for i, (sha, _) in enumerate(entries):
            target = base_time + (interval * i)
            if i == 0:
                jitter = timedelta(0)
            elif i == count - 1:
                jitter = timedelta(minutes=random.uniform(0, jitter_minutes))
            else:
                jitter = timedelta(minutes=random.uniform(-jitter_minutes, jitter_minutes))
            new_dates[sha] = (target + jitter).isoformat()

    return _rewrite_dates(cwd, new_dates, count, spread_hours, burst)


def _rewrite_dates(cwd, new_dates, count, spread_hours, burst):
    """Apply new dates via git filter-branch."""
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

    subprocess.run(
        ["rm", "-rf", f"{cwd}/.git/refs/original"],
        capture_output=True, cwd=cwd,
    )

    out = _git("log", "--format=%h  %aI  %s", cwd=cwd)
    if out:
        if burst:
            sessions, gap = burst
            print(f"\n  Rewrote {count} commits across {sessions} sessions (~{spread_hours}h each, ~{gap}d gaps):\n")
        else:
            print(f"\n  Rewrote {count} commits over ~{spread_hours}h:\n")
        for line in out.strip().split("\n"):
            print(f"    {line}")
        print()

    return True
