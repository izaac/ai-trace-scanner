"""Output formatting — text and JSON."""

from __future__ import annotations

import json
import sys

from . import Finding

SEVERITY_COLORS: dict[str, str] = {"high": "\033[91m", "medium": "\033[93m", "low": "\033[90m"}
RESET: str = "\033[0m"
BOLD: str = "\033[1m"


def supports_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def format_text(findings: list[Finding], use_color: bool) -> str:
    lines: list[str] = []
    if not findings:
        mark = "\033[92mOK\033[0m" if use_color else "OK"
        lines.append(f"\n  {mark} No AI authorship traces found.\n")
        return "\n".join(lines)

    lines.append(f"\n  Found {len(findings)} finding(s):\n")

    by_category: dict[str, list[Finding]] = {}
    for f in findings:
        by_category.setdefault(f.category, []).append(f)

    for cat, items in by_category.items():
        header = cat.replace("-", " ").title()
        if use_color:
            lines.append(f"  {BOLD}{header}{RESET}")
        else:
            lines.append(f"  {header}")

        for item in items:
            sev = item.severity.upper()
            if use_color:
                color = SEVERITY_COLORS.get(item.severity, "")
                lines.append(f"    {color}[{sev}]{RESET} {item.location}")
                lines.append(f"           {item.message}")
            else:
                lines.append(f"    [{sev}] {item.location}")
                lines.append(f"           {item.message}")
        lines.append("")

    return "\n".join(lines)


def format_json(findings: list[Finding]) -> str:
    return json.dumps([f._asdict() for f in findings], indent=2)
