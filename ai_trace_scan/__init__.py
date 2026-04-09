"""ai-trace-scan — Detect AI/agentic authorship fingerprints in a codebase."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Literal, NamedTuple

try:
    __version__: str = version("ai-trace-scan")
except PackageNotFoundError:
    __version__ = "0.0.0"

Severity = Literal["high", "medium", "low"]


class Finding(NamedTuple):
    severity: Severity
    category: str
    location: str
    message: str
