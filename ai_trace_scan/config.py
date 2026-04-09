"""Config file loading and exclude filter."""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

CONFIG_FILENAME: str = ".ai-trace-scan.yml"


def load_config(root: str | Path) -> dict[str, Any]:
    """Load .ai-trace-scan.yml from repo root if present."""
    config_path = Path(root) / CONFIG_FILENAME
    if not config_path.is_file():
        return {}
    try:
        try:
            import yaml  # type: ignore[import-untyped]

            with open(config_path) as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            pass

        # Minimal fallback parser — only supports `key: value` and
        # `key: [a, b]` inline lists.  Warn if we encounter YAML
        # features that require the full parser.
        config: dict[str, Any] = {}
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("- "):
                    print(
                        f"  WARNING: {CONFIG_FILENAME} uses YAML list syntax "
                        "not supported by the fallback parser. "
                        "Install PyYAML (`pip install pyyaml`) for full support.",
                        file=sys.stderr,
                    )
                    return config
                if ":" in line:
                    key, _, val_str = line.partition(":")
                    val_str = val_str.strip()
                    val: Any
                    if val_str.startswith("[") and val_str.endswith("]"):
                        val = [v.strip().strip("'\"") for v in val_str[1:-1].split(",")]
                    else:
                        val = val_str
                    config[key.strip()] = val
        return config
    except OSError:
        return {}


def make_exclude_filter(patterns: list[str]) -> Callable[[str], bool]:
    """Return a function that checks if a string matches any exclude pattern."""
    if not patterns:
        return lambda _: False
    compiled: list[re.Pattern[str]] = [re.compile(p) for p in patterns]
    return lambda s: any(r.search(s) for r in compiled)
