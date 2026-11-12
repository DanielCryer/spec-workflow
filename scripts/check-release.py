"""Fail when the top CHANGELOG version has no matching tag.

A release in this repository is two halves: a `## X.Y.Z` section at the top of
`CHANGELOG.md` and a tag `vX.Y.Z` on the commit that carries it. The section is
written as part of the change, the tag is a separate manual step, and that step
was skipped three times in a row while the changelog kept moving. This check
closes the gap:

- the first `## X.Y.Z` heading is the current version;
- the repository must have a `vX.Y.Z` tag.

Tags are read with `git tag --list`, so the checkout must have them. CI runs the
check on push only: a pull request bumps the version before the tag exists.

Usage:

    python3 scripts/check-release.py
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION = re.compile(r"^##\s+(\d+\.\d+\.\d+)\b", re.MULTILINE)
TAG_PREFIX = "v"


def current_version(changelog: str) -> str | None:
    """The version of the topmost `## X.Y.Z` section, or None when there is none."""
    match = VERSION.search(changelog)
    return match.group(1) if match else None


def tag_name(version: str) -> str:
    """Tag that marks the release."""
    return f"{TAG_PREFIX}{version}"


def git_tags(repo: Path) -> list[str] | None:
    """Release tags of the repository, or None when git cannot read them."""
    result = subprocess.run(
        ["git", "-C", str(repo), "tag", "--list", f"{TAG_PREFIX}*"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return None
    return result.stdout.split()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that the top CHANGELOG version is tagged."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=ROOT,
        help="repository root with CHANGELOG.md and git tags (default: this checkout)",
    )
    args = parser.parse_args(argv)

    changelog = args.repo / "CHANGELOG.md"
    if not changelog.is_file():
        print(f"no CHANGELOG.md in {args.repo}", file=sys.stderr)
        return 1

    version = current_version(changelog.read_text(encoding="utf-8"))
    if version is None:
        print("no `## X.Y.Z` version heading in CHANGELOG.md", file=sys.stderr)
        return 1

    tags = git_tags(args.repo)
    if tags is None:
        print(f"cannot read git tags in {args.repo}", file=sys.stderr)
        return 1
    if tag_name(version) not in tags:
        print(
            f"release {version} is not tagged: expected {tag_name(version)} in {args.repo}",
            file=sys.stderr,
        )
        return 1

    print(f"release {version} is tagged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
