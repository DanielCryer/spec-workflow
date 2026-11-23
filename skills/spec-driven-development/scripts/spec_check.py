"""Deterministic gate for the spec: form, not meaning.

The gate answers one question: can the claims made in a spec folder be resolved
against the code repositories as they are now? It does not judge whether a
feature is worth having; that is the review role.

Checks:

- ``symbols``   a checked plan item names a class or attribute that does not
  exist in the code (a stale name after a rename). Plan items are read from
  ``plan.md``, ``stages/*.md`` and ``stages/*/stage.md``; the code may be C, C++,
  Python, TypeScript or QML;
- ``commits``   every ``repo@hash`` and bare ``@hash`` resolves to a commit;
- ``paths``     every ``file.ext:NN`` reference points inside an existing file;
- ``numbering`` decision and problem numbers are unique in the task folder, a
  stage journal always carries its stage code, every reference resolves to a
  record or to a transfer, the root summary lists every root record, and — once
  stage codes are in use — a moved record is named on both sides and
  ``**Правило:**`` in a record matches ``Источники`` in ``rules/``. The index may
  be a line per decision or a thematic digest that names the numbers;
  ``--index`` sets how strict that check is;
- ``shortcuts`` every ``shortcut:`` marker in a code comment states a limit and
  a return condition. Only comments count: a marker inside a string literal or
  a docstring is not a marker, and identifiers such as ``QShortcut::activated``
  do not match;
- ``todo``      ``todo.md`` stays under the cap.

The gate never runs the tests: running them is the executor's job, and the
result is recorded in the step card.

The spec folder is the input. Repository paths come from the ``repos:`` field of
``index.md`` and the convention ``<projects-root>/<name>``; an entry may be a
path, so submodules work as ``<name>=<path>`` or ``sub/module`` (the name is the
last segment); ``ref_roots:`` adds extra locations for ``file.ext:NN`` references
outside the repositories. ``code_roots:`` narrows where names and markers are
looked up (paths are relative to ``<projects-root>``, e.g. ``beta/Sources``);
without it the gate reads ``src``/``tests`` of a repository, or the whole
repository. Vendor and build directories are never read.

``<projects-root>`` is ``PROJECTS_ROOT``, else the parent of ``SPECS_ROOT``,
else ``~/projects``.

Usage:

    python3 scripts/spec_check.py --spec <SPECS_ROOT>/tasks/<folder>
    python3 scripts/spec_check.py --spec <folder> --strict
    python3 scripts/spec_check.py --spec <folder> --file-root <extra>
    python3 scripts/spec_check.py --spec <folder> --index strict

Errors fail the gate; warnings only fail it with ``--strict``. Exit code is 1
on failure, 0 otherwise.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

_CODE_SUFFIXES = (
    ".c", ".cc", ".cpp", ".cxx", ".c++",
    ".h", ".hh", ".hpp", ".hxx", ".h++",
    ".inl", ".ipp", ".tpp",
    ".py", ".pyi",
    ".js", ".jsx", ".ts", ".tsx", ".qml", ".proto",
)

_ARTIFACT_SUFFIXES = (
    ".json", ".jsonl", ".md", ".toml", ".jsonc", ".txt", ".yml", ".yaml", ".sh",
)

# A span with one of these suffixes is a path, not a symbol name.
_FILE_SUFFIXES = tuple(dict.fromkeys(_ARTIFACT_SUFFIXES + _CODE_SUFFIXES))

# Vendor and build trees are never read: a stale name must not be "found" there,
# and indexing them is the slowest part of a large repository.
_IGNORED_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", ".idea", ".vscode", "__pycache__", ".pytest_cache",
        ".venv", "venv", "node_modules",
        "build", "Build", "cmake-build-debug", "cmake-build-release",
        "Externals", "externals", "external", "third_party", "third-party",
        "3rdparty", "vendor",
    }
)

_EXTENSIONS = "|".join(
    re.escape(suffix.lstrip(".")) for suffix in sorted(_FILE_SUFFIXES, key=len, reverse=True)
)
_ITEM = re.compile(r"^(\s*)- \[([ xX])\] (.*)$")
_SPAN = re.compile(r"`([^`]+)`")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TYPE_ARGS = re.compile(r"<[^<>]*>")
_UPPERCASE = re.compile(r"[A-Z]")
_COMMIT = re.compile(r"(?<![A-Za-z0-9_])(?:([A-Za-z][A-Za-z0-9_.-]*))?@([0-9a-f]{7,40})\b")
_FILE_REF = re.compile(
    rf"(?P<path>[A-Za-z0-9_][\w./\\-]*\.(?:{_EXTENSIONS}))"
    r":(?P<start>\d+)(?:-(?P<end>\d+))?"
)
_TOKEN_TEXT = re.compile(r"([DP])(?:([a-z]{2,8})\.)?(\d{1,4})")
_RECORD = re.compile(r"^## ([DP])(?:([a-z]{2,8})\.)?(\d{1,4})\.", re.MULTILINE)
_TOMBSTONE = re.compile(
    r"\*\*\s*Статус:?\s*\*\*:?\s*(?:объединено|влито|поглощено|absorbed|merged)"
    r"|—\s*влито в D\S+"
    r"|absorbed into D\S+"
    r"|merged into D\S+",
    re.IGNORECASE,
)
_DECISION_INDEX = re.compile(
    r"^- \*\*(D(?:[a-z]{2,8}\.)?\d+)\*\*", re.MULTILINE
)
_ACTIVE_HEADING = re.compile(
    r"^##[ \t]+(?:Действующие решения|Active decisions)[ \t]*$", re.MULTILINE | re.IGNORECASE
)
_MOVED_HEADING = re.compile(
    r"^##[ \t]+(?:Перенесено|Moved)[ \t]*$", re.MULTILINE | re.IGNORECASE
)
_SOURCES_HEADING = re.compile(
    r"^##[ \t]+(?:Источники|Sources)[ \t]*$", re.MULTILINE | re.IGNORECASE
)
_REFERENCE = re.compile(r"\b([DP])(?:([a-z]{2,8})\.)?(\d{1,4})\b")
_MOVED_LINE = re.compile(
    r"^-[ \t]*([DP](?:[a-z]{2,8}\.)?\d+)[ \t]*→[ \t]*([DP](?:[a-z]{2,8}\.)?\d+)"
    r"(?:[ \t]*(?:—|-)[ \t]*`?([^`\s]+)`?)?",
    re.MULTILINE,
)
_BY_WAS = re.compile(
    r"\*\*\s*Было:?\s*\*\*:?[ \t]*`?([DP](?:[a-z]{2,8}\.)?\d+)`?"
)
_RULE_REF = re.compile(
    r"\*\*\s*Правило:?\s*\*\*:?[ \t]*`?([\w./-]+\.md)`?"
)
_CODE_DECL = re.compile(
    r"\*\*\s*Код:?\s*\*\*:?[ \t]*`?([A-Za-z0-9_-]+)`?", re.MULTILINE
)
_CODE_TEXT = re.compile(r"[a-z]{2,8}")

# A marker lives in a comment: `# shortcut:`, `// shortcut:`, `/* shortcut: */`.
# Without the prefix, `QShortcut::activated` and strings like "shortcut: …"
# would count as markers.
_SHORTCUT = re.compile(r"(?:\*|//+|#)\s*shortcut:\s*(.+)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Finding:
    level: str
    check: str
    where: str
    message: str

    def render(self) -> str:
        return f"{self.level.upper():5}  {self.check:9}  {self.where}  {self.message}"


@dataclass(slots=True)
class PlanItem:
    line: int
    checked: bool
    text: str


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# plan symbols


def parse_plan(text: str) -> list[PlanItem]:
    """Return plan items, joining indented continuation lines."""
    items: list[PlanItem] = []
    current: PlanItem | None = None
    for number, line in enumerate(text.splitlines(), 1):
        match = _ITEM.match(line)
        if match:
            current = PlanItem(number, match.group(2).lower() == "x", match.group(3))
            items.append(current)
        elif current is not None and line.strip() and line.startswith((" ", "\t")):
            current.text = current.text + " " + line.strip()
        else:
            current = None
    return items


def _iter_files(root: Path) -> Iterator[Path]:
    """Files under ``root``, skipping vendor and build trees."""
    if root.is_file():
        yield root
        return
    if not root.is_dir():
        return
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in _IGNORED_DIRS:
                        continue
                    stack.append(Path(entry.path))
                elif entry.is_file():
                    yield Path(entry.path)
            except OSError:
                continue


@dataclass(slots=True)
class SymbolIndex:
    """Where a name may live: code files under the configured roots."""

    roots: Sequence[Path]

    def missing(self, names: Iterable[str]) -> set[str]:
        """Names not found anywhere in the code; one pass over the files."""
        wanted = {name: re.compile(rf"\b{re.escape(name)}\b") for name in names}
        for path in self._files():
            if not wanted:
                break
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for name, pattern in tuple(wanted.items()):
                if pattern.search(text):
                    del wanted[name]
        return set(wanted)

    def _files(self) -> Iterator[Path]:
        for root in _dedupe_roots(self.roots):
            for path in _iter_files(root):
                if path.suffix.lower() in _CODE_SUFFIXES:
                    yield path


def _symbol_name(span: str) -> str | None:
    """The name a backticked span refers to, or ``None`` when it is not a symbol.

    Paths and artifacts are skipped, prose words in lowercase are skipped (too
    many false hits), and the C++/Python ways of writing a name are reduced to
    its last segment: ``ntpro::dto::FieldMask`` -> ``FieldMask``,
    ``FieldMask<T>`` -> ``FieldMask``, ``ResetFieldsByFlag()`` ->
    ``ResetFieldsByFlag``, ``Event.to_record`` -> ``to_record``.
    """
    span = span.strip()
    if any(char in span for char in "/\\,→ \t"):
        return None
    if span.lower().endswith(_FILE_SUFFIXES):
        return None
    span = span.removesuffix("()")
    span = _TYPE_ARGS.sub("", span)
    span = span.split("::")[-1]
    # A qualified name is `Head.tail`, not a dotfile: `.gitignore` must not turn
    # into the symbol `gitignore`.
    head, _, tail = span.rpartition(".")
    dotted = bool(head) and all(_IDENTIFIER.fullmatch(part) for part in head.split("."))
    span = tail or span
    if not _IDENTIFIER.fullmatch(span):
        return None
    if dotted:
        return span
    if not _UPPERCASE.search(span):
        return None
    return span


def symbol_candidates(text: str) -> list[str]:
    """Names in backticks that must exist in the code."""
    names: list[str] = []
    for span in _SPAN.findall(text):
        name = _symbol_name(span)
        if name is not None and name not in names:
            names.append(name)
    return names


def _missing_names(names: Iterable[str], corpus: str | SymbolIndex) -> set[str]:
    wanted = set(names)
    if isinstance(corpus, str):
        return {name for name in wanted if not re.search(rf"\b{re.escape(name)}\b", corpus)}
    return corpus.missing(wanted)


def check_plan_symbols(
    items: Sequence[PlanItem], corpus: str | SymbolIndex, where: str
) -> list[Finding]:
    """Checked items may only name symbols that exist in the code."""
    checked = [(item, symbol_candidates(item.text)) for item in items if item.checked]
    missing = _missing_names(
        (name for _, names in checked for name in names), corpus
    )
    findings: list[Finding] = []
    for item, names in checked:
        for name in names:
            if name not in missing:
                continue
            findings.append(
                Finding(
                    "error",
                    "symbols",
                    f"{where}:{item.line}",
                    f"checked item names `{name}`, which is not in the code",
                )
            )
    return findings


# --------------------------------------------------------------------------
# references


def check_commit_refs(
    documents: Iterable[tuple[str, str]],
    repos: dict[str, Path],
    primary: str | None,
    git: Sequence[str] = ("git",),
) -> list[Finding]:
    """Every ``repo@hash`` and bare ``@hash`` must resolve to a commit."""
    findings: list[Finding] = []
    for where, text in documents:
        for match in _COMMIT.finditer(text):
            repo_name, ref = match.group(1), match.group(2)
            name = repo_name or primary
            path = repos.get(name) if name else None
            if path is None:
                findings.append(
                    Finding(
                        "warn",
                        "commits",
                        where,
                        f"@{ref}: repository {name or '(primary)'} is not configured",
                    )
                )
                continue
            result = subprocess.run(
                [*git, "-C", str(path), "cat-file", "-e", f"{ref}^{{commit}}"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                findings.append(
                    Finding(
                        "error",
                        "commits",
                        where,
                        f"{name}@{ref} does not resolve to a commit",
                    )
                )
    return findings


def _dedupe_roots(roots: Sequence[Path]) -> list[Path]:
    """Drop repeated roots and roots nested inside an earlier one."""
    kept: list[Path] = []
    for root in sorted((path.resolve() for path in roots), key=lambda path: len(path.parts)):
        if any(root == other or other in root.parents for other in kept):
            continue
        kept.append(root)
    return kept


def index_files(roots: Sequence[Path]) -> dict[str, list[Path]]:
    index: dict[str, list[Path]] = {}
    for root in _dedupe_roots(roots):
        for path in _iter_files(root):
            index.setdefault(path.name, []).append(path)
    return index


def resolve_file(ref: str, index: dict[str, list[Path]]) -> tuple[Path | None, bool]:
    """Return the file and whether the choice was ambiguous."""
    candidates = index.get(Path(ref.replace("\\", "/")).name, [])
    if not candidates:
        return None, False
    if len(candidates) == 1:
        return candidates[0], False
    wanted = ref.replace("\\", "/").lstrip("./")
    exact = [path for path in candidates if path.as_posix().endswith(wanted)]
    if len(exact) == 1:
        return exact[0], False
    return None, True


def check_file_refs(
    documents: Iterable[tuple[str, str]], index: dict[str, list[Path]]
) -> list[Finding]:
    findings: list[Finding] = []
    for where, text in documents:
        for match in _FILE_REF.finditer(text):
            ref = match.group("path")
            start = int(match.group("start"))
            end = int(match.group("end") or start)
            path, ambiguous = resolve_file(ref, index)
            if ambiguous:
                findings.append(Finding("warn", "paths", where, f"{ref}: several files match"))
                continue
            if path is None:
                findings.append(
                    Finding("warn", "paths", where, f"{ref}: file not found in the configured roots")
                )
                continue
            total = len(read_text(path).splitlines())
            if end > total:
                findings.append(
                    Finding(
                        "warn",
                        "paths",
                        where,
                        f"{ref}:{match.group('start')} points past the end of {path.name} ({total} lines)",
                    )
                )
    return findings


# --------------------------------------------------------------------------
# journals


@dataclass(frozen=True, slots=True)
class Token:
    """A decision or problem number: ``D7`` in the root, ``Dtc.3`` on a stage."""

    kind: str      # "D" or "P"
    code: str      # "" for the root journal
    number: int

    @property
    def text(self) -> str:
        return f"{self.kind}{self.code}{'.' if self.code else ''}{self.number}"


@dataclass(slots=True)
class Journal:
    """One journal file: its kind, the stage code it is scoped to, and its text."""

    kind: str
    code: str      # "" for the root, the stage code for a stage, "#archive" for history
    path: str      # path relative to the spec folder
    text: str


def parse_token(text: str) -> Token | None:
    match = _TOKEN_TEXT.fullmatch(text.strip().strip("`"))
    if match is None:
        return None
    kind, code, number = match.groups()
    return Token(kind, code or "", int(number))


def tokens_in(text: str) -> set[Token]:
    """Every number mentioned in a piece of text."""
    return {
        Token(kind, code or "", int(number))
        for kind, code, number in _REFERENCE.findall(text)
    }


def record_spans(text: str, kind: str) -> list[tuple[Token, str]]:
    """Records of one journal: heading token and the body that follows it."""
    matches = [match for match in _RECORD.finditer(text) if match.group(1) == kind]
    spans: list[tuple[Token, str]] = []
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        token = Token(kind, match.group(2) or "", int(match.group(3)))
        spans.append((token, text[match.end() : end]))
    return spans


def active_summary(decisions: str) -> str | None:
    """Text of the active-decisions summary: from its heading to the next ``## ``.

    The summary may be a line per decision or a thematic digest that names the
    records in brackets; only the numbers matter, so the form is not fixed.
    """
    match = _ACTIVE_HEADING.search(decisions)
    if match is None:
        return None
    rest = decisions[match.end() :]
    following = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: following.start()] if following else rest


def section(text: str, heading: re.Pattern[str]) -> str | None:
    """Body of a section, from its heading to the next ``## ``."""
    match = heading.search(text)
    if match is None:
        return None
    rest = text[match.end() :]
    following = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: following.start()] if following else rest


def indexed_decisions(decisions: str, mode: str) -> tuple[set[int] | None, str | None]:
    """Numbers the summary points at, plus a note when there is no summary.

    ``mode`` is ``summary`` (numbers anywhere in the summary), ``strict`` (one
    ``- **D<n>**`` line per record) or ``off``. ``None`` means "do not check".
    """
    if mode == "off":
        return None, None
    if mode == "strict":
        tokens = (parse_token(match) for match in _DECISION_INDEX.findall(decisions))
        return {token.number for token in tokens if token and not token.code}, None
    summary = active_summary(decisions)
    if summary is None:
        return None, "no active decisions summary, the index is not checked"
    # The root summary indexes root records only: a coded number here is a
    # stage record and is checked against its own journal.
    return {
        token.number
        for token in tokens_in(summary)
        if token.kind == "D" and not token.code
    }, None


def absorbed_decisions(decisions: str) -> set[int]:
    """Tombstones: records merged into another one, so the summary names the target."""
    return {
        token.number
        for token, body in record_spans(decisions, "D")
        if _TOMBSTONE.search(body)
    }


def check_journal_numbering(
    journals: Sequence[Journal],
    index_mode: str = "summary",
) -> list[Finding]:
    """Numbers are unique per task folder; a stage journal carries its stage code."""
    findings: list[Finding] = []
    seen: dict[str, str] = {}
    groups: dict[tuple[str, str], list[int]] = {}
    group_where: dict[tuple[str, str], str] = {}
    retired = {(token.kind, token.code, token.number) for token in moved_tokens(journals)}
    for journal in journals:
        for token, _body in record_spans(journal.text, journal.kind):
            if journal.code == "" and token.code:
                findings.append(
                    Finding(
                        "error",
                        "numbering",
                        journal.path,
                        f"{token.text} carries a stage code, the root journal keeps bare numbers",
                    )
                )
            elif journal.code not in ("", "#archive") and token.code != journal.code:
                expected = f"{journal.kind}{journal.code}.{token.number}"
                findings.append(
                    Finding(
                        "error",
                        "numbering",
                        journal.path,
                        f"{token.text} must be written {expected}: a stage journal always carries its code",
                    )
                )
            if token.text in seen:
                findings.append(
                    Finding(
                        "error",
                        "numbering",
                        journal.path,
                        f"{token.text} is declared twice ({seen[token.text]})",
                    )
                )
            else:
                seen[token.text] = journal.path
            scope_matches = (
                token.code == journal.code
                if journal.code not in ("", "#archive")
                else (journal.code == "#archive" or not token.code)
            )
            if scope_matches:
                group = (journal.kind, token.code)
                groups.setdefault(group, []).append(token.number)
                group_where.setdefault(group, journal.path)

    # The archive keeps the numbers a record arrived with, so a gap is counted
    # across the live journal and the archive of the same scope.
    for (kind, code), numbers in sorted(groups.items()):
        # A number that moved out is not missing, and it does not extend the
        # range: the counter is judged by the records that still live there.
        retired_here = {
            number for kind_, code_, number in retired if (kind_, code_) == (kind, code)
        }
        gaps = sorted(
            set(range(1, max(numbers, default=0) + 1)) - set(numbers) - retired_here
        )
        prefix = f"{kind}{code}{'.' if code else ''}"
        for number in gaps:
            findings.append(
                Finding(
                    "warn",
                    "numbering",
                    group_where[(kind, code)],
                    f"{prefix}{number} is missing from the sequence",
                )
            )

    root = next(
        (journal for journal in journals if journal.kind == "D" and journal.code == ""),
        None,
    )
    if root is None:
        return findings
    listed, note = indexed_decisions(root.text, index_mode)
    declared_root = {
        token.number for token, _ in record_spans(root.text, "D")
    }
    if note is not None and declared_root:
        findings.append(Finding("warn", "numbering", root.path, note))
    if listed is not None:
        absorbed = absorbed_decisions(root.text)
        for number in sorted(listed - declared_root):
            findings.append(
                Finding(
                    "error",
                    "numbering",
                    root.path,
                    f"index lists D{number}, which has no record",
                )
            )
        for number in sorted(declared_root - listed - absorbed):
            findings.append(
                Finding(
                    "error",
                    "numbering",
                    root.path,
                    f"D{number} is missing from the active index",
                )
            )
    return findings


def check_references(
    documents: Iterable[tuple[str, str]],
    journals: Sequence[Journal],
    moved: Iterable[Token] = (),
) -> list[Finding]:
    """Every number mentioned in the folder resolves to a record or a transfer."""
    declared = {
        token
        for journal in journals
        for token, _ in record_spans(journal.text, journal.kind)
    }
    allowed = declared | set(moved)
    findings: list[Finding] = []
    for where, text in documents:
        for token in sorted(tokens_in(text), key=lambda item: item.text):
            if token not in allowed:
                findings.append(
                    Finding(
                        "error",
                        "numbering",
                        where,
                        f"refers to {token.text}, which is not declared",
                    )
                )
    return findings


def transfer_lines(journal: Journal) -> list[tuple[Token, Token, str | None]]:
    """Transfers written in a journal: the old token, the new one, the target path.

    The path is omitted when the record moved to the root journal.
    """
    body = section(journal.text, _MOVED_HEADING)
    if not body:
        return []
    lines: list[tuple[Token, Token, str | None]] = []
    for old_text, new_text, path in _MOVED_LINE.findall(body):
        old, new = parse_token(old_text), parse_token(new_text)
        if old is None or new is None:
            continue
        target = path.strip().strip("`").rstrip(".,;") if path else ""
        lines.append((old, new, target or None))
    return lines


def moved_tokens(journals: Sequence[Journal]) -> set[Token]:
    """Old numbers named in a transfer section, wherever the section lives."""
    return {
        old
        for journal in journals
        for old, _new, _path in transfer_lines(journal)
    }


def check_transfers(journals: Sequence[Journal]) -> list[Finding]:
    """A moved record and its old number are named on both sides."""
    findings: list[Finding] = []
    roots = {
        journal.kind: journal for journal in journals if journal.code == ""
    }
    by_path = {journal.path: journal for journal in journals}
    pairs: dict[str, str] = {}
    for journal in journals:
        for old, new, path in transfer_lines(journal):
            target = by_path.get(path) if path else roots.get(new.kind)
            if target is None:
                findings.append(
                    Finding(
                        "error",
                        "numbering",
                        journal.path,
                        f"transfer {old.text} → {new.text} points to "
                        f"{path or 'the root journal'}, which does not exist",
                    )
                )
                continue
            pairs[old.text] = new.text
            records = {
                token.text: body for token, body in record_spans(target.text, target.kind)
            }
            if new.text not in records:
                findings.append(
                    Finding(
                        "error",
                        "numbering",
                        target.path,
                        f"transfer {old.text} → {new.text} names a record that is not here",
                    )
                )
                continue
            was = _BY_WAS.search(records[new.text])
            if was is None or was.group(1) != old.text:
                findings.append(
                    Finding(
                        "error",
                        "numbering",
                        target.path,
                        f"{new.text} has no '**Было:** {old.text}' line",
                    )
                )

    for journal in journals:
        for token, body_text in record_spans(journal.text, journal.kind):
            was = _BY_WAS.search(body_text)
            if was is None:
                continue
            if pairs.get(was.group(1)) != token.text:
                findings.append(
                    Finding(
                        "error",
                        "numbering",
                        journal.path,
                        f"{token.text} says it was moved from {was.group(1)}, "
                        "but no transfer line says so",
                    )
                )
    return findings


def check_rules_links(journals: Sequence[Journal], spec: Path) -> list[Finding]:
    """``**Правило:**`` in a record and ``Источники`` in ``rules/`` point at each other."""
    findings: list[Finding] = []
    named: dict[str, set[str]] = {}
    for journal in journals:
        for token, body in record_spans(journal.text, journal.kind):
            match = _RULE_REF.search(body)
            if match is None:
                continue
            rule = match.group(1)
            named.setdefault(rule, set()).add(token.text)
            if not (spec / rule).is_file():
                findings.append(
                    Finding(
                        "error",
                        "numbering",
                        journal.path,
                        f"{token.text} names {rule}, which does not exist",
                    )
                )

    for rule, tokens in sorted(named.items()):
        path = spec / rule
        if not path.is_file():
            continue
        sources = section(read_text(path), _SOURCES_HEADING)
        listed = {token.text for token in tokens_in(sources or "")}
        for token in sorted(tokens - listed):
            findings.append(
                Finding(
                    "error",
                    "numbering",
                    rule,
                    f"{token} names this rule, but it is not listed in Источники",
                )
            )

    rules_dir = spec / "rules"
    if rules_dir.is_dir():
        for path in sorted(rules_dir.glob("*.md")):
            relative = f"rules/{path.name}"
            sources = section(read_text(path), _SOURCES_HEADING)
            for token in sorted(tokens_in(sources or ""), key=lambda item: item.text):
                if token.text in named.get(relative, set()):
                    continue
                findings.append(
                    Finding(
                        "error",
                        "numbering",
                        relative,
                        f"Источники list {token.text}, but the record does not name this rule",
                    )
                )
    return findings


def check_numbering(
    decisions: str,
    problems: str,
    mentioned: Iterable[tuple[str, str]],
    index_mode: str = "summary",
) -> list[Finding]:
    """Both journals of a bare-numbered task folder: numbers are unique across both."""
    journals = []
    if decisions:
        journals.append(Journal("D", "", "decisions.md", decisions))
    if problems:
        journals.append(Journal("P", "", "problems.md", problems))
    findings = check_journal_numbering(journals, index_mode)
    findings += check_references(mentioned, journals)
    return findings



# --------------------------------------------------------------------------
# shortcuts and todo


_PARAGRAPH_LINES = 20


def _is_comment_line(line: str) -> bool:
    """Whether the line is a comment continuation, not code or a string."""
    stripped = line.lstrip()
    return stripped.startswith(("#", "//", "/*", "*"))


def _join_comment(lines: Sequence[str], start: int) -> str:
    """Marker text: the marker line and the rest of its paragraph.

    The paragraph runs to a blank line, to the next marker or to a non-comment
    line: a neighbouring shortcut or a code line must not lend the marker a
    return condition, and the text is not limited to the first lines.
    """
    parts = []
    for offset, line in enumerate(lines[start : start + _PARAGRAPH_LINES]):
        if offset:
            if _SHORTCUT.search(line) or not _is_comment_line(line):
                break
        stripped = _clean_comment(line)
        if not stripped:
            break
        parts.append(stripped)
    return " ".join(parts)


def _clean_comment(line: str) -> str:
    """Strip Python, C++ and block-comment decoration from one line."""
    return line.strip().lstrip("/*").lstrip("#").strip().rstrip("*/").strip()


def shortcut_complete(text: str) -> bool:
    """A shortcut must say when to come back: that is what keeps it alive.

    The limit itself may be qualitative ("only plain http"), so the gate checks
    the return trigger instead of a number.
    """
    lowered = text.lower()
    return any(
        word in lowered
        for word in (
            "revisit",
            "switch to",
            "when ",
            "until ",
            "if ",
            "когда",
            "если",
            "вернуться",
            "до ",
        )
    )


def _is_test_file(path: Path) -> bool:
    """Markers document production compromises; examples in tests are not markers."""
    return "tests" in path.parts or path.name.lower().startswith("test")


def check_shortcuts(files: Iterable[Path], display) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        if _is_test_file(path):
            continue
        lines = read_text(path).splitlines()
        for number, line in enumerate(lines):
            if not _SHORTCUT.search(line):
                continue
            marker = _join_comment(lines, number)
            if not shortcut_complete(marker):
                findings.append(
                    Finding(
                        "warn",
                        "shortcuts",
                        f"{display(path)}:{number + 1}",
                        "shortcut states no limit or no return condition",
                    )
                )
    return findings


def check_todo(text: str, limit: int, where: str = "todo.md") -> list[Finding]:
    count = len(re.findall(r"^\s*- \[[ xX]\]", text, re.MULTILINE))
    if count > limit:
        return [
            Finding(
                "warn",
                "todo",
                where,
                f"{count} actions, cap is {limit}: either a stage or a dump",
            )
        ]
    return []


# --------------------------------------------------------------------------
# runner


@dataclass(slots=True)
class Context:
    projects_root: Path
    spec: Path
    repos: dict[str, Path]
    primary: str | None
    code_roots: list[Path]
    file_roots: list[Path]

    def display(self, path: Path) -> str:
        try:
            return path.relative_to(self.projects_root).as_posix()
        except ValueError:
            return path.as_posix()

    def spec_documents(self) -> list[tuple[str, str]]:
        paths = (
            sorted(self.spec.glob("*.md"))
            + sorted(self.spec.glob("stages/*.md"))
            + sorted(self.spec.glob("stages/*/*.md"))
            + sorted(self.spec.glob("rules/*.md"))
        )
        return [
            (path.relative_to(self.spec).as_posix(), read_text(path))
            for path in paths
            if path.is_file()
        ]


def parse_frontmatter(text: str) -> dict[str, object]:
    """Read the YAML-ish front matter of ``index.md``; lists and scalars only."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else ""
    out: dict[str, object] = {}
    for line in block.splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            out[key] = [
                item.strip().strip("'\"") for item in inner.split(",") if item.strip()
            ]
        elif value:
            out[key] = value.strip("'\"")
        else:
            out[key] = None
    return out


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    if isinstance(value, str) and value:
        return [value]
    return []


def load_context(
    spec: Path,
    projects_root: Path,
    overrides: dict[str, Path] | None = None,
    extra_roots: Sequence[Path] = (),
) -> Context:
    overrides = overrides or {}
    index = spec / "index.md"
    front = parse_frontmatter(read_text(index)) if index.is_file() else {}

    repo_names = _as_list(front.get("repos"))
    repos: dict[str, Path] = {}
    for entry in repo_names:
        # `<name>`, `<path>` (submodule: name is the last segment) or `<name>=<path>`.
        raw_name, separator, raw_path = entry.partition("=")
        if separator and raw_path.strip():
            name, relative = raw_name.strip(), raw_path.strip()
        else:
            relative = entry.strip()
            name = Path(relative).name
        if not name or not relative:
            continue
        repos[name] = Path(overrides.get(name) or (projects_root / relative)).resolve()

    configured = _as_list(front.get("code_roots"))
    code_roots: list[Path] = []
    if configured:
        # Paths are relative to PROJECTS_ROOT, same as `repos:` (beta/Sources).
        code_roots = [
            Path(entry) if Path(entry).is_absolute() else projects_root / entry
            for entry in configured
        ]
    else:
        for repo in repos.values():
            subs = [repo / sub for sub in ("src", "tests") if (repo / sub).is_dir()]
            code_roots.extend(subs or [repo])

    file_roots = list(code_roots) + [spec.resolve()]
    for raw in _as_list(front.get("ref_roots")):
        path = Path(raw)
        file_roots.append(path if path.is_absolute() else (projects_root / path))
    file_roots.extend(Path(path) for path in extra_roots)

    return Context(
        projects_root=projects_root.resolve(),
        spec=spec.resolve(),
        repos=repos,
        primary=next(iter(repos), None),
        code_roots=_dedupe_roots(code_roots),
        file_roots=_dedupe_roots(file_roots),
    )


def plan_documents(spec: Path) -> list[Path]:
    """Where checked items live: ``plan.md`` and the stage cards."""
    paths: list[Path] = []
    plan = spec / "plan.md"
    if plan.is_file():
        paths.append(plan)
    paths.extend(sorted(path for path in (spec / "stages").glob("*.md") if path.is_file()))
    paths.extend(
        sorted(path for path in (spec / "stages").glob("*/stage.md") if path.is_file())
    )
    return paths


def stage_codes(spec: Path) -> tuple[dict[str, str], list[Finding]]:
    """Stage folder → its code, plus what is wrong with the codes."""
    codes: dict[str, str] = {}
    findings: list[Finding] = []
    stages = spec / "stages"
    if not stages.is_dir():
        return codes, findings
    for folder in sorted(path for path in stages.iterdir() if path.is_dir()):
        relative = folder.relative_to(spec).as_posix()
        card = folder / "stage.md"
        has_journal = any((folder / name).is_file() for name in ("decisions.md", "problems.md"))
        if not card.is_file():
            if has_journal:
                findings.append(
                    Finding(
                        "error",
                        "numbering",
                        relative,
                        "stage folder has journals but no stage.md with a code",
                    )
                )
            continue
        match = _CODE_DECL.search(read_text(card))
        code = match.group(1).strip("`") if match else ""
        if not code:
            if has_journal:
                findings.append(
                    Finding(
                        "error",
                        "numbering",
                        f"{relative}/stage.md",
                        "stage journals need a code: add '**Код:** <code>' to the card",
                    )
                )
            continue
        if not _CODE_TEXT.fullmatch(code):
            findings.append(
                Finding(
                    "error",
                    "numbering",
                    f"{relative}/stage.md",
                    f"code `{code}` must be 2-8 lowercase latin letters",
                )
            )
        if code in codes.values():
            findings.append(
                Finding(
                    "error",
                    "numbering",
                    f"{relative}/stage.md",
                    f"code `{code}` is already used by another stage",
                )
            )
        codes[relative] = code
    return codes, findings


def collect_journals(spec: Path) -> tuple[list[Journal], list[Finding]]:
    """Every journal of the task folder: root, stage and the problem archive."""
    codes, findings = stage_codes(spec)
    journals: list[Journal] = []
    for kind, name in (("D", "decisions.md"), ("P", "problems.md")):
        path = spec / name
        if path.is_file():
            journals.append(Journal(kind, "", name, read_text(path)))
    for relative, code in sorted(codes.items()):
        for kind, name in (("D", "decisions.md"), ("P", "problems.md")):
            path = spec / relative / name
            if path.is_file():
                journals.append(
                    Journal(kind, code, f"{relative}/{name}", read_text(path))
                )
    archive = spec / "archive"
    if archive.is_dir():
        for path in sorted(archive.glob("problems*.md")):
            if not path.is_file():
                continue
            journals.append(
                Journal(
                    "P",
                    "#archive",
                    path.relative_to(spec).as_posix(),
                    read_text(path),
                )
            )
    return journals, findings


def has_codes(journals: Sequence[Journal]) -> bool:
    """Whether the folder uses stage codes; the old layout has none."""
    return any(journal.code not in ("", "#archive") for journal in journals)


def run_checks(context: Context, *, index_mode: str = "summary") -> list[Finding]:
    findings: list[Finding] = []
    documents = context.spec_documents()
    symbols = SymbolIndex(context.code_roots)

    for plan_path in plan_documents(context.spec):
        where = plan_path.relative_to(context.spec).as_posix()
        items = parse_plan(read_text(plan_path))
        findings += check_plan_symbols(items, symbols, where)

    findings += check_commit_refs(documents, context.repos, context.primary)
    findings += check_file_refs(documents, index_files(context.file_roots))

    journals, journal_findings = collect_journals(context.spec)
    findings += journal_findings
    findings += check_journal_numbering(journals, index_mode=index_mode)
    findings += check_references(documents, journals, moved=moved_tokens(journals))
    if has_codes(journals):
        findings += check_transfers(journals)
        findings += check_rules_links(journals, context.spec)

    findings += check_shortcuts(
        [
            path
            for root in context.code_roots
            for path in _iter_files(root)
            if path.suffix.lower() in _CODE_SUFFIXES
        ],
        context.display,
    )

    todo = context.spec / "todo.md"
    if todo.is_file():
        findings += check_todo(read_text(todo), limit=15)
    return findings


def default_projects_root() -> Path:
    """PROJECTS_ROOT, else the parent of SPECS_ROOT, else ~/projects."""
    env = os.environ.get("PROJECTS_ROOT")
    if env:
        return Path(env)
    specs = os.environ.get("SPECS_ROOT")
    if specs:
        return Path(specs).parent
    return Path.home() / "projects"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a spec folder against the code.")
    parser.add_argument("--spec", type=Path, required=True, help="path to the task folder")
    parser.add_argument(
        "--projects-root",
        type=Path,
        default=default_projects_root(),
        help="where <name> from repos: lives (default: PROJECTS_ROOT, "
        "else parent of SPECS_ROOT, else ~/projects)",
    )
    parser.add_argument("--repo", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--file-root", action="append", default=[], metavar="PATH")
    parser.add_argument("--strict", action="store_true", help="warnings fail the gate")
    parser.add_argument(
        "--index",
        dest="index_mode",
        choices=("summary", "strict", "off"),
        default="summary",
        help="how decisions.md must index its records: summary (any mention in the "
        "active decisions section, default), strict (one '- **D<n>**' line per "
        "record) or off (do not check)",
    )
    args = parser.parse_args(argv)

    if not args.spec.is_dir():
        print(f"spec folder not found: {args.spec}", file=sys.stderr)
        return 2

    overrides = {}
    for item in args.repo:
        name, _, path = item.partition("=")
        if name and path:
            overrides[name] = Path(path)

    context = load_context(
        args.spec,
        args.projects_root,
        overrides=overrides,
        extra_roots=[Path(path) for path in args.file_root],
    )
    findings = run_checks(
        context,
        index_mode=args.index_mode,
    )

    for finding in sorted(findings, key=lambda item: (item.level != "error", item.check, item.where)):
        print(finding.render())

    errors = sum(1 for finding in findings if finding.level == "error")
    warnings = len(findings) - errors
    print(f"\n{errors} error(s), {warnings} warning(s)")
    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
