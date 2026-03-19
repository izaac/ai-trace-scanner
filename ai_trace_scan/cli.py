"""CLI entry point."""

import argparse
import sys
from pathlib import Path

from . import __version__
from .config import load_config, make_exclude_filter
from .git_scan import (
    get_default_branch,
    is_git_repo,
    scan_branches,
    scan_commits,
    scan_staged,
    scan_tags,
)
from .output import format_json, format_text, supports_color
from .source_scan import scan_config_files, scan_source_tree


def main():
    parser = argparse.ArgumentParser(
        prog="ai-trace-scan",
        description="Detect AI/agentic authorship fingerprints in a codebase.",
    )
    parser.add_argument("path", nargs="?", default=".",
                        help="Repository path to scan (default: current directory)")
    parser.add_argument("--staged", action="store_true",
                        help="Scan only staged changes (pre-commit hook mode)")
    parser.add_argument("--branch", metavar="REF",
                        help="Scan commits in REF not in main/master")
    parser.add_argument("--commits", type=int, default=50, metavar="N",
                        help="Max commits to scan (default: 50)")
    parser.add_argument("--exclude", action="append", default=[], metavar="PATTERN",
                        help="Exclude findings matching regex (repeatable)")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        dest="output_format",
                        help="Output format (default: text)")
    parser.add_argument("--no-color", action="store_true",
                        help="Disable colored output")
    parser.add_argument("--quiet", action="store_true",
                        help="Only print findings, no banner")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args()

    root = Path(args.path).resolve()
    use_color = supports_color() and not args.no_color and args.output_format == "text"

    if not root.is_dir():
        print(f"Error: {root} is not a directory", file=sys.stderr)
        sys.exit(2)

    config = load_config(root)
    excludes = list(args.exclude)
    config_excludes = config.get("exclude", [])
    if isinstance(config_excludes, list):
        excludes.extend(config_excludes)
    elif isinstance(config_excludes, str):
        excludes.append(config_excludes)
    exclude_fn = make_exclude_filter(excludes)

    if not args.quiet and args.output_format == "text":
        name = root.name
        if use_color:
            print(f"\n  \033[1mai-trace-scan\033[0m — {name}")
        else:
            print(f"\n  ai-trace-scan — {name}")

    findings = []
    has_git = is_git_repo(root)

    if args.staged:
        if not has_git:
            print("Error: --staged requires a git repository", file=sys.stderr)
            sys.exit(2)
        findings.extend(scan_staged(root, exclude_fn))
    else:
        if has_git:
            if args.branch:
                default = get_default_branch(root)
                rev_range = f"{default}..{args.branch}" if default else args.branch
            else:
                rev_range = "HEAD"

            findings.extend(scan_commits(root, rev_range, args.commits, exclude_fn))
            findings.extend(scan_branches(root, exclude_fn))
            findings.extend(scan_tags(root, exclude_fn))

        findings.extend(scan_config_files(root, exclude_fn))
        findings.extend(scan_source_tree(root, exclude_fn))

    if args.output_format == "json":
        print(format_json(findings))
    else:
        print(format_text(findings, use_color))

    sys.exit(1 if findings else 0)
