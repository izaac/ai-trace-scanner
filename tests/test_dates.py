"""Tests for commit date analysis and fix."""

import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from ai_trace_scan.dates import (
    _check_clean_worktree,
    _check_no_operation_in_progress,
    _check_not_pushed,
    _collect_tree_shas,
    _create_backup_branch,
    _detect_clustering,
    _skip_weekends,
    fix_dates,
    preflight_checks,
    scan_dates,
)


def _git_run(*args, cwd, env_extra=None):
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
    }
    if env_extra:
        env.update(env_extra)
    subprocess.run(["git", *list(args)], cwd=cwd, capture_output=True, check=True, env=env)


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
        _git_run(
            "commit",
            "-m",
            f"commit {i}",
            cwd=tmp_path,
            env_extra={"GIT_AUTHOR_DATE": datestr, "GIT_COMMITTER_DATE": datestr},
        )
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
            cwd=clustered_repo,
            capture_output=True,
            text=True,
        ).stdout
        dates = [datetime.fromisoformat(d) for d in out.strip().split("\n")]
        dates.reverse()
        total_span = (dates[-1] - dates[0]).total_seconds() / 3600
        assert total_span >= 2.5  # ~3 hours with some tolerance

    def test_preserves_first_commit_date_with_first_commit_anchor(self, clustered_repo):
        # Get original first commit date
        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%aI", "--reverse"],
            cwd=clustered_repo,
            capture_output=True,
            text=True,
        ).stdout
        original_first = out.strip().split("\n")[0]

        fix_dates(clustered_repo, "HEAD", spread_hours=3.0, jitter_minutes=0, anchor="first-commit")

        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%aI", "--reverse"],
            cwd=clustered_repo,
            capture_output=True,
            text=True,
        ).stdout
        new_first = out.strip().split("\n")[0]
        assert new_first == original_first

    def test_present_anchor_no_future_dates(self, clustered_repo):
        fix_dates(clustered_repo, "HEAD", spread_hours=3.0, jitter_minutes=0, anchor="present")

        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%aI"],
            cwd=clustered_repo,
            capture_output=True,
            text=True,
        ).stdout
        now = datetime.now(tz=timezone.utc)
        for line in out.strip().split("\n"):
            dt = datetime.fromisoformat(line)
            assert dt <= now + timedelta(minutes=1)  # small tolerance

    def test_burst_mode(self, tmp_path):
        repo = _make_clustered_repo(tmp_path, count=6, gap_seconds=60)
        result = fix_dates(repo, "HEAD", spread_hours=2.0, jitter_minutes=0, burst=(2, 3))
        assert result is True

        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%aI", "--reverse"],
            cwd=repo,
            capture_output=True,
            text=True,
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
            cwd=clustered_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
        original_shas = out.split("\n")

        result = fix_dates(clustered_repo, "HEAD", spread_hours=3.0, jitter_minutes=0, dry_run=True)
        assert result is True

        # SHAs must be unchanged
        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%H"],
            cwd=clustered_repo,
            capture_output=True,
            text=True,
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
        shas = (
            subprocess.run(
                ["git", "rev-list", "HEAD"],
                cwd=clustered_repo,
                capture_output=True,
                text=True,
            )
            .stdout.strip()
            .split("\n")
        )
        err = _check_not_pushed(clustered_repo, shas)
        assert err is None  # no remotes = safe

    def test_backup_branch_created(self, clustered_repo):
        name = _create_backup_branch(clustered_repo)
        assert name is not None
        assert name.startswith("backup/fix-dates-")

        # Branch actually exists
        out = subprocess.run(
            ["git", "branch", "--list", name],
            cwd=clustered_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert name in out

    def test_collect_tree_shas(self, clustered_repo):
        trees = _collect_tree_shas(clustered_repo, "HEAD")
        assert len(trees) > 0
        # All values should be 40-char hex
        for _sha, tree in trees.items():
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
            cwd=clustered_repo,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert "backup/fix-dates-" in out

    def test_preflight_all_clear(self, clustered_repo):
        shas = (
            subprocess.run(
                ["git", "rev-list", "HEAD"],
                cwd=clustered_repo,
                capture_output=True,
                text=True,
            )
            .stdout.strip()
            .split("\n")
        )
        ok, _msgs = preflight_checks(clustered_repo, shas)
        assert ok is True

    def test_preflight_blocks_dirty(self, clustered_repo):
        (clustered_repo / "dirty.txt").write_text("no")
        ok, msgs = preflight_checks(clustered_repo, [])
        assert ok is False
        assert any("uncommitted" in m.lower() for m in msgs)


class TestSkipWeekends:
    """Tests for --no-weekends logic."""

    def test_saturday_moves_to_monday(self):
        # 2026-03-28 is a Saturday
        dates = {"abc": "2026-03-28T14:00:00-07:00"}
        result = _skip_weekends(dates)
        dt = datetime.fromisoformat(result["abc"])
        assert dt.weekday() == 0  # Monday
        assert dt.day == 30

    def test_sunday_moves_to_monday(self):
        # 2026-03-29 is a Sunday
        dates = {"abc": "2026-03-29T10:30:00-07:00"}
        result = _skip_weekends(dates)
        dt = datetime.fromisoformat(result["abc"])
        assert dt.weekday() == 0  # Monday
        assert dt.day == 30

    def test_weekday_unchanged(self):
        # 2026-03-27 is a Friday
        dates = {"abc": "2026-03-27T09:00:00-07:00"}
        result = _skip_weekends(dates)
        dt = datetime.fromisoformat(result["abc"])
        assert dt.weekday() == 4  # Friday
        assert dt.day == 27

    def test_preserves_time_of_day(self):
        dates = {"abc": "2026-03-28T15:42:30-07:00"}
        result = _skip_weekends(dates)
        dt = datetime.fromisoformat(result["abc"])
        assert dt.hour == 15
        assert dt.minute == 42
        assert dt.second == 30

    def test_multiple_commits_mixed(self):
        dates = {
            "a": "2026-03-27T10:00:00-07:00",  # Friday
            "b": "2026-03-28T11:00:00-07:00",  # Saturday
            "c": "2026-03-29T12:00:00-07:00",  # Sunday
            "d": "2026-03-30T13:00:00-07:00",  # Monday
        }
        result = _skip_weekends(dates)
        days = {sha: datetime.fromisoformat(d).weekday() for sha, d in result.items()}
        assert days["a"] == 4  # Friday stays
        assert days["b"] == 0  # Saturday -> Monday
        assert days["c"] == 0  # Sunday -> Monday
        assert days["d"] == 0  # Monday stays

    def test_fix_dates_with_no_weekends(self, tmp_path):
        """Integration: commits landing on weekends get shifted."""
        # Create a repo with commits on a Saturday
        _git_run("init", cwd=tmp_path)
        _git_run("config", "user.email", "test@test.com", cwd=tmp_path)
        _git_run("config", "user.name", "Test", cwd=tmp_path)

        # 2026-03-28 is Saturday
        base = datetime(2026, 3, 28, 10, 0, 0)
        for i in range(3):
            dt = base + timedelta(minutes=i)
            datestr = dt.strftime("%Y-%m-%dT%H:%M:%S-07:00")
            (tmp_path / f"file{i}.txt").write_text(str(i))
            _git_run("add", ".", cwd=tmp_path)
            _git_run(
                "commit",
                "-m",
                f"commit {i}",
                cwd=tmp_path,
                env_extra={"GIT_AUTHOR_DATE": datestr, "GIT_COMMITTER_DATE": datestr},
            )

        result = fix_dates(
            tmp_path,
            "HEAD",
            spread_hours=3.0,
            jitter_minutes=0,
            no_weekends=True,
            anchor="first-commit",
        )
        assert result is True

        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%aI"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        ).stdout
        for line in out.strip().split("\n"):
            dt = datetime.fromisoformat(line)
            assert dt.weekday() < 5, f"Commit on weekend: {line}"
