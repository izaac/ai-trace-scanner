"""Config file loading and exclude filter."""

import re
from pathlib import Path

CONFIG_FILENAME = ".ai-trace-scan.yml"


def load_config(root):
    """Load .ai-trace-scan.yml from repo root if present."""
    config_path = Path(root) / CONFIG_FILENAME
    if not config_path.is_file():
        return {}
    try:
        try:
            import yaml

            with open(config_path) as f:
                return yaml.safe_load(f) or {}
        except ImportError:
            pass

        config = {}
        with open(config_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if ":" in line:
                    key, _, val = line.partition(":")
                    val = val.strip()
                    if val.startswith("[") and val.endswith("]"):
                        val = [v.strip().strip("'\"") for v in val[1:-1].split(",")]
                    config[key.strip()] = val
        return config
    except OSError:
        return {}


def make_exclude_filter(patterns):
    """Return a function that checks if a string matches any exclude pattern."""
    if not patterns:
        return lambda _: False
    compiled = [re.compile(p) for p in patterns]
    return lambda s: any(r.search(s) for r in compiled)
