"""Git history date normalization."""

from __future__ import annotations

import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import Finding


def _git(*args: str, cwd: str | Path | None = None) -> str | None:
    r = subprocess.run(
        ["git", "--no-pager", *list(args)],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=30,
    )
    return r.stdout if r.returncode == 0 else None


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------


def _check_clean_worktree(cwd: str | Path) -> str | None:
    """Ensure no uncommitted changes exist."""
    status = _git("status", "--porcelain", cwd=cwd)
    if status is None:
        return "Unable to read git status"
    if status.strip():
        return "Working tree has uncommitted changes -- commit or stash first"
    return None


def _check_no_operation_in_progress(cwd: str | Path) -> str | None:
    """Ensure no rebase, merge, or cherry-pick is in progress."""
    git_dir = Path(cwd) / ".git"
    for marker in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD"):
        if (git_dir / marker).exists():
            return f"A {marker.replace('_', ' ').lower()} operation is in progress -- finish or abort it first"
    return None


def _check_not_pushed(cwd: str | Path, shas: list[str]) -> str | None:
    """Warn if any commits exist on a remote tracking branch."""
    remotes = _git("remote", cwd=cwd)
    if not remotes or not remotes.strip():
        return None  # no remotes -- safe
    # Check if any of the SHAs are reachable from remote branches
    for sha in shas:
        out = _git("branch", "-r", "--contains", sha, cwd=cwd)
        if out and out.strip():
            branches = ", ".join(b.strip() for b in out.strip().split("\n")[:3])
            return (
                f"Commit {sha[:12]} exists on remote branch(es): {branches}\n"
                "  Rewriting pushed history will cause problems for collaborators.\n"
                "  Use --force to proceed anyway."
            )
    return None


def _create_backup_branch(cwd: str | Path) -> str | None:
    """Create a backup branch before rewriting."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_name = f"backup/fix-dates-{timestamp}"
    result = _git("branch", backup_name, cwd=cwd)
    if result is None:
        return None
    return backup_name


def _collect_tree_shas(cwd: str | Path, rev_range: str) -> dict[str, str]:
    """Collect {commit_sha: tree_sha} for verification after rewrite."""
    out = _git("log", "--format=%H %T", rev_range, cwd=cwd)
    if not out:
        return {}
    trees: dict[str, str] = {}
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        commit_sha, tree_sha = line.split(" ", 1)
        trees[commit_sha] = tree_sha
    return trees


def _verify_trees_preserved(
    cwd: str | Path,
    original_trees: dict[str, str],
    new_shas: list[str],
) -> str | None:
    """Verify that all tree objects are unchanged after rewrite.

    Tree SHAs represent the actual file content of a commit. If dates
    changed but trees are identical, content was preserved.
    """
    new_out = _git("log", "--format=%H %T", "HEAD", cwd=cwd)
    if not new_out:
        return "Unable to read post-rewrite commits"

    new_trees: list[str] = []
    for line in new_out.strip().split("\n"):
        if not line.strip():
            continue
        _, tree_sha = line.split(" ", 1)
        new_trees.append(tree_sha)

    old_trees = list(original_trees.values())

    if sorted(new_trees[: len(old_trees)]) != sorted(old_trees):
        return (
            "WARNING: Tree SHAs changed after rewrite -- file content may have been altered!\n"
            "  Restore from backup branch immediately."
        )
    return None


def preflight_checks(
    cwd: str | Path,
    shas: list[str],
    force: bool = False,
    dry_run: bool = False,
) -> tuple[bool, list[str]]:
    """Run all safety checks before rewriting history.

    Returns (ok, messages) where messages is a list of strings.
    """
    messages: list[str] = []

    # 1. Clean worktree
    err = _check_clean_worktree(cwd)
    if err:
        return False, [err]

    # 2. No git operation in progress
    err = _check_no_operation_in_progress(cwd)
    if err:
        return False, [err]

    # 3. Check for pushed commits (skip in dry-run)
    if not dry_run:
        err = _check_not_pushed(cwd, shas)
        if err:
            if force:
                messages.append(f"WARNING: {err.split(chr(10))[0]} -- proceeding with --force")
            else:
                return False, [err]

    return True, messages


def _detect_clustering(dates: list[datetime], threshold_minutes: float = 5) -> bool:
    """Check if commits are suspiciously clustered in time."""
    if len(dates) < 2:
        return False
    total_span = (dates[-1] - dates[0]).total_seconds() / 60
    avg_gap = total_span / (len(dates) - 1) if len(dates) > 1 else 0
    return avg_gap < threshold_minutes


def scan_dates(
    cwd: str | Path,
    rev_range: str,
    max_commits: int,
    threshold_minutes: float = 5,
) -> list[Finding]:
    """Scan for suspiciously tight commit clustering and future-dated commits."""
    findings: list[Finding] = []
    out = _git("log", f"--max-count={max_commits}", "--format=%H %aI", rev_range, cwd=cwd)
    if not out:
        return findings

    entries: list[tuple[str, datetime]] = []
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        sha, datestr = line.split(" ", 1)
        dt = datetime.fromisoformat(datestr)
        entries.append((sha[:12], dt))

    if not entries:
        return findings

    # Check for future-dated commits
    now = datetime.now(timezone.utc)
    for sha, dt in entries:
        dt_utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt
        if dt_utc > now + timedelta(minutes=5):  # 5min tolerance for clock skew
            delta = dt_utc - now
            hours = delta.total_seconds() / 3600
            findings.append(
                Finding(
                    severity="medium",
                    category="future-date",
                    location=f"commit {sha}",
                    message=f"Commit is dated {hours:.1f}h in the future ({dt.isoformat()})",
                )
            )

    if len(entries) < 2:
        return findings

    entries.reverse()  # oldest first
    dates: list[datetime] = [dt for _, dt in entries]

    if not _detect_clustering(dates, threshold_minutes):
        return findings

    total_minutes = (dates[-1] - dates[0]).total_seconds() / 60
    findings.append(
        Finding(
            severity="medium",
            category="commit-timing",
            location=f"{len(entries)} commits in {total_minutes:.0f} minutes",
            message=f"Commits are clustered within {threshold_minutes}min avg gap (possible automation)",
        )
    )

    for i in range(1, len(entries)):
        gap = (dates[i] - dates[i - 1]).total_seconds()
        if gap < threshold_minutes * 60:
            findings.append(
                Finding(
                    severity="low",
                    category="commit-timing",
                    location=f"commit {entries[i][0]}",
                    message=f"{gap:.0f}s after previous commit",
                )
            )

    return findings


def _skip_weekends(new_dates: dict[str, str]) -> dict[str, str]:
    """Shift any Saturday/Sunday dates to the following Monday, keeping time-of-day."""
    shifted: dict[str, str] = {}
    for sha, datestr in new_dates.items():
        dt = datetime.fromisoformat(datestr)
        weekday = dt.weekday()  # 5 = Saturday, 6 = Sunday
        if weekday == 5:
            dt += timedelta(days=2)
        elif weekday == 6:
            dt += timedelta(days=1)
        shifted[sha] = dt.isoformat()
    return shifted


def fix_dates(
    cwd: str | Path,
    rev_range: str,
    spread_hours: float = 3.0,
    jitter_minutes: float = 15.0,
    burst: tuple[int, float] | None = None,
    dry_run: bool = False,
    force: bool = False,
    anchor: str = "present",
    no_weekends: bool = False,
) -> bool:
    """Rewrite commit timestamps to spread over a realistic time range.

    Args:
        cwd: repository path
        rev_range: git rev range (e.g. "HEAD", "main..feature")
        spread_hours: total time span per work session
        jitter_minutes: random +/- variance per commit
        burst: if set, (sessions, gap_days) tuple -- split commits into N work
               sessions separated by gap_days idle days between them
        dry_run: if True, show what would happen without modifying history
        force: if True, skip confirmation and allow rewriting pushed commits
        anchor: "present" (default) anchors last commit to now and spreads
                backwards; "first-commit" keeps the first commit's date and
                spreads forward (may produce future dates)
        no_weekends: if True, shift any Saturday/Sunday commits to Monday
    """
    out = _git("log", "--format=%H %aI %s", rev_range, cwd=cwd)
    if not out:
        print("  No commits found.", file=sys.stderr)
        return False

    entries: list[tuple[str, datetime, str]] = []
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split(" ", 2)
        sha, datestr = parts[0], parts[1]
        subject = parts[2] if len(parts) > 2 else ""
        dt = datetime.fromisoformat(datestr)
        entries.append((sha, dt, subject))

    if len(entries) < 2:
        print("  Only one commit, nothing to spread.", file=sys.stderr)
        return False

    entries.reverse()  # oldest first
    shas: list[str] = [sha for sha, _, _ in entries]

    # --- Preflight safety checks ---
    ok, messages = preflight_checks(cwd, shas, force=force, dry_run=dry_run)
    for msg in messages:
        print(f"  {msg}", file=sys.stderr)
    if not ok:
        return False

    base_time: datetime = entries[0][1]
    count: int = len(entries)
    new_dates: dict[str, str] = {}

    if burst:
        sessions, gap_days = burst
        # Split commits into roughly equal sessions
        per_session = max(1, count // sessions)
        chunks: list[list[tuple[str, datetime, str]]] = []
        for i in range(0, count, per_session):
            chunks.append(entries[i : i + per_session])
        # Merge trailing remainder into last chunk
        if len(chunks) > sessions:
            chunks[-2].extend(chunks[-1])
            chunks.pop()

        cursor: datetime = base_time
        for ci, chunk in enumerate(chunks):
            if ci > 0:
                # Idle gap between sessions (with some variance)
                gap = timedelta(days=gap_days + random.uniform(-0.5, 0.5))
                cursor = cursor + gap
            # Spread this chunk within one session
            session_len = timedelta(hours=spread_hours)
            interval: timedelta = session_len / max(len(chunk) - 1, 1)

            for j, (sha, _, _subj) in enumerate(chunk):
                target: datetime = cursor + (interval * j)
                if j == 0 and ci == 0:
                    jitter = timedelta(0)
                else:
                    jitter = timedelta(minutes=random.uniform(-jitter_minutes, jitter_minutes))
                new_dates[sha] = (target + jitter).isoformat()

            # Advance cursor past this session
            cursor = cursor + session_len
    else:
        # Single session -- even spread + jitter
        interval = timedelta(hours=spread_hours) / (count - 1) if count > 1 else timedelta()

        for i, (sha, _, _subj) in enumerate(entries):
            target = base_time + (interval * i)
            if i == 0:
                jitter = timedelta(0)
            elif i == count - 1:
                jitter = timedelta(minutes=random.uniform(0, jitter_minutes))
            else:
                jitter = timedelta(minutes=random.uniform(-jitter_minutes, jitter_minutes))
            new_dates[sha] = (target + jitter).isoformat()

    # --- Anchor adjustment ---
    # By default, shift the whole series so the last commit lands at "now"
    if anchor == "present":
        now = datetime.now(timezone.utc).astimezone()
        # Find the latest computed date
        latest = max(datetime.fromisoformat(d) for d in new_dates.values())
        shift = now - latest
        new_dates = {
            sha: (datetime.fromisoformat(d) + shift).isoformat() for sha, d in new_dates.items()
        }

    # --- Weekend avoidance ---
    if no_weekends:
        new_dates = _skip_weekends(new_dates)

    # --- Future date guard ---
    now = datetime.now(timezone.utc)
    future_dates: list[tuple[str, str]] = []
    for sha, datestr in new_dates.items():
        dt = datetime.fromisoformat(datestr)
        dt_utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt
        if dt_utc > now + timedelta(minutes=5):
            future_dates.append((sha[:12], datestr))
    if future_dates:
        print(
            f"  ERROR: {len(future_dates)} commit(s) would land in the future:",
            file=sys.stderr,
        )
        for sha, datestr in future_dates[:5]:
            print(f"    {sha}  {datestr}", file=sys.stderr)
        if no_weekends:
            print(
                "\n  This is likely caused by --no-weekends shifting weekend dates forward.",
                file=sys.stderr,
            )
        print("  Try without --no-weekends, or use --burst with smaller gaps.", file=sys.stderr)
        return False

    if dry_run:
        # Build subject lookup from entries
        subjects: dict[str, str] = {sha: subj for sha, _, subj in entries}
        # Show newest-first like git log
        ordered = sorted(new_dates.items(), key=lambda x: x[1], reverse=True)
        print("\n  [DRY RUN] Rewritten history would look like:\n")
        for sha, datestr in ordered:
            dt = datetime.fromisoformat(datestr)
            ts = dt.strftime("%Y-%m-%d %H:%M")
            day = dt.strftime("%a")
            subj = subjects.get(sha, "")
            print(f"    {sha[:12]}  {day} {ts}  {subj}")
        print()
        return True

    return _rewrite_dates(cwd, new_dates, count, spread_hours, burst, rev_range)


def _rewrite_dates(
    cwd: str | Path,
    new_dates: dict[str, str],
    count: int,
    spread_hours: float,
    burst: tuple[int, float] | None,
    rev_range: str,
) -> bool:
    """Apply new dates via git filter-branch with safety net."""

    # 1. Snapshot tree SHAs for post-rewrite verification
    original_trees: dict[str, str] = _collect_tree_shas(cwd, rev_range)

    # 2. Create backup branch
    backup_name: str | None = _create_backup_branch(cwd)
    if backup_name:
        print(f"  Backup branch created: {backup_name}", file=sys.stderr)
    else:
        print("  WARNING: Could not create backup branch -- proceeding anyway", file=sys.stderr)

    # 3. Build and run filter-branch
    cases: list[str] = []
    for sha, datestr in new_dates.items():
        cases.append(
            f"  {sha})\n"
            f"    export GIT_AUTHOR_DATE='{datestr}'\n"
            f"    export GIT_COMMITTER_DATE='{datestr}'\n"
            f"    ;;"
        )
    case_block: str = "\n".join(cases)
    env_filter: str = f'case "$GIT_COMMIT" in\n{case_block}\nesac'

    result = subprocess.run(
        ["git", "filter-branch", "-f", "--env-filter", env_filter, "--", "--all"],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=120,
    )

    if result.returncode != 0:
        print(f"  filter-branch failed: {result.stderr}", file=sys.stderr)
        if backup_name:
            print(f"  Restore with: git reset --hard {backup_name}", file=sys.stderr)
        return False

    # 4. Verify tree SHAs match (content unchanged)
    new_shas: list[str] = list(new_dates.keys())
    err = _verify_trees_preserved(cwd, original_trees, new_shas)
    if err:
        print(f"  {err}", file=sys.stderr)
        if backup_name:
            print(f"  Restore with: git reset --hard {backup_name}", file=sys.stderr)
        return False

    # 5. Clean up refs/original (filter-branch backup -- we have our own backup branch)
    subprocess.run(
        ["rm", "-rf", f"{cwd}/.git/refs/original"],
        capture_output=True,
        cwd=cwd,
    )

    # 6. Show results
    out = _git("log", "--format=%h  %aI  %s", cwd=cwd)
    if out:
        if burst:
            sessions, gap = burst
            print(
                f"\n  Rewrote {count} commits across {sessions} sessions (~{spread_hours}h each, ~{gap}d gaps):\n"
            )
        else:
            print(f"\n  Rewrote {count} commits over ~{spread_hours}h:\n")
        for line in out.strip().split("\n"):
            print(f"    {line}")
        print()

    if backup_name:
        print(f"  To undo: git reset --hard {backup_name}", file=sys.stderr)
        print(f"  To clean up backup: git branch -D {backup_name}\n", file=sys.stderr)

    return True
