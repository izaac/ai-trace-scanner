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


# ---------------------------------------------------------------------------
# Safety checks
# ---------------------------------------------------------------------------

def _check_clean_worktree(cwd):
    """Ensure no uncommitted changes exist."""
    status = _git("status", "--porcelain", cwd=cwd)
    if status is None:
        return "Unable to read git status"
    if status.strip():
        return "Working tree has uncommitted changes — commit or stash first"
    return None


def _check_no_operation_in_progress(cwd):
    """Ensure no rebase, merge, or cherry-pick is in progress."""
    from pathlib import Path
    git_dir = Path(cwd) / ".git"
    for marker in ("rebase-merge", "rebase-apply", "MERGE_HEAD", "CHERRY_PICK_HEAD"):
        if (git_dir / marker).exists():
            return f"A {marker.replace('_', ' ').lower()} operation is in progress — finish or abort it first"
    return None


def _check_not_pushed(cwd, shas):
    """Warn if any commits exist on a remote tracking branch."""
    remotes = _git("remote", cwd=cwd)
    if not remotes or not remotes.strip():
        return None  # no remotes — safe
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


def _create_backup_branch(cwd):
    """Create a backup branch before rewriting."""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_name = f"backup/fix-dates-{timestamp}"
    result = _git("branch", backup_name, cwd=cwd)
    if result is None:
        return None
    return backup_name


def _collect_tree_shas(cwd, rev_range):
    """Collect {commit_sha: tree_sha} for verification after rewrite."""
    out = _git("log", "--format=%H %T", rev_range, cwd=cwd)
    if not out:
        return {}
    trees = {}
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        commit_sha, tree_sha = line.split(" ", 1)
        trees[commit_sha] = tree_sha
    return trees


def _verify_trees_preserved(cwd, original_trees, new_shas):
    """Verify that all tree objects are unchanged after rewrite.

    Tree SHAs represent the actual file content of a commit. If dates
    changed but trees are identical, content was preserved.
    """
    new_out = _git("log", "--format=%H %T", "HEAD", cwd=cwd)
    if not new_out:
        return "Unable to read post-rewrite commits"

    new_trees = []
    for line in new_out.strip().split("\n"):
        if not line.strip():
            continue
        _, tree_sha = line.split(" ", 1)
        new_trees.append(tree_sha)

    old_trees = list(original_trees.values())

    if sorted(new_trees[:len(old_trees)]) != sorted(old_trees):
        return (
            "WARNING: Tree SHAs changed after rewrite — file content may have been altered!\n"
            "  Restore from backup branch immediately."
        )
    return None


def preflight_checks(cwd, shas, force=False, dry_run=False):
    """Run all safety checks before rewriting history.

    Returns (ok, messages) where messages is a list of strings.
    """
    messages = []

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
                messages.append(f"WARNING: {err.split(chr(10))[0]} — proceeding with --force")
            else:
                return False, [err]

    return True, messages


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


def fix_dates(cwd, rev_range, spread_hours=3.0, jitter_minutes=15.0, burst=None,
              dry_run=False, force=False, anchor="present"):
    """Rewrite commit timestamps to spread over a realistic time range.

    Args:
        cwd: repository path
        rev_range: git rev range (e.g. "HEAD", "main..feature")
        spread_hours: total time span per work session
        jitter_minutes: random +/- variance per commit
        burst: if set, (sessions, gap_days) tuple — split commits into N work
               sessions separated by gap_days idle days between them
        dry_run: if True, show what would happen without modifying history
        force: if True, skip confirmation and allow rewriting pushed commits
        anchor: "present" (default) anchors last commit to now and spreads
                backwards; "first-commit" keeps the first commit's date and
                spreads forward (may produce future dates)
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
    shas = [sha for sha, _ in entries]

    # --- Preflight safety checks ---
    ok, messages = preflight_checks(cwd, shas, force=force, dry_run=dry_run)
    for msg in messages:
        print(f"  {msg}", file=sys.stderr)
    if not ok:
        return False

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

    # --- Anchor adjustment ---
    # By default, shift the whole series so the last commit lands at "now"
    if anchor == "present":
        now = datetime.now(timezone.utc).astimezone()
        # Find the latest computed date
        latest = max(datetime.fromisoformat(d) for d in new_dates.values())
        shift = now - latest
        new_dates = {
            sha: (datetime.fromisoformat(d) + shift).isoformat()
            for sha, d in new_dates.items()
        }

    if dry_run:
        print(f"\n  [DRY RUN] Would rewrite {count} commits:\n")
        for sha, datestr in new_dates.items():
            original = next(dt for s, dt in entries if s == sha)
            print(f"    {sha[:12]}  {original.isoformat()}  ->  {datestr}")
        print()
        return True

    return _rewrite_dates(cwd, new_dates, count, spread_hours, burst, rev_range)


def _rewrite_dates(cwd, new_dates, count, spread_hours, burst, rev_range):
    """Apply new dates via git filter-branch with safety net."""

    # 1. Snapshot tree SHAs for post-rewrite verification
    original_trees = _collect_tree_shas(cwd, rev_range)

    # 2. Create backup branch
    backup_name = _create_backup_branch(cwd)
    if backup_name:
        print(f"  Backup branch created: {backup_name}", file=sys.stderr)
    else:
        print("  WARNING: Could not create backup branch — proceeding anyway", file=sys.stderr)

    # 3. Build and run filter-branch
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
        if backup_name:
            print(f"  Restore with: git reset --hard {backup_name}", file=sys.stderr)
        return False

    # 4. Verify tree SHAs match (content unchanged)
    new_shas = list(new_dates.keys())
    err = _verify_trees_preserved(cwd, original_trees, new_shas)
    if err:
        print(f"  {err}", file=sys.stderr)
        if backup_name:
            print(f"  Restore with: git reset --hard {backup_name}", file=sys.stderr)
        return False

    # 5. Clean up refs/original (filter-branch backup — we have our own backup branch)
    subprocess.run(
        ["rm", "-rf", f"{cwd}/.git/refs/original"],
        capture_output=True, cwd=cwd,
    )

    # 6. Show results
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

    if backup_name:
        print(f"  To undo: git reset --hard {backup_name}", file=sys.stderr)
        print(f"  To clean up backup: git branch -D {backup_name}\n", file=sys.stderr)

    return True
