"""Deterministic gate for the spec: form, not meaning.

The gate answers one question: can the claims made in a spec folder be resolved
against the code repositories as they are now? It does not judge whether a
feature is worth having; that is the review role.

Checks:

- ``symbols``   a checked plan item names a class or attribute that does not
  exist in the code (a stale name after a rename);
- ``commits``   every ``repo@hash`` and bare ``@hash`` resolves to a commit;
- ``paths``     every ``file.py:NN`` reference points inside an existing file;
- ``numbering`` decision and problem numbers are unique, the decision index
  lists every record exactly once, and every ``D<n>``/``P<n>`` mentioned in the
  folder exists. The index may be a line per decision or a thematic digest that
  names the numbers; ``--index`` sets how strict that check is;
- ``shortcuts`` every ``shortcut:`` marker states a limit and a return
  condition;
- ``todo``      ``todo.md`` stays under the cap;
- ``tests``     the repositories' own test suites pass.

The spec folder is the input. Repository paths come from the ``repos:`` field of
``index.md`` and the convention ``<projects-root>/<name>``; an entry may be a
path, so submodules work as ``<name>=<path>`` or ``sub/module`` (the name is the
last segment); ``ref_roots:`` adds extra locations for ``file.py:NN`` references
outside the repositories.

``<projects-root>`` is ``PROJECTS_ROOT``, else the parent of ``SPECS_ROOT``,
else ``~/projects``.

Usage:

    python3 scripts/spec_check.py --spec <SPECS_ROOT>/tasks/<folder>
    python3 scripts/spec_check.py --spec <folder> --strict
    python3 scripts/spec_check.py --spec <folder> --no-tests --file-root <extra>
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
from typing import Iterable, Sequence

_ARTIFACT_SUFFIXES = (
    ".py", ".json", ".jsonl", ".md", ".toml", ".jsonc", ".txt", ".yml", ".yaml", ".sh",
)

_ITEM = re.compile(r"^(\s*)- \[([ xX])\] (.*)$")
_SPAN = re.compile(r"`([^`]+)`")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_COMMIT = re.compile(r"(?<![A-Za-z0-9_])(?:([A-Za-z][A-Za-z0-9_.-]*))?@([0-9a-f]{7,40})\b")
_FILE_REF = re.compile(
    r"(?P<path>[A-Za-z0-9_][\w./\\-]*\.(?:py|json|jsonl|md|toml|jsonc|ts|ps1))"
    r":(?P<start>\d+)(?:-(?P<end>\d+))?"
)
_DECISION = re.compile(r"^## (D)(\d+)\.", re.MULTILINE)
_PROBLEM = re.compile(r"^## (P)(\d+)\.", re.MULTILINE)
_TOMBSTONE = re.compile(
    r"\*\*Статус\*\*:?\s*(?:объединено|влито|поглощено|absorbed|merged)"
    r"|—\s*влито в D\d+"
    r"|absorbed into D\d+"
    r"|merged into D\d+",
    re.IGNORECASE,
)
_DECISION_INDEX = re.compile(r"^- \*\*(D)(\d+)\*\*", re.MULTILINE)
_ACTIVE_HEADING = re.compile(
    r"^##[ \t]+(?:Действующие решения|Active decisions)[ \t]*$", re.MULTILINE | re.IGNORECASE
)
_REFERENCE = re.compile(r"\b([DP])(\d{1,3})\b")
_SHORTCUT = re.compile(r"shortcut:\s*(.+)", re.IGNORECASE)

_PYTEST_MARKERS = ("pyproject.toml", "pytest.ini", "requirements-dev.txt", "tox.ini")


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


def symbol_candidates(text: str) -> list[str]:
    """Names in backticks that must exist in the code.

    Artifacts (``snapshot.json``), paths (``core.py``), endpoints and anything
    with a separator are skipped: only class-like names and dotted attributes
    are checked.
    """
    names: list[str] = []
    for span in _SPAN.findall(text):
        span = span.strip()
        if any(char in span for char in "/() :→,"):
            continue
        if span.lower().endswith(_ARTIFACT_SUFFIXES):
            continue
        if _IDENTIFIER.fullmatch(span):
            if re.search(r"[A-Z]", span):
                names.append(span)
        elif _DOTTED.fullmatch(span):
            names.append(span.rsplit(".", 1)[1])
    return names


def check_plan_symbols(items: Sequence[PlanItem], corpus: str, where: str) -> list[Finding]:
    findings: list[Finding] = []
    for item in items:
        if not item.checked:
            continue
        for name in symbol_candidates(item.text):
            if not re.search(rf"\b{re.escape(name)}\b", corpus):
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
        if root.is_file():
            index.setdefault(root.name, []).append(root)
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
                continue
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
# journal numbering


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


def indexed_decisions(decisions: str, mode: str) -> tuple[set[int] | None, str | None]:
    """Numbers the summary points at, plus a note when there is no summary.

    ``mode`` is ``summary`` (numbers anywhere in the summary), ``strict`` (one
    ``- **D<n>**`` line per record) or ``off``. ``None`` means "do not check".
    """
    if mode == "off":
        return None, None
    if mode == "strict":
        return {int(number) for _, number in _DECISION_INDEX.findall(decisions)}, None
    summary = active_summary(decisions)
    if summary is None:
        return None, "no active decisions summary, the index is not checked"
    return {int(number) for kind, number in _REFERENCE.findall(summary) if kind == "D"}, None


def absorbed_decisions(decisions: str) -> set[int]:
    """Tombstones: records merged into another one, so the summary names the target."""
    absorbed: set[int] = set()
    headings = list(_DECISION.finditer(decisions))
    for position, heading in enumerate(headings):
        end = headings[position + 1].start() if position + 1 < len(headings) else len(decisions)
        if _TOMBSTONE.search(decisions[heading.end() : end]):
            absorbed.add(int(heading.group(2)))
    return absorbed


def check_numbering(
    decisions: str,
    problems: str,
    mentioned: Iterable[tuple[str, str]],
    index_mode: str = "summary",
) -> list[Finding]:
    findings: list[Finding] = []
    declared: dict[str, set[int]] = {
        "D": {int(number) for _, number in _DECISION.findall(decisions) or []},
        "P": {int(number) for _, number in _PROBLEM.findall(problems) or []},
    }
    for kind, text, source in (("D", decisions, "decisions.md"), ("P", problems, "problems.md")):
        numbers = [
            int(number)
            for number in re.findall(rf"^## {kind}(\d+)\.", text, re.MULTILINE)
        ]
        duplicates = sorted({number for number in numbers if numbers.count(number) > 1})
        for number in duplicates:
            findings.append(
                Finding("error", "numbering", source, f"{kind}{number} is declared twice")
            )
        gaps = sorted(set(range(1, max(numbers, default=0) + 1)) - set(numbers))
        for number in gaps:
            findings.append(
                Finding("warn", "numbering", source, f"{kind}{number} is missing from the sequence")
            )

    listed, note = indexed_decisions(decisions, index_mode)
    if note is not None and declared["D"]:
        findings.append(Finding("warn", "numbering", "decisions.md", note))
    if listed is not None:
        absorbed = absorbed_decisions(decisions)
        for number in sorted(listed - declared["D"]):
            findings.append(
                Finding("error", "numbering", "decisions.md", f"index lists D{number}, which has no record")
            )
        for number in sorted(declared["D"] - listed - absorbed):
            findings.append(
                Finding("error", "numbering", "decisions.md", f"D{number} is missing from the active index")
            )

    for where, text in mentioned:
        for kind, number in _REFERENCE.findall(text):
            if int(number) not in declared[kind]:
                findings.append(
                    Finding(
                        "error",
                        "numbering",
                        where,
                        f"refers to {kind}{number}, which is not declared",
                    )
                )
    return findings


# --------------------------------------------------------------------------
# shortcuts and todo


def _join_comment(lines: Sequence[str], start: int) -> str:
    """Marker text: the marker line and up to two lines of its paragraph."""
    parts = []
    for line in lines[start : start + 3]:
        stripped = line.strip().lstrip("#").strip()
        if not stripped:
            break
        parts.append(stripped)
    return " ".join(parts)


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
    return "tests" in path.parts or path.name.startswith("test_")


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
        return [(path.name, read_text(path)) for path in sorted(self.spec.glob("*.md"))]


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

    code_roots: list[Path] = []
    for name, repo in repos.items():
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


def _is_python_repo(repo: Path) -> bool:
    return (repo / "tests").is_dir() and any((repo / marker).is_file() for marker in _PYTEST_MARKERS)


def run_checks(
    context: Context,
    *,
    with_tests: bool = True,
    python: str | None = None,
    index_mode: str = "summary",
) -> list[Finding]:
    python = python or sys.executable
    findings: list[Finding] = []
    documents = context.spec_documents()

    plan_path = context.spec / "plan.md"
    if plan_path.is_file():
        corpus = "\n".join(
            read_text(path)
            for root in context.code_roots
            if root.is_dir()
            for path in root.rglob("*.py")
        )
        findings += check_plan_symbols(parse_plan(read_text(plan_path)), corpus, "plan.md")

    findings += check_commit_refs(documents, context.repos, context.primary)

    index = index_files(context.file_roots)
    findings += check_file_refs(documents, index)

    decisions = context.spec / "decisions.md"
    problems = context.spec / "problems.md"
    findings += check_numbering(
        read_text(decisions) if decisions.is_file() else "",
        read_text(problems) if problems.is_file() else "",
        documents,
        index_mode=index_mode,
    )

    findings += check_shortcuts(
        [path for root in context.code_roots if root.is_dir() for path in root.rglob("*.py")],
        context.display,
    )

    todo = context.spec / "todo.md"
    if todo.is_file():
        findings += check_todo(read_text(todo), limit=15)

    if with_tests:
        for name, repo in context.repos.items():
            if not _is_python_repo(repo):
                continue
            result = subprocess.run(
                [python, "-m", "pytest", "-q"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
            if result.returncode != 0:
                tail = (result.stdout + result.stderr).strip().splitlines()[-6:]
                findings.append(
                    Finding("error", "tests", name, "suite is red: " + " / ".join(tail))
                )
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
    parser.add_argument("--no-tests", action="store_true", help="skip the test suites")
    parser.add_argument(
        "--python",
        default=None,
        help="python interpreter for pytest (default: the one running this script)",
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
        with_tests=not args.no_tests,
        python=args.python,
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
