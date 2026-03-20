"""Tests for output formatting."""

import json
import pytest

from ai_trace_scan import Finding
from ai_trace_scan.output import format_text, format_json


class TestFormatText:
    def test_no_findings_no_color(self):
        result = format_text([], use_color=False)
        assert "OK" in result
        assert "No AI authorship traces found" in result

    def test_no_findings_with_color(self):
        result = format_text([], use_color=True)
        assert "OK" in result
        assert "\033[92m" in result

    def test_single_finding(self):
        findings = [Finding("high", "git-history", "commit abc123", "Copilot trailer")]
        result = format_text(findings, use_color=False)
        assert "1 finding(s)" in result
        assert "[HIGH]" in result
        assert "commit abc123" in result
        assert "Copilot trailer" in result

    def test_groups_by_category(self):
        findings = [
            Finding("high", "git-history", "commit abc", "trailer"),
            Finding("medium", "branch-name", "copilot/fix", "pattern"),
            Finding("high", "git-history", "commit def", "another trailer"),
        ]
        result = format_text(findings, use_color=False)
        assert "Git History" in result
        assert "Branch Name" in result

    def test_color_codes_by_severity(self):
        findings = [
            Finding("high", "test", "loc1", "msg1"),
            Finding("medium", "test", "loc2", "msg2"),
            Finding("low", "test", "loc3", "msg3"),
        ]
        result = format_text(findings, use_color=True)
        assert "\033[91m" in result  # high = red
        assert "\033[93m" in result  # medium = yellow
        assert "\033[90m" in result  # low = gray


class TestFormatJson:
    def test_empty_findings(self):
        result = format_json([])
        assert json.loads(result) == []

    def test_single_finding(self):
        findings = [Finding("high", "git-history", "commit abc", "trailer")]
        result = json.loads(format_json(findings))
        assert len(result) == 1
        assert result[0] == {
            "severity": "high",
            "category": "git-history",
            "location": "commit abc",
            "message": "trailer",
        }

    def test_multiple_findings_preserve_order(self):
        findings = [
            Finding("high", "a", "loc1", "msg1"),
            Finding("medium", "b", "loc2", "msg2"),
        ]
        result = json.loads(format_json(findings))
        assert len(result) == 2
        assert result[0]["category"] == "a"
        assert result[1]["category"] == "b"
