"""Tests for commit date analysis and fix."""

import os
import subprocess
import pytest
from datetime import datetime, timedelta, timezone

from ai_trace_scan.dates import (
    _detect_clustering,
    _check_clean_worktree,
    _check_no_operation_in_progress,
    _check_not_pushed,
    _create_backup_branch,
    _collect_tree_shas,
    _verify_trees_preserved,
    preflight_checks,
    scan_dates,
    fix_dates,
)


def _git_run(*args, cwd, env_extra=None):
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@test.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@test.com"}
    if env_extra:
        env.update(env_extra)
    subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, check=True, env=env)


def _make_clustered_repo(tmp_path, count=5, gap_seconds=60):
    """Create a repo with tightly clustered commits."""
    _git_run("init", cwd=tmp_path)
    _git_run("config", "user.email", "test@test.com", cwd=tmp_path)
    _git_run("config", "user.name", "Test", cwd=tmp_path)

    base = datetime(2026, 3, 29, 10, 0, 0)
    for i in range(count):
        dt = base + timedelta(seconds=gap_seconds * i)
        datestr = dt.strftime("%Y-%m-%dT%H:%M:%S-07:00")
        (tmp_path / f"file{i}.txt").write_text(str(i))
        _git_run("add", ".", cwd=tmp_path)
        _git_run("commit", "-m", f"commit {i}",
                 cwd=tmp_path,
                 env_extra={"GIT_AUTHOR_DATE": datestr, "GIT_COMMITTER_DATE": datestr})
    return tmp_path


@pytest.fixture
def clustered_repo(tmp_path):
    return _make_clustered_repo(tmp_path)


@pytest.fixture
def spread_repo(tmp_path):
    return _make_clustered_repo(tmp_path, gap_seconds=3600)


class TestDetectClustering:
    def test_clustered_dates(self):
        base = datetime(2026, 1, 1, 10, 0)
        dates = [base + timedelta(minutes=i) for i in range(5)]
        assert _detect_clustering(dates, threshold_minutes=5) is True

    def test_spread_dates(self):
        base = datetime(2026, 1, 1, 10, 0)
        dates = [base + timedelta(hours=i) for i in range(5)]
        assert _detect_clustering(dates, threshold_minutes=5) is False

    def test_single_date(self):
        assert _detect_clustering([datetime.now()]) is False

    def test_two_dates_close(self):
        base = datetime(2026, 1, 1, 10, 0)
        dates = [base, base + timedelta(minutes=2)]
        assert _detect_clustering(dates, threshold_minutes=5) is True

    def test_two_dates_far(self):
        base = datetime(2026, 1, 1, 10, 0)
        dates = [base, base + timedelta(hours=1)]
        assert _detect_clustering(dates, threshold_minutes=5) is False


class TestScanDates:
    def test_detects_clustered_commits(self, clustered_repo):
        findings = scan_dates(clustered_repo, "HEAD", 50, threshold_minutes=5)
        assert len(findings) > 0
        assert any(f.category == "commit-timing" for f in findings)

    def test_no_findings_for_spread_commits(self, spread_repo):
        findings = scan_dates(spread_repo, "HEAD", 50, threshold_minutes=5)
        assert len(findings) == 0

    def test_reports_per_commit_gaps(self, clustered_repo):
        findings = scan_dates(clustered_repo, "HEAD", 50, threshold_minutes=5)
        low_findings = [f for f in findings if f.severity == "low"]
        assert len(low_findings) > 0
        assert all("after previous commit" in f.message for f in low_findings)


class TestFixDates:
    def test_spreads_commits(self, clustered_repo):
        result = fix_dates(clustered_repo, "HEAD", spread_hours=3.0, jitter_minutes=0)
        assert result is True

        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%aI"],
            cwd=clustered_repo, capture_output=True, text=True,
        ).stdout
        dates = [datetime.fromisoformat(d) for d in out.strip().split("\n")]
        dates.reverse()
        total_span = (dates[-1] - dates[0]).total_seconds() / 3600
        assert total_span >= 2.5  # ~3 hours with some tolerance

    def test_preserves_first_commit_date_with_first_commit_anchor(self, clustered_repo):
        # Get original first commit date
        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%aI", "--reverse"],
            cwd=clustered_repo, capture_output=True, text=True,
        ).stdout
        original_first = out.strip().split("\n")[0]

        fix_dates(clustered_repo, "HEAD", spread_hours=3.0, jitter_minutes=0,
                  anchor="first-commit")

        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%aI", "--reverse"],
            cwd=clustered_repo, capture_output=True, text=True,
        ).stdout
        new_first = out.strip().split("\n")[0]
        assert new_first == original_first

    def test_present_anchor_no_future_dates(self, clustered_repo):
        fix_dates(clustered_repo, "HEAD", spread_hours=3.0, jitter_minutes=0,
                  anchor="present")

        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%aI"],
            cwd=clustered_repo, capture_output=True, text=True,
        ).stdout
        now = datetime.now(tz=timezone.utc)
        for line in out.strip().split("\n"):
            dt = datetime.fromisoformat(line)
            assert dt <= now + timedelta(minutes=1)  # small tolerance

    def test_burst_mode(self, tmp_path):
        repo = _make_clustered_repo(tmp_path, count=6, gap_seconds=60)
        result = fix_dates(repo, "HEAD", spread_hours=2.0, jitter_minutes=0,
                           burst=(2, 3))
        assert result is True

        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%aI", "--reverse"],
            cwd=repo, capture_output=True, text=True,
        ).stdout
        dates = [datetime.fromisoformat(d) for d in out.strip().split("\n")]

        # There should be a multi-day gap between sessions
        # Session 1: commits 0-2, Session 2: commits 3-5
        gap = (dates[3] - dates[2]).total_seconds() / 86400
        assert gap >= 2.0  # At least 2 days gap

    def test_returns_false_for_single_commit(self, tmp_path):
        _git_run("init", cwd=tmp_path)
        _git_run("config", "user.email", "test@test.com", cwd=tmp_path)
        _git_run("config", "user.name", "Test", cwd=tmp_path)
        (tmp_path / "file.txt").write_text("init")
        _git_run("add", ".", cwd=tmp_path)
        _git_run("commit", "-m", "only commit", cwd=tmp_path)
        result = fix_dates(tmp_path, "HEAD", spread_hours=3.0)
        assert result is False

    def test_dry_run_does_not_modify_history(self, clustered_repo):
        # Get original SHAs
        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%H"],
            cwd=clustered_repo, capture_output=True, text=True,
        ).stdout.strip()
        original_shas = out.split("\n")

        result = fix_dates(clustered_repo, "HEAD", spread_hours=3.0,
                           jitter_minutes=0, dry_run=True)
        assert result is True

        # SHAs must be unchanged
        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%H"],
            cwd=clustered_repo, capture_output=True, text=True,
        ).stdout.strip()
        assert out.split("\n") == original_shas


class TestSafetyChecks:
    """Tests for paranoid safety checks."""

    def test_clean_worktree_passes(self, clustered_repo):
        err = _check_clean_worktree(clustered_repo)
        assert err is None

    def test_dirty_worktree_fails(self, clustered_repo):
        (clustered_repo / "dirty.txt").write_text("uncommitted")
        err = _check_clean_worktree(clustered_repo)
        assert err is not None
        assert "uncommitted" in err.lower()

    def test_staged_changes_fail(self, clustered_repo):
        (clustered_repo / "staged.txt").write_text("staged")
        _git_run("add", "staged.txt", cwd=clustered_repo)
        err = _check_clean_worktree(clustered_repo)
        assert err is not None

    def test_no_operation_passes(self, clustered_repo):
        err = _check_no_operation_in_progress(clustered_repo)
        assert err is None

    def test_rebase_in_progress_fails(self, clustered_repo):
        # Simulate rebase in progress
        (clustered_repo / ".git" / "rebase-merge").mkdir()
        err = _check_no_operation_in_progress(clustered_repo)
        assert err is not None
        assert "rebase" in err.lower()

    def test_merge_in_progress_fails(self, clustered_repo):
        (clustered_repo / ".git" / "MERGE_HEAD").write_text("abc123")
        err = _check_no_operation_in_progress(clustered_repo)
        assert err is not None
        assert "merge" in err.lower()

    def test_cherry_pick_in_progress_fails(self, clustered_repo):
        (clustered_repo / ".git" / "CHERRY_PICK_HEAD").write_text("abc123")
        err = _check_no_operation_in_progress(clustered_repo)
        assert err is not None
        assert "cherry" in err.lower()

    def test_no_remotes_passes(self, clustered_repo):
        shas = subprocess.run(
            ["git", "rev-list", "HEAD"],
            cwd=clustered_repo, capture_output=True, text=True,
        ).stdout.strip().split("\n")
        err = _check_not_pushed(clustered_repo, shas)
        assert err is None  # no remotes = safe

    def test_backup_branch_created(self, clustered_repo):
        name = _create_backup_branch(clustered_repo)
        assert name is not None
        assert name.startswith("backup/fix-dates-")

        # Branch actually exists
        out = subprocess.run(
            ["git", "branch", "--list", name],
            cwd=clustered_repo, capture_output=True, text=True,
        ).stdout.strip()
        assert name in out

    def test_collect_tree_shas(self, clustered_repo):
        trees = _collect_tree_shas(clustered_repo, "HEAD")
        assert len(trees) > 0
        # All values should be 40-char hex
        for sha, tree in trees.items():
            assert len(tree) == 40

    def test_fix_dates_blocked_by_dirty_worktree(self, clustered_repo):
        (clustered_repo / "dirty.txt").write_text("blocker")
        result = fix_dates(clustered_repo, "HEAD", spread_hours=3.0)
        assert result is False

    def test_fix_dates_blocked_by_rebase(self, clustered_repo):
        (clustered_repo / ".git" / "rebase-merge").mkdir()
        result = fix_dates(clustered_repo, "HEAD", spread_hours=3.0)
        assert result is False

    def test_fix_dates_creates_backup(self, clustered_repo):
        fix_dates(clustered_repo, "HEAD", spread_hours=3.0, jitter_minutes=0)

        out = subprocess.run(
            ["git", "branch", "--list", "backup/*"],
            cwd=clustered_repo, capture_output=True, text=True,
        ).stdout.strip()
        assert "backup/fix-dates-" in out

    def test_preflight_all_clear(self, clustered_repo):
        shas = subprocess.run(
            ["git", "rev-list", "HEAD"],
            cwd=clustered_repo, capture_output=True, text=True,
        ).stdout.strip().split("\n")
        ok, msgs = preflight_checks(clustered_repo, shas)
        assert ok is True

    def test_preflight_blocks_dirty(self, clustered_repo):
        (clustered_repo / "dirty.txt").write_text("no")
        ok, msgs = preflight_checks(clustered_repo, [])
        assert ok is False
        assert any("uncommitted" in m.lower() for m in msgs)
