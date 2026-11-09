"""Fail when a frontmatter value breaks a strict YAML parser.

Markdown files in `agents/`, `commands/`, `skills/` and `templates/` may open with
YAML frontmatter. OpenCode reads an unquoted value loosely, but a strict parser
(and the forge preview) does not, and the words themselves never matter — only a
few characters inside an unquoted value change what the parser sees:

- `": "` or a trailing `:` starts a nested mapping, the value is rejected;
- `" #"` cuts the value at the hash, the tail disappears without an error;
- a leading `@ `` ` `% ! & * | > ] } ,` or a lone `-` `?` `:` breaks the token;
- a tab is not allowed anywhere in the line;
- a repeated key silently overrides the first one.

Quoted values, block scalars and flow collections (`[]`, `{}`) are allowed. Usage:

    python3 scripts/check-frontmatter.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ("agents", "commands", "skills", "templates")
QUOTE = ('"', "'")
INDICATORS = ("@", "`", "%", "!", "&", "*", "|", ">", "]", "}", ",", "-", "?", ":")
BLOCK_HEADER = re.compile(r"^[|>][-+0-9]*$")
FIRST_LINE = 2  # frontmatter starts under the opening `---`


def unsafe_lines(frontmatter: list[str]) -> list[tuple[int, str, str]]:
    """Return file line number, line text and reason for values a parser misreads."""
    found: list[tuple[int, str, str]] = []
    block_indent: int | None = None
    seen: set[tuple[int, str]] = set()
    for offset, raw in enumerate(frontmatter):
        number = FIRST_LINE + offset
        text = raw.strip()
        if not text:
            continue

        indent = len(raw) - len(raw.lstrip(" "))
        if block_indent is not None:
            # A block scalar ends at the first line indented no deeper than its key.
            if indent > block_indent:
                continue

            block_indent = None

        if "\t" in raw:
            found.append((number, text, "tab in frontmatter"))
            continue

        body = text[2:].lstrip() if text.startswith("- ") else text
        key, separator, value = body.partition(":")
        if not separator:
            continue

        key = key.strip()
        if (indent, key) in seen:
            found.append((number, text, f"duplicate key `{key}`"))
        seen.add((indent, key))

        value = value.strip()
        if not value:
            continue

        if BLOCK_HEADER.match(value):
            block_indent = indent
            continue

        if value[0] in QUOTE:
            closing = value.rfind(value[0])
            if closing == 0:
                found.append((number, text, "unterminated quote"))
                continue

            tail = value[closing + 1 :].strip()
            if tail and not tail.startswith("#"):
                found.append((number, text, "text after the closing quote"))

            continue

        reason = ""
        if value[0] in INDICATORS:
            reason = f"value starts with `{value[0]}`"
        elif value[0] == "#":
            reason = "value is a comment"
        elif " #" in value:
            reason = "`#` after a space cuts the value"
        else:
            scalar = value.rstrip()
            if scalar.endswith(":") or ": " in scalar:
                reason = "colon in an unquoted value"

        if reason:
            found.append((number, text, reason))

    return found


def main() -> int:
    problems: list[str] = []
    for directory in SCAN_DIRS:
        for path in sorted((ROOT / directory).rglob("*.md")):
            lines = path.read_text(encoding="utf-8").splitlines()
            if not lines or lines[0].strip() != "---":
                continue

            relative = path.relative_to(ROOT).as_posix()
            end = next(
                (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
                None,
            )
            if end is None:
                problems.append(f"{relative}: frontmatter is never closed")
                continue

            for number, text, reason in unsafe_lines(lines[1:end]):
                problems.append(f"{relative}:{number}: {reason}: {text}")

    for problem in problems:
        print(f"unsafe frontmatter: {problem}", file=sys.stderr)
    print(f"frontmatter check: {'ok' if not problems else f'{len(problems)} unsafe'}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
