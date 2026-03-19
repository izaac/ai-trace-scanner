"""ai-trace-scan — Detect AI/agentic authorship fingerprints in a codebase."""

from collections import namedtuple

__version__ = "0.4.0"

Finding = namedtuple("Finding", ["severity", "category", "location", "message"])
