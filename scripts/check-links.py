"""Fail when a relative Markdown link points at a missing file.

Absolute URLs, mail links and in-page anchors are skipped. Usage:

    python3 scripts/check-links.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache"}
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"```.*?```", re.DOTALL)
CODE_SPAN = re.compile(r"`[^`\n]*`")


def links_in(text: str) -> list[str]:
    # Links inside code blocks and code spans are examples, not links.
    prose = CODE_SPAN.sub("", FENCE.sub("", text))
    return [target for target in LINK.findall(prose) if "://" not in target]


def main() -> int:
    missing: list[str] = []
    for path in sorted(ROOT.rglob("*.md")):
        if SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        text = path.read_text(encoding="utf-8")
        for target in links_in(text):
            if target.startswith(("mailto:", "#")):
                continue
            relative = target.split("#", 1)[0].split("?", 1)[0]
            if not relative:
                continue
            if not (path.parent / relative).exists():
                missing.append(f"{path.relative_to(ROOT).as_posix()}: {target}")

    for line in missing:
        print(f"missing link target: {line}", file=sys.stderr)
    print(f"link check: {'ok' if not missing else f'{len(missing)} broken'}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
