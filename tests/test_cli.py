"""Tests for CLI entry point."""

import subprocess
import os
import pytest


def _run_cli(*args, cwd=None):
    """Run ai-trace-scan as a subprocess."""
    result = subprocess.run(
        ["uv", "run", "ai-trace-scan"] + list(args),
        capture_output=True, text=True,
        cwd=cwd or os.path.dirname(os.path.dirname(__file__)),
    )
    return result


def _git_run(*args, cwd):
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "test@test.com",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@test.com"}
    subprocess.run(["git"] + list(args), cwd=cwd, capture_output=True, check=True, env=env)


class TestCliBasic:
    def test_version(self):
        result = _run_cli("--version")
        assert result.returncode == 0
        assert "ai-trace-scan" in result.stdout

    def test_help(self):
        result = _run_cli("--help")
        assert result.returncode == 0
        assert "fingerprints" in result.stdout

    def test_nonexistent_path(self):
        result = _run_cli("/nonexistent/path/12345")
        assert result.returncode == 2

    def test_clean_directory(self, tmp_path):
        result = _run_cli(str(tmp_path), "--quiet")
        assert result.returncode == 0
        assert "No AI authorship traces found" in result.stdout


class TestCliScan:
    def test_json_output(self, tmp_path):
        result = _run_cli(str(tmp_path), "--format", "json")
        assert result.returncode == 0
        assert result.stdout.strip() == "[]"

    def test_detects_config_files(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("config")
        result = _run_cli(str(tmp_path), "--no-color")
        assert result.returncode == 1
        assert "CLAUDE.md" in result.stdout

    def test_exclude_filters_findings(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("config")
        result = _run_cli(str(tmp_path), "--exclude", r"CLAUDE\.md")
        assert result.returncode == 0

    def test_quiet_suppresses_banner(self, tmp_path):
        result = _run_cli(str(tmp_path), "--quiet")
        assert "ai-trace-scan" not in result.stdout

    def test_no_color(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("config")
        result = _run_cli(str(tmp_path), "--no-color")
        assert "\033[" not in result.stdout


class TestCliGit:
    def test_staged_requires_git(self, tmp_path):
        result = _run_cli("--staged", str(tmp_path))
        assert result.returncode == 2

    def test_scan_git_repo(self, tmp_path):
        _git_run("init", cwd=tmp_path)
        _git_run("config", "user.email", "test@test.com", cwd=tmp_path)
        _git_run("config", "user.name", "Test", cwd=tmp_path)
        (tmp_path / "file.txt").write_text("init")
        _git_run("add", ".", cwd=tmp_path)
        _git_run("commit", "-m", "Initial commit", cwd=tmp_path)
        result = _run_cli(str(tmp_path))
        assert result.returncode == 0


class TestCliBurst:
    def test_invalid_burst_format(self, tmp_path):
        _git_run("init", cwd=tmp_path)
        _git_run("config", "user.email", "test@test.com", cwd=tmp_path)
        _git_run("config", "user.name", "Test", cwd=tmp_path)
        (tmp_path / "file.txt").write_text("init")
        _git_run("add", ".", cwd=tmp_path)
        _git_run("commit", "-m", "commit", cwd=tmp_path)
        result = _run_cli("--fix-dates", "--burst", "invalid", str(tmp_path))
        assert result.returncode == 2
