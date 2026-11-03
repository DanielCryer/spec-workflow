import importlib.util
import subprocess
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("spec_check.py")
_SPEC = importlib.util.spec_from_file_location("spec_check", MODULE_PATH)
assert _SPEC and _SPEC.loader
spec_check = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = spec_check
_SPEC.loader.exec_module(spec_check)


def levels(findings):
    return [finding.level for finding in findings]


def test_plan_items_join_continuation_lines():
    items = spec_check.parse_plan(
        "# plan\n\n- [x] first item\n      wrapped tail\n- [ ] second\n"
    )

    assert [(item.line, item.checked, item.text) for item in items] == [
        (3, True, "first item wrapped tail"),
        (5, False, "second"),
    ]


def test_symbol_candidates_skip_artifacts_endpoints_and_lowercase():
    text = (
        "`Core` `pytest` `snapshot.json` `/state` `list`/`active` "
        "`Event.to_record` `core.py`"
    )

    assert spec_check.symbol_candidates(text) == ["Core", "to_record"]


def test_unknown_symbol_is_an_error_only_in_checked_items():
    plan = spec_check.parse_plan(
        "- [x] has `Core`\n- [x] has `Missing`\n- [ ] has `Future`\n"
    )

    findings = spec_check.check_plan_symbols(plan, "class Core: pass", "plan.md")

    assert [(finding.level, finding.where) for finding in findings] == [
        ("error", "plan.md:2")
    ]


def test_commit_refs_resolve_and_report_unknown(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for command in (
        ["git", "init", "-q"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-qm", "one"],
    ):
        subprocess.run(command, cwd=repo, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    findings = spec_check.check_commit_refs(
        [("spec.md", f"repo@{head} bare @{head} repo@deadbeefcafe other@1234567")],
        {"repo": repo},
        "repo",
    )

    assert levels(findings) == ["error", "warn"]
    assert "deadbeefcafe" in findings[0].message


def test_file_refs_check_bounds_ambiguity_and_missing_files(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "mod.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (tmp_path / "b" / "mod.py").write_text("one\n", encoding="utf-8")
    (tmp_path / "only.py").write_text("one\ntwo\n", encoding="utf-8")

    index = spec_check.index_files([tmp_path])
    findings = spec_check.check_file_refs(
        [("d.md", "only.py:2 ok, only.py:9 past, mod.py:1 ambiguous, nope.py:1 missing")],
        index,
    )

    messages = [finding.message for finding in findings]
    assert levels(findings) == ["warn", "warn", "warn"]
    assert "past the end" in messages[0]
    assert "several files match" in messages[1]
    assert "not found" in messages[2]


def test_numbering_reports_duplicates_gaps_index_drift_and_dangling_refs():
    decisions = (
        "## Действующие решения\n\n- **D1** — one\n\n"
        "## D1. one — 2026-11-03\n\n## D2. two — 2026-11-03\n\n"
        "## D2. two again — 2026-11-03\n"
    )
    problems = "## P1. one — 2026-11-03\n\n## P3. three — 2026-11-03\n"

    findings = spec_check.check_numbering(
        decisions, problems, [("plan.md", "see P2 and D9")]
    )

    messages = [finding.message for finding in findings]
    assert any("D2 is declared twice" in message for message in messages)
    assert any("D2 is missing from the active index" in message for message in messages)
    assert any("P2 is missing from the sequence" in message for message in messages)
    assert any("refers to P2" in message for message in messages)
    assert any("refers to D9" in message for message in messages)


def test_shortcut_needs_a_return_condition():
    assert spec_check.shortcut_complete(
        "shortcut: rescans the journal; switch to an offset when the file grows past 10k"
    )
    assert spec_check.shortcut_complete(
        "shortcut: only plain http is tunnelled. Revisit with P5."
    )
    assert not spec_check.shortcut_complete(
        "shortcut: capped pagination; above 1000 sessions the list is incomplete"
    )
    assert not spec_check.shortcut_complete("shortcut: keeps everything in memory for now")


def test_todo_cap_counts_only_checkboxes():
    text = "\n".join(f"- [ ] action {index}" for index in range(16))

    assert levels(spec_check.check_todo(text, limit=15)) == ["warn"]
    assert spec_check.check_todo(text, limit=16) == []


def test_context_derives_repos_and_roots_from_index(tmp_path):
    spec = tmp_path / "spec"
    repo = tmp_path / "r"
    (spec / "sub").mkdir(parents=True)
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "pkg.py").write_text("class Core:\n    pass\n", encoding="utf-8")
    (spec / "sub" / "index.md").write_text(
        "---\nrepos: [r]\nref_roots: [proto]\n---\n",
        encoding="utf-8",
    )

    context = spec_check.load_context(spec / "sub", tmp_path)

    assert context.repos == {"r": (tmp_path / "r").resolve()}
    assert context.primary == "r"
    assert (repo / "src").resolve() in context.code_roots
    assert (tmp_path / "proto").resolve() in context.file_roots


def test_end_to_end_fixture(tmp_path):
    spec = tmp_path / "spec"
    src = tmp_path / "r" / "src"
    spec.mkdir()
    src.mkdir(parents=True)
    (src / "pkg.py").write_text("class Core:\n    pass\n", encoding="utf-8")
    (spec / "index.md").write_text("---\nrepos: [r]\n---\n", encoding="utf-8")
    (spec / "plan.md").write_text("- [x] `Core` is there\n", encoding="utf-8")
    (spec / "decisions.md").write_text(
        "## Действующие решения\n\n- **D1** — one\n\n## D1. one — 2026-11-03\n",
        encoding="utf-8",
    )
    (spec / "problems.md").write_text("", encoding="utf-8")
    (spec / "todo.md").write_text("- [ ] one\n", encoding="utf-8")

    context = spec_check.load_context(spec, tmp_path)
    assert spec_check.run_checks(context, with_tests=False) == []

    (spec / "plan.md").write_text("- [x] `Missing` is there\n", encoding="utf-8")
    findings = spec_check.run_checks(context, with_tests=False)

    assert [(finding.check, finding.level) for finding in findings] == [
        ("symbols", "error")
    ]
