"""Repo-wide lint for a specs repository: form and bookkeeping, not meaning.

The per-task gate (`spec_check.py`) answers "do the claims of this spec resolve
against the code?". This linter answers a cheaper question: "is the repository
kept the way the rules say?". It reads no code repositories, so it is fast enough
to run on every push.

Checks:

- ``frontmatter`` the required keys of ``index.md``, the allowed ``kind`` and
  ``status``, ISO dates, and ``ids`` that include ``task``;
- ``layout``     the task folder name, a ``stage.md`` in every stage folder, the
  stage slug, and that every stage link in ``index.md`` resolves;
- ``tasks``      ``TASKS.md`` and the folders agree in both directions: one row
  per folder, and the row's ids, status and date match ``index.md``;
- ``numbering``  the same journal checks as the gate: unique numbers, declared
  references, two-sided transfers, and ``**Правило:**`` matching ``Источники``;
- ``todo``       ``todo.md`` stays under the cap;
- ``closure``    a ``done`` task has a filled ``Итог``;
- ``plan``       ``plan.md`` keeps order and risks, not checkboxes.

With ``--base <ref>`` only the tasks touched by ``<ref>..HEAD`` are checked: a
broken task that someone else pushed does not block your own push. Without it
the whole repository is checked, which is what CI and manual runs want.

Errors fail the run; warnings are printed. Exit code: 0 clean, 1 errors,
2 usage.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import spec_check  # noqa: E402

Finding = spec_check.Finding

REQUIRED = ("task", "ids", "kind", "status", "created", "updated")
KINDS = {"project", "problem", "research"}
STATUSES = {"draft", "approved", "in-progress", "done", "cancelled", "shelved"}
WORD_ID = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAGE_LINK = re.compile(r"(?:\]\(|`)(stages/[^)\s`]+)(?:\)|`)")
CHECKBOX = re.compile(r"^\s*-\s*\[[ xX]\]", re.MULTILINE)
ROW = re.compile(r"^\|(.+)\|\s*$", re.MULTILINE)


def task_documents(task: Path) -> list[tuple[str, str]]:
    paths = (
        sorted(task.glob("*.md"))
        + sorted(task.glob("stages/*.md"))
        + sorted(task.glob("stages/*/*.md"))
        + sorted(task.glob("rules/*.md"))
    )
    return [
        (path.relative_to(task).as_posix(), path.read_text(encoding="utf-8"))
        for path in paths
        if path.is_file()
    ]


def check_frontmatter(task: Path, front: dict[str, object]) -> list[Finding]:
    findings: list[Finding] = []
    where = "index.md"
    for key in REQUIRED:
        if key not in front:
            findings.append(Finding("error", "frontmatter", where, f"`{key}` is missing"))
    kind = front.get("kind")
    if isinstance(kind, str) and kind not in KINDS:
        findings.append(
            Finding("error", "frontmatter", where, f"`kind: {kind}` is not one of {sorted(KINDS)}")
        )
    status = front.get("status")
    if isinstance(status, str) and status not in STATUSES:
        findings.append(
            Finding(
                "error",
                "frontmatter",
                where,
                f"`status: {status}` is not one of {sorted(STATUSES)}",
            )
        )
    for key in ("created", "updated"):
        value = front.get(key)
        if isinstance(value, str) and not DATE.match(value):
            findings.append(
                Finding("error", "frontmatter", where, f"`{key}: {value}` is not YYYY-MM-DD")
            )
    task_id = front.get("task")
    ids = spec_check._as_list(front.get("ids"))
    if isinstance(task_id, str) and ids and task_id not in ids:
        findings.append(
            Finding("error", "frontmatter", where, f"`ids` does not include `task: {task_id}`")
        )
    if not ids and "ids" in front:
        findings.append(Finding("error", "frontmatter", where, "`ids` is empty"))
    return findings


def check_layout(task: Path, name: str, front: dict[str, object]) -> list[Finding]:
    findings: list[Finding] = []
    word_id = None
    match = re.match(r"^([A-Z][A-Z0-9]*-\d+)", name)
    if match:
        word_id = match.group(1)
    task_id = front.get("task")
    if name.startswith("_"):
        return findings
    if word_id and not WORD_ID.match(word_id):
        findings.append(
            Finding("warn", "layout", name, f"folder name starts with `{word_id}`, not an ID")
        )
    elif word_id is None and not SLUG.match(name):
        findings.append(
            Finding("warn", "layout", name, "folder name is neither `<ID>-<slug>` nor a latin slug")
        )
    elif word_id and isinstance(task_id, str) and word_id != task_id:
        findings.append(
            Finding(
                "warn",
                "layout",
                name,
                f"folder starts with `{word_id}`, while `task` is `{task_id}`",
            )
        )

    stages = task / "stages"
    linked: set[str] = set()
    index = task / "index.md"
    if index.is_file():
        for target in STAGE_LINK.findall(index.read_text(encoding="utf-8")):
            linked.add(target)
            if not (task / target).is_file():
                findings.append(
                    Finding("error", "layout", "index.md", f"stage link `{target}` does not resolve")
                )
    if stages.is_dir():
        for folder in sorted(path for path in stages.iterdir() if path.is_dir()):
            relative = folder.relative_to(task).as_posix()
            if not SLUG.match(folder.name):
                findings.append(
                    Finding("error", "layout", relative, "stage folder is not a latin slug")
                )
            card = folder / "stage.md"
            if not card.is_file():
                findings.append(
                    Finding("error", "layout", relative, "stage folder has no stage.md")
                )
            elif f"{relative}/stage.md" not in linked:
                findings.append(
                    Finding(
                        "warn",
                        "layout",
                        "index.md",
                        f"stage `{relative}` is not linked from the stages table",
                    )
                )
    return findings


def rows_of(tasks_md: str) -> list[list[str]]:
    rows = []
    for line in ROW.findall(tasks_md):
        cells = [cell.strip() for cell in line.split("|")]
        if not cells or set("".join(cells)) <= set("-: "):
            continue
        rows.append(cells)
    return rows


def check_tasks_index(
    specs: Path,
    folders: list[Path],
    fronts: dict[str, dict[str, object]],
    only: set[str] | None = None,
    added: set[str] | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    path = specs / "TASKS.md"
    if not path.is_file():
        return [Finding("error", "tasks", "TASKS.md", "TASKS.md is missing")]
    table: dict[str, list[str]] = {}
    for cells in rows_of(path.read_text(encoding="utf-8")):
        if len(cells) < 6 or cells[0] == "Тип":
            continue
        folder = cells[1]
        match = re.search(r"\[([^\]]+)\]\(", folder)
        if match:
            folder = match.group(1)
        table[folder.strip("` ")] = cells[2:6]
    for folder in folders:
        name = folder.name
        if only is not None and name not in only:
            continue
        if name not in table:
            findings.append(
                Finding("error", "tasks", "TASKS.md", f"no row for `{name}`")
            )
            continue
        main_id, all_ids, status, updated = table[name]
        front = fronts.get(name, {})
        if isinstance(front.get("task"), str) and main_id != front["task"]:
            findings.append(
                Finding(
                    "error",
                    "tasks",
                    "TASKS.md",
                    f"row `{name}` has main ID `{main_id}`, index has `{front['task']}`",
                )
            )
        listed = {item.strip() for item in all_ids.split(",") if item.strip()}
        declared = set(spec_check._as_list(front.get("ids")))
        if declared and listed != declared:
            findings.append(
                Finding(
                    "error",
                    "tasks",
                    "TASKS.md",
                    f"row `{name}` lists {sorted(listed)}, index declares {sorted(declared)}",
                )
            )
        if isinstance(front.get("status"), str) and status != front["status"]:
            findings.append(
                Finding(
                    "error",
                    "tasks",
                    "TASKS.md",
                    f"row `{name}` has status `{status}`, index has `{front['status']}`",
                )
            )
        if isinstance(front.get("updated"), str) and updated != front["updated"]:
            findings.append(
                Finding(
                    "warn",
                    "tasks",
                    "TASKS.md",
                    f"row `{name}` has date `{updated}`, index has `{front['updated']}`",
                )
            )
    known = {folder.name for folder in folders}
    for name in sorted(set(table) - known):
        if only is not None and (added is None or name not in added):
            continue
        findings.append(
            Finding("error", "tasks", "TASKS.md", f"row `{name}` has no folder under tasks/")
        )
    return findings


def has_filled_summary(index_text: str) -> bool:
    match = re.search(r"^## Итог[ \t]*$", index_text, re.MULTILINE)
    if match is None:
        return False
    rest = index_text[match.end() :]
    following = re.search(r"^## ", rest, re.MULTILINE)
    body = rest[: following.start()] if following else rest
    return bool(body.strip())


def changed_paths(specs: Path, base: str) -> list[str] | None:
    """Files changed between ``base`` and HEAD, or ``None`` when git cannot tell."""
    check = ["git", "-C", str(specs), "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"]
    try:
        if (
            subprocess.run(
                check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            ).returncode
            != 0
        ):
            return None
        output = subprocess.check_output(
            ["git", "-C", str(specs), "diff", "--name-only", f"{base}..HEAD"], text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return [line.strip() for line in output.splitlines() if line.strip()]


def changed_tasks(paths: list[str]) -> set[str]:
    names = set()
    for path in paths:
        match = re.match(r"^tasks/([^/]+)/", path)
        if match:
            names.add(match.group(1))
    return names


def changed_rows(specs: Path, base: str) -> tuple[set[str], set[str]]:
    """Task folders named in the changed ``TASKS.md`` lines: touched and added."""
    try:
        output = subprocess.check_output(
            ["git", "-C", str(specs), "diff", "-U0", f"{base}..HEAD", "--", "TASKS.md"],
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return set(), set()
    touched: set[str] = set()
    added: set[str] = set()
    for line in output.splitlines():
        if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
            continue
        cells = [cell.strip() for cell in line[1:].strip().strip("|").split("|")]
        if len(cells) < 2:
            continue
        folder = cells[1]
        match = re.search(r"\[([^\]]+)\]\(", folder)
        if match:
            folder = match.group(1)
        folder = folder.strip("` ")
        if not folder:
            continue
        touched.add(folder)
        if line.startswith("+"):
            added.add(folder)
    return touched, added


def check_task(task: Path) -> list[Finding]:
    findings: list[Finding] = []
    index = task / "index.md"
    if not index.is_file():
        return [Finding("error", "frontmatter", task.name, "index.md is missing")]
    text = index.read_text(encoding="utf-8")
    front = spec_check.parse_frontmatter(text)
    findings += check_frontmatter(task, front)
    findings += check_layout(task, task.name, front)

    journals, journal_findings = spec_check.collect_journals(task)
    findings += journal_findings
    findings += spec_check.check_journal_numbering(journals)
    documents = task_documents(task)
    findings += spec_check.check_references(documents, journals, spec_check.moved_tokens(journals))
    if spec_check.has_codes(journals):
        findings += spec_check.check_transfers(journals)
        findings += spec_check.check_rules_links(journals, task)

    todo = task / "todo.md"
    if todo.is_file():
        findings += spec_check.check_todo(todo.read_text(encoding="utf-8"), limit=15)

    plan = task / "plan.md"
    if plan.is_file() and CHECKBOX.search(plan.read_text(encoding="utf-8")):
        findings.append(
            Finding(
                "warn",
                "plan",
                "plan.md",
                "checkboxes belong to stage.md; plan.md keeps order, dependencies and risks",
            )
        )

    if front.get("status") == "done" and not has_filled_summary(text):
        findings.append(Finding("error", "closure", "index.md", "`status: done` without a filled «Итог»"))
    return findings


def lint(specs: Path, base: str | None = None) -> list[Finding]:
    tasks = specs / "tasks"
    if not tasks.is_dir():
        return [Finding("error", "layout", str(specs), "no tasks/ directory")]
    folders = sorted(path for path in tasks.iterdir() if path.is_dir())

    only: set[str] | None = None
    added: set[str] | None = None
    if base is not None:
        paths = changed_paths(specs, base)
        if paths is not None:
            only = changed_tasks(paths)
            added = set()
            if "TASKS.md" in paths:
                rows, added = changed_rows(specs, base)
                only |= rows
            folders = [folder for folder in folders if folder.name in only]

    findings: list[Finding] = []
    fronts: dict[str, dict[str, object]] = {}
    for folder in folders:
        index = folder / "index.md"
        if index.is_file():
            fronts[folder.name] = spec_check.parse_frontmatter(index.read_text(encoding="utf-8"))
        for finding in check_task(folder):
            findings.append(
                Finding(finding.level, finding.check, f"{folder.name}/{finding.where}", finding.message)
                if finding.where != folder.name
                else finding
            )
    findings += check_tasks_index(specs, folders, fronts, only=only, added=added)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lint a specs repository against the rules.")
    parser.add_argument(
        "--specs",
        type=Path,
        default=None,
        help="path to the specs repository (default: SPECS_ROOT)",
    )
    parser.add_argument(
        "--base",
        metavar="REF",
        default=None,
        help="check only the tasks touched between REF and HEAD (used by the pre-push hook)",
    )
    parser.add_argument("--strict", action="store_true", help="warnings fail the run")
    args = parser.parse_args(argv)

    specs = args.specs
    if specs is None:
        import os

        root = os.environ.get("SPECS_ROOT")
        if not root:
            print("specs repository not found: pass --specs or set SPECS_ROOT", file=sys.stderr)
            return 2
        specs = Path(root)
    if not specs.is_dir():
        print(f"specs repository not found: {specs}", file=sys.stderr)
        return 2

    findings = lint(specs, base=args.base)
    for finding in sorted(findings, key=lambda item: (item.level != "error", item.check, item.where)):
        print(finding.render())

    errors = sum(1 for finding in findings if finding.level == "error")
    warnings = len(findings) - errors
    print(f"\n{errors} error(s), {warnings} warning(s)")
    return 1 if errors or (args.strict and warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
