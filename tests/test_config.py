"""Tests for config loading and exclude filter."""

import pytest
from pathlib import Path

from ai_trace_scan.config import load_config, make_exclude_filter


class TestLoadConfig:
    def test_returns_empty_when_no_file(self, tmp_path):
        assert load_config(tmp_path) == {}

    def test_parses_simple_key_value(self, tmp_path):
        cfg = tmp_path / ".ai-trace-scan.yml"
        cfg.write_text("key: value\n")
        assert load_config(tmp_path) == {"key": "value"}

    def test_parses_list_value(self, tmp_path):
        cfg = tmp_path / ".ai-trace-scan.yml"
        cfg.write_text("exclude: ['upstream/', 'AGENTS\\.md']\n")
        result = load_config(tmp_path)
        assert result["exclude"] == ["upstream/", "AGENTS\\.md"]

    def test_skips_comments_and_empty_lines(self, tmp_path):
        cfg = tmp_path / ".ai-trace-scan.yml"
        cfg.write_text("# comment\n\nkey: value\n")
        assert load_config(tmp_path) == {"key": "value"}

    def test_handles_quoted_list_values(self, tmp_path):
        cfg = tmp_path / ".ai-trace-scan.yml"
        cfg.write_text('exclude: ["pattern1", "pattern2"]\n')
        result = load_config(tmp_path)
        assert result["exclude"] == ["pattern1", "pattern2"]


class TestMakeExcludeFilter:
    def test_empty_patterns_match_nothing(self):
        fn = make_exclude_filter([])
        assert fn("anything") is False
        assert fn("copilot/fix") is False

    def test_single_pattern(self):
        fn = make_exclude_filter([r"upstream/"])
        assert fn("upstream/copilot/fix") is True
        assert fn("origin/main") is False

    def test_multiple_patterns(self):
        fn = make_exclude_filter([r"upstream/", r"AGENTS\.md"])
        assert fn("upstream/copilot/fix") is True
        assert fn("AGENTS.md") is True
        assert fn("README.md") is False

    def test_regex_anchoring(self):
        fn = make_exclude_filter([r"^copilot/"])
        assert fn("copilot/fix-bug") is True
        assert fn("upstream/copilot/fix-bug") is False

    def test_case_sensitive_by_default(self):
        fn = make_exclude_filter([r"AGENTS\.md"])
        assert fn("AGENTS.md") is True
        assert fn("agents.md") is False
