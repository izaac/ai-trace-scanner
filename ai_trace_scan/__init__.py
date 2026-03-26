"""ai-trace-scan — Detect AI/agentic authorship fingerprints in a codebase."""

from __future__ import annotations

from typing import NamedTuple

__version__: str = "0.5.0"


class Finding(NamedTuple):
    severity: str
    category: str
    location: str
    message: str
