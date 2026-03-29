"""CLI entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import Finding, __version__
from .config import load_config, make_exclude_filter
from .dates import fix_dates, scan_dates
from .git_scan import (
    get_default_branch,
    get_unpushed_range,
    is_git_repo,
    scan_branches,
    scan_commit_diffs,
    scan_commits,
    scan_staged,
    scan_tags,
)
from .output import format_json, format_text, supports_color
from .source_scan import scan_config_files, scan_source_tree, scan_workflows


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ai-trace-scan",
        description="Detect AI/agentic authorship fingerprints in a codebase.",
    )
    parser.add_argument(
        "path", nargs="?", default=".", help="Repository path to scan (default: current directory)"
    )
    parser.add_argument(
        "--staged", action="store_true", help="Scan only staged changes (pre-commit hook mode)"
    )
    parser.add_argument("--branch", metavar="REF", help="Scan commits in REF not in main/master")
    parser.add_argument(
        "--commits", type=int, default=50, metavar="N", help="Max commits to scan (default: 50)"
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Exclude findings matching regex (repeatable)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        dest="output_format",
        help="Output format (default: text)",
    )
    parser.add_argument("--no-color", action="store_true", help="Disable colored output")
    parser.add_argument("--quiet", action="store_true", help="Only print findings, no banner")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    date_group = parser.add_argument_group("date normalization")
    date_group.add_argument(
        "--fix-dates",
        action="store_true",
        help="Rewrite commit timestamps to realistic spacing (destructive)",
    )
    date_group.add_argument(
        "--all-commits",
        action="store_true",
        help="Rewrite ALL commits in the branch (default: only unpushed commits)",
    )
    date_group.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what --fix-dates would do without modifying history",
    )
    date_group.add_argument(
        "--force", action="store_true", help="Skip safety checks (pushed commits, confirmation)"
    )
    date_group.add_argument(
        "--sign",
        action="store_true",
        help="GPG/SSH sign all commits after rewriting timestamps",
    )
    date_group.add_argument(
        "--anchor",
        choices=["present", "first-commit"],
        default="present",
        help="Anchor last commit to now (default) or keep first commit's date",
    )
    date_group.add_argument(
        "--spread",
        type=float,
        default=3.0,
        metavar="HOURS",
        help="Time span per work session (default: 3.0)",
    )
    date_group.add_argument(
        "--jitter",
        type=float,
        default=15.0,
        metavar="MINUTES",
        help="Random variance per commit (default: 15.0)",
    )
    date_group.add_argument(
        "--burst",
        metavar="SESSIONS,GAP_DAYS",
        help="Split commits into work sessions with idle days between (e.g. 3,2)",
    )
    date_group.add_argument(
        "--no-weekends",
        action="store_true",
        help="Shift Saturday/Sunday commits to the following Monday",
    )
    date_group.add_argument(
        "--cluster-threshold",
        type=float,
        default=5.0,
        metavar="MINUTES",
        help="Flag commits closer than this average gap (default: 5.0)",
    )

    args = parser.parse_args()

    root: Path = Path(args.path).resolve()
    use_color: bool = supports_color() and not args.no_color and args.output_format == "text"

    if not root.is_dir():
        print(f"Error: {root} is not a directory", file=sys.stderr)
        sys.exit(2)

    has_git: bool = is_git_repo(root)

    # Fix dates mode -- rewrite and exit
    if args.fix_dates:
        if not has_git:
            print("Error: --fix-dates requires a git repository", file=sys.stderr)
            sys.exit(2)

        # Determine rev range
        rev_range: str
        if args.branch:
            default = get_default_branch(root)
            rev_range = f"{default}..{args.branch}" if default else args.branch
        elif args.all_commits:
            print(
                "  WARNING: --all-commits will rewrite the ENTIRE branch history.",
                file=sys.stderr,
            )
            if not args.force and not args.dry_run:
                try:
                    answer = input("  Continue? [y/N] ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    answer = ""
                if answer != "y":
                    print("  Aborted.", file=sys.stderr)
                    sys.exit(0)
            rev_range = "HEAD"
        else:
            # Default: only unpushed commits
            unpushed = get_unpushed_range(root)
            if unpushed is None:
                print(
                    "Error: No remote tracking branch found. Cannot determine unpushed commits.\n"
                    "  Use --branch <name> to target a specific branch, or\n"
                    "  Use --all-commits to rewrite the entire branch history.",
                    file=sys.stderr,
                )
                sys.exit(2)
            rev_range = unpushed
        burst: tuple[int, float] | None = None
        if args.burst:
            try:
                parts = args.burst.split(",")
                burst = (int(parts[0]), float(parts[1]))
            except (ValueError, IndexError):
                print("Error: --burst format is SESSIONS,GAP_DAYS (e.g. 3,2)", file=sys.stderr)
                sys.exit(2)
        ok: bool = fix_dates(
            root,
            rev_range,
            spread_hours=args.spread,
            jitter_minutes=args.jitter,
            burst=burst,
            dry_run=args.dry_run,
            force=args.force,
            anchor=args.anchor,
            no_weekends=args.no_weekends,
            sign=args.sign,
        )
        sys.exit(0 if ok else 2)

    # Normal scan mode
    config = load_config(root)
    excludes: list[str] = list(args.exclude)
    config_excludes = config.get("exclude", [])
    if isinstance(config_excludes, list):
        excludes.extend(config_excludes)
    elif isinstance(config_excludes, str):
        excludes.append(config_excludes)
    exclude_fn = make_exclude_filter(excludes)

    if not args.quiet and args.output_format == "text":
        name = root.name
        if use_color:
            print(f"\n  \033[1mai-trace-scan\033[0m -- {name}")
        else:
            print(f"\n  ai-trace-scan -- {name}")

    findings: list[Finding] = []

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
            findings.extend(scan_commit_diffs(root, rev_range, args.commits, exclude_fn))
            findings.extend(scan_dates(root, rev_range, args.commits, args.cluster_threshold))
            findings.extend(scan_branches(root, exclude_fn))
            findings.extend(scan_tags(root, exclude_fn))

        findings.extend(scan_config_files(root, exclude_fn))
        findings.extend(scan_source_tree(root, exclude_fn))
        findings.extend(scan_workflows(root, exclude_fn))

    if args.output_format == "json":
        print(format_json(findings))
    else:
        print(format_text(findings, use_color))

    sys.exit(1 if findings else 0)
