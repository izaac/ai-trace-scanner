"""Tests for commit date analysis and fix."""

import os
import subprocess
import pytest
from datetime import datetime, timedelta

from ai_trace_scan.dates import _detect_clustering, scan_dates, fix_dates


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

    def test_preserves_first_commit_date(self, clustered_repo):
        # Get original first commit date
        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%aI", "--reverse"],
            cwd=clustered_repo, capture_output=True, text=True,
        ).stdout
        original_first = out.strip().split("\n")[0]

        fix_dates(clustered_repo, "HEAD", spread_hours=3.0, jitter_minutes=0)

        out = subprocess.run(
            ["git", "--no-pager", "log", "--format=%aI", "--reverse"],
            cwd=clustered_repo, capture_output=True, text=True,
        ).stdout
        new_first = out.strip().split("\n")[0]
        assert new_first == original_first

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
