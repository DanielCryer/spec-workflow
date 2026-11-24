import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import spec_lint  # noqa: E402

INDEX = """---
task: T-1
ids: [T-1]
kind: project
status: draft
created: 2026-01-01
updated: 2026-01-01
repos: []
---

# Пример

## Этапы

| # | Этап | Статус | Документ |
|---|------|--------|----------|
| 1 | Этап | draft | `stages/one/stage.md` |
"""

TASKS = """# Задачи

| Тип | Папка | Основной ID | Все ID | Статус | Обновлено |
|-----|-------|-------------|--------|--------|-----------|
| project | T-1-example | T-1 | T-1 | draft | 2026-01-01 |
"""


def _repo(tmp_path, *, index=INDEX, tasks=TASKS, stage=True, extra=None):
    specs = tmp_path / "specs"
    task = specs / "tasks" / "T-1-example"
    task.mkdir(parents=True)
    (specs / "TASKS.md").write_text(tasks, encoding="utf-8")
    (task / "index.md").write_text(index, encoding="utf-8")
    if stage:
        folder = task / "stages" / "one"
        folder.mkdir(parents=True)
        (folder / "stage.md").write_text("# Этап\n\n**Код:** one\n", encoding="utf-8")
    for name, text in (extra or {}).items():
        path = task / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return specs


def _messages(findings):
    return [f"{finding.check}: {finding.message}" for finding in findings]


def test_clean_repository_has_no_findings(tmp_path):
    assert spec_lint.lint(_repo(tmp_path)) == []


def test_missing_frontmatter_key_is_an_error(tmp_path):
    index = INDEX.replace("updated: 2026-01-01\n", "")
    findings = spec_lint.lint(_repo(tmp_path, index=index))

    assert any("`updated` is missing" in message for message in _messages(findings))


def test_unknown_status_is_an_error(tmp_path):
    index = INDEX.replace("status: draft", "status: doing")
    findings = spec_lint.lint(_repo(tmp_path, index=index))

    assert any("`status: doing`" in message for message in _messages(findings))


def test_missing_tasks_row_is_an_error(tmp_path):
    tasks = """# Задачи

| Тип | Папка | Основной ID | Все ID | Статус | Обновлено |
|-----|-------|-------------|--------|--------|-----------|
"""
    findings = spec_lint.lint(_repo(tmp_path, tasks=tasks))

    assert any("no row for `T-1-example`" in message for message in _messages(findings))


def test_tasks_status_must_match_the_index(tmp_path):
    tasks = TASKS.replace("| draft |", "| done |")
    findings = spec_lint.lint(_repo(tmp_path, tasks=tasks))

    assert any("has status `done`" in message for message in _messages(findings))


def test_orphan_tasks_row_is_an_error(tmp_path):
    tasks = TASKS + "| project | nothing | T-9 | T-9 | draft | 2026-01-01 |\n"
    findings = spec_lint.lint(_repo(tmp_path, tasks=tasks))

    assert any("row `nothing` has no folder" in message for message in _messages(findings))


def test_stage_folder_needs_a_card(tmp_path):
    specs = _repo(tmp_path, stage=False)
    (specs / "tasks" / "T-1-example" / "stages" / "one").mkdir(parents=True)
    findings = spec_lint.lint(specs)

    assert any("has no stage.md" in message for message in _messages(findings))


def test_stage_card_must_be_linked_from_the_index(tmp_path):
    index = INDEX.replace("| 1 | Этап | draft | `stages/one/stage.md` |\n", "")
    findings = spec_lint.lint(_repo(tmp_path, index=index))

    assert any("is not linked from the stages table" in message for message in _messages(findings))


def test_checkboxes_in_plan_are_a_warning(tmp_path):
    specs = _repo(tmp_path, extra={"plan.md": "# План\n\n- [ ] пункт\n"})
    findings = spec_lint.lint(specs)

    assert [finding.level for finding in findings] == ["warn"]


def test_done_without_a_summary_is_an_error(tmp_path):
    index = INDEX.replace("status: draft", "status: done")
    tasks = TASKS.replace("| draft |", "| done |")
    findings = spec_lint.lint(_repo(tmp_path, index=index, tasks=tasks))

    assert any("without a filled" in message for message in _messages(findings))


def test_numbering_is_checked_inside_a_stage_journal(tmp_path):
    specs = _repo(
        tmp_path,
        extra={
            "stages/one/decisions.md": "## Done.1. Первое\n\n## Done.1. Второе\n",
        },
    )
    findings = spec_lint.lint(specs)

    assert any("declared twice" in message for message in _messages(findings))


def _git(repo, *args):
    import subprocess

    subprocess.check_call(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _commit(repo, message="step"):
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-q",
        "-m",
        message,
    )


def _head(repo):
    import subprocess

    return subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()


def _two_task_repo(tmp_path):
    specs = _repo(tmp_path)
    other = specs / "tasks" / "T-2-other"
    other.mkdir()
    index = INDEX.replace("task: T-1", "task: T-2").replace("ids: [T-1]", "ids: [T-2]")
    index = index.replace("| 1 | Этап | draft | `stages/one/stage.md` |\n", "")
    (other / "index.md").write_text(index, encoding="utf-8")
    tasks = (specs / "TASKS.md").read_text(encoding="utf-8")
    (specs / "TASKS.md").write_text(
        tasks + "| project | T-2-other | T-2 | T-2 | draft | 2026-01-01 |\n",
        encoding="utf-8",
    )
    _git(specs, "init", "-q")
    _commit(specs, "base")
    return specs


def test_base_mode_ignores_a_task_that_was_broken_elsewhere(tmp_path):
    specs = _two_task_repo(tmp_path)
    table = specs / "TASKS.md"
    table.write_text(
        table.read_text(encoding="utf-8").replace(
            "| project | T-2-other | T-2 | T-2 | draft | 2026-01-01 |\n", ""
        ),
        encoding="utf-8",
    )
    _commit(specs, "break the other task")
    base = _head(specs)

    (specs / "tasks" / "T-1-example" / "plan.md").write_text(
        "# План\n\n- [ ] пункт\n", encoding="utf-8"
    )
    _commit(specs, "touch the first task")

    findings = spec_lint.lint(specs, base=base)

    assert [finding.level for finding in findings] == ["warn"]


def test_base_mode_reports_a_problem_in_the_touched_task(tmp_path):
    specs = _two_task_repo(tmp_path)
    base = _head(specs)
    (specs / "tasks" / "T-1-example" / "plan.md").write_text(
        "# План\n\n- [ ] пункт\n", encoding="utf-8"
    )
    _commit(specs, "add a plan")

    findings = spec_lint.lint(specs, base=base)

    assert [finding.level for finding in findings] == ["warn"]


def test_base_mode_reports_an_added_row_without_a_folder(tmp_path):
    specs = _two_task_repo(tmp_path)
    base = _head(specs)
    with (specs / "TASKS.md").open("a", encoding="utf-8") as stream:
        stream.write("| project | T-9-ghost | T-9 | T-9 | draft | 2026-01-01 |\n")
    _commit(specs, "add a ghost row")

    findings = spec_lint.lint(specs, base=base)

    assert any("row `T-9-ghost` has no folder" in f"{finding.check}: {finding.message}" for finding in findings)
