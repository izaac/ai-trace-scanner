"""Tests for git-related scanners."""

import os
import subprocess

import pytest

from ai_trace_scan.git_scan import (
    get_default_branch,
    git,
    is_git_repo,
    scan_branches,
    scan_commit_diffs,
    scan_commits,
    scan_staged,
    scan_tags,
)


def _git_run(*args, cwd):
    subprocess.run(
        ["git", *list(args)],
        cwd=cwd,
        capture_output=True,
        check=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Test",
            "GIT_AUTHOR_EMAIL": "test@test.com",
            "GIT_COMMITTER_NAME": "Test",
            "GIT_COMMITTER_EMAIL": "test@test.com",
        },
    )


def _init_repo(tmp_path):
    _git_run("init", cwd=tmp_path)
    _git_run("config", "user.email", "test@test.com", cwd=tmp_path)
    _git_run("config", "user.name", "Test", cwd=tmp_path)
    (tmp_path / "file.txt").write_text("init")
    _git_run("add", ".", cwd=tmp_path)
    _git_run("commit", "-m", "Initial commit", cwd=tmp_path)
    return tmp_path


@pytest.fixture
def git_repo(tmp_path):
    return _init_repo(tmp_path)


class TestGitHelpers:
    def test_git_returns_stdout(self, git_repo):
        result = git("rev-parse", "--git-dir", cwd=git_repo)
        assert result is not None
        assert ".git" in result

    def test_git_returns_none_on_failure(self, tmp_path):
        result = git("rev-parse", "--git-dir", cwd=tmp_path)
        assert result is None

    def test_is_git_repo_true(self, git_repo):
        assert is_git_repo(git_repo) is True

    def test_is_git_repo_false(self, tmp_path):
        assert is_git_repo(tmp_path) is False

    def test_get_default_branch(self, git_repo):
        branch = get_default_branch(git_repo)
        assert branch in ("main", "master")


class TestScanCommits:
    def test_detects_copilot_trailer(self, git_repo):
        (git_repo / "f2.txt").write_text("data")
        _git_run("add", ".", cwd=git_repo)
        _git_run(
            "commit", "-m", "Fix bug\n\nCo-authored-by: Copilot <copilot@github.com>", cwd=git_repo
        )
        findings = scan_commits(git_repo, "HEAD", 10, lambda _: False)
        assert any("Copilot trailer" in f.message for f in findings)

    def test_detects_agentic_language(self, git_repo):
        (git_repo / "f2.txt").write_text("data")
        _git_run("add", ".", cwd=git_repo)
        _git_run("commit", "-m", "Fix: as an AI I rewrote this", cwd=git_repo)
        findings = scan_commits(git_repo, "HEAD", 10, lambda _: False)
        assert any("Agentic language" in f.message for f in findings)

    def test_detects_bot_author(self, git_repo):
        (git_repo / "f2.txt").write_text("data")
        _git_run("add", ".", cwd=git_repo)
        subprocess.run(
            ["git", "commit", "-m", "Bot commit"],
            cwd=git_repo,
            capture_output=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "copilot[bot]",
                "GIT_AUTHOR_EMAIL": "copilot[bot]@users.noreply.github.com",
                "GIT_COMMITTER_NAME": "copilot[bot]",
                "GIT_COMMITTER_EMAIL": "copilot[bot]@users.noreply.github.com",
            },
        )
        findings = scan_commits(git_repo, "HEAD", 10, lambda _: False)
        assert any("bot author" in f.message.lower() for f in findings)

    def test_clean_commits_no_findings(self, git_repo):
        findings = scan_commits(git_repo, "HEAD", 10, lambda _: False)
        assert len(findings) == 0

    def test_respects_exclude(self, git_repo):
        (git_repo / "f2.txt").write_text("data")
        _git_run("add", ".", cwd=git_repo)
        _git_run(
            "commit", "-m", "Fix\n\nCo-authored-by: Copilot <copilot@github.com>", cwd=git_repo
        )
        # Exclude all commits
        findings = scan_commits(git_repo, "HEAD", 10, lambda _: True)
        assert len(findings) == 0


class TestScanBranches:
    def test_detects_copilot_branch(self, git_repo):
        _git_run("branch", "copilot/fix-bug", cwd=git_repo)
        findings = scan_branches(git_repo, lambda _: False)
        assert any("copilot/fix-bug" in f.location for f in findings)

    def test_detects_aider_branch(self, git_repo):
        _git_run("branch", "aider-refactor", cwd=git_repo)
        findings = scan_branches(git_repo, lambda _: False)
        assert any("aider-refactor" in f.location for f in findings)

    def test_clean_branches_no_findings(self, git_repo):
        _git_run("branch", "feature/user-auth", cwd=git_repo)
        findings = scan_branches(git_repo, lambda _: False)
        assert len(findings) == 0

    def test_respects_exclude(self, git_repo):
        _git_run("branch", "copilot/fix-bug", cwd=git_repo)

        def exclude_fn(s):
            return "copilot" in s

        findings = scan_branches(git_repo, exclude_fn)
        assert len(findings) == 0


class TestScanTags:
    def test_detects_trailer_in_tag(self, git_repo):
        _git_run(
            "tag",
            "-a",
            "v1.0",
            "-m",
            "Release\n\nCo-authored-by: Copilot <c@github.com>",
            cwd=git_repo,
        )
        findings = scan_tags(git_repo, lambda _: False)
        assert any("tag v1.0" in f.location for f in findings)

    def test_clean_tag_no_findings(self, git_repo):
        _git_run("tag", "-a", "v1.0", "-m", "Release v1.0", cwd=git_repo)
        findings = scan_tags(git_repo, lambda _: False)
        assert len(findings) == 0

    def test_respects_exclude(self, git_repo):
        _git_run(
            "tag",
            "-a",
            "v1.0",
            "-m",
            "Release\n\nCo-authored-by: Copilot <c@github.com>",
            cwd=git_repo,
        )
        findings = scan_tags(git_repo, lambda s: "v1.0" in s)
        assert len(findings) == 0


class TestScanStaged:
    def test_detects_trailer_in_staged_diff(self, git_repo):
        f = git_repo / "new.py"
        f.write_text("# Co-authored-by: Copilot <copilot@github.com>\nx = 1\n")
        _git_run("add", "new.py", cwd=git_repo)
        findings = scan_staged(git_repo, lambda _: False)
        assert len(findings) >= 1

    def test_detects_ai_comment_in_staged(self, git_repo):
        f = git_repo / "new.py"
        f.write_text("# generated by copilot\nx = 1\n")
        _git_run("add", "new.py", cwd=git_repo)
        findings = scan_staged(git_repo, lambda _: False)
        assert len(findings) >= 1

    def test_clean_staged_no_findings(self, git_repo):
        f = git_repo / "clean.py"
        f.write_text("def hello():\n    print('hi')\n")
        _git_run("add", "clean.py", cwd=git_repo)
        findings = scan_staged(git_repo, lambda _: False)
        assert len(findings) == 0

    def test_respects_exclude(self, git_repo):
        f = git_repo / "skip.py"
        f.write_text("# generated by copilot\n")
        _git_run("add", "skip.py", cwd=git_repo)
        findings = scan_staged(git_repo, lambda s: "skip" in s)
        assert len(findings) == 0


class TestScanCommitDiffs:
    def test_detects_generated_by_copilot(self, git_repo):
        f = git_repo / "app.py"
        f.write_text("# generated by copilot\ndef main(): pass\n")
        _git_run("add", ".", cwd=git_repo)
        _git_run("commit", "-m", "Add app", cwd=git_repo)
        findings = scan_commit_diffs(git_repo, "HEAD", 10, lambda _: False)
        assert any(f.category == "commit-diff" for f in findings)
        assert any(
            "copilot" in f.message.lower() or "generation" in f.message.lower() for f in findings
        )

    def test_ignores_deleted_lines(self, git_repo):
        f = git_repo / "app.py"
        f.write_text("# generated by copilot\ndef main(): pass\n")
        _git_run("add", ".", cwd=git_repo)
        _git_run("commit", "-m", "Add app", cwd=git_repo)
        # Now remove the offending line
        f.write_text("def main(): pass\n")
        _git_run("add", ".", cwd=git_repo)
        _git_run("commit", "-m", "Clean up", cwd=git_repo)
        # Only scan the second commit
        findings = scan_commit_diffs(git_repo, "HEAD~1..HEAD", 10, lambda _: False)
        assert all(
            f.category != "commit-diff" or "copilot" not in f.message.lower() for f in findings
        )

    def test_respects_exclude(self, git_repo):
        f = git_repo / "skip.py"
        f.write_text("# generated by copilot\n")
        _git_run("add", ".", cwd=git_repo)
        _git_run("commit", "-m", "Add skip", cwd=git_repo)
        findings = scan_commit_diffs(git_repo, "HEAD", 10, lambda s: "skip" in s)
        assert len(findings) == 0

    def test_detects_diff_pattern_todo(self, git_repo):
        f = git_repo / "work.py"
        f.write_text("# TODO: copilot should fix this\nx = 1\n")
        _git_run("add", ".", cwd=git_repo)
        _git_run("commit", "-m", "Add work", cwd=git_repo)
        findings = scan_commit_diffs(git_repo, "HEAD", 10, lambda _: False)
        assert any("TODO" in f.message for f in findings)

    def test_clean_diff_no_findings(self, git_repo):
        f = git_repo / "clean.py"
        f.write_text("def hello():\n    print('hi')\n")
        _git_run("add", ".", cwd=git_repo)
        _git_run("commit", "-m", "Add clean code", cwd=git_repo)
        findings = scan_commit_diffs(git_repo, "HEAD", 10, lambda _: False)
        assert len(findings) == 0

    def test_severity_is_medium(self, git_repo):
        f = git_repo / "app.py"
        f.write_text("# generated by copilot\n")
        _git_run("add", ".", cwd=git_repo)
        _git_run("commit", "-m", "Add app", cwd=git_repo)
        findings = scan_commit_diffs(git_repo, "HEAD", 10, lambda _: False)
        assert all(f.severity == "medium" for f in findings if f.category == "commit-diff")
