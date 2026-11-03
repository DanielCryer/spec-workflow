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


def test_symbol_candidates_read_cpp_names():
    text = (
        "`ChatRouter.hpp` `ntpro::dto::FieldMask` `FieldMask<T>` "
        "`ResetFieldsByFlag()` `std::string` `QuoteEngine.cpp`"
    )

    assert spec_check.symbol_candidates(text) == ["FieldMask", "ResetFieldsByFlag"]


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


def test_thematic_summary_indexes_decisions_without_a_line_per_record():
    decisions = (
        "## Действующие решения\n\n"
        "- **Маски и поля** — роли выражаются тегами (D1, D2, D3).\n"
        "- **Совместимость** — новые поля только опциональные (D4).\n\n"
        "## D1. one — 2026-11-03\n\n## D2. two — 2026-11-03\n\n"
        "## D3. three — 2026-11-03\n\n## D4. four — 2026-11-03\n"
    )

    assert spec_check.check_numbering(decisions, "", []) == []

    decisions += "\n## D5. five — 2026-11-03\n"
    findings = spec_check.check_numbering(decisions, "", [])

    assert [finding.message for finding in findings] == [
        "D5 is missing from the active index"
    ]


def test_absorbed_records_need_no_summary_line():
    decisions = (
        "## Действующие решения\n\n- **Тема** — (D1)\n\n"
        "## D1. one — 2026-11-03\n\n"
        "## D2. two — влито в D1 — 2026-11-03\n\n- **Статус**: объединено с D1\n\n"
        "## D3. three — 2026-11-03\n"
    )

    findings = spec_check.check_numbering(decisions, "", [])

    assert spec_check.absorbed_decisions(decisions) == {2}
    assert [finding.message for finding in findings] == [
        "D3 is missing from the active index"
    ]


def test_index_mode_controls_how_strictly_the_summary_is_read():
    decisions = (
        "## Действующие решения\n\n- **Тема** — (D1, D2)\n\n"
        "## D1. one — 2026-11-03\n\n## D2. two — 2026-11-03\n"
    )

    strict = spec_check.check_numbering(decisions, "", [], index_mode="strict")
    assert sorted(finding.message for finding in strict) == [
        "D1 is missing from the active index",
        "D2 is missing from the active index",
    ]

    assert spec_check.check_numbering(decisions, "", [], index_mode="off") == []

    without_summary = "## D1. one — 2026-11-03\n"
    findings = spec_check.check_numbering(without_summary, "", [], index_mode="summary")

    assert levels(findings) == ["warn"]
    assert "no active decisions summary" in findings[0].message


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


def test_context_resolves_repo_paths_and_submodules(tmp_path):
    spec = tmp_path / "spec"
    (spec).mkdir()
    (tmp_path / "beta" / "Sources" / "UiFramework" / "src").mkdir(parents=True)
    (spec / "index.md").write_text(
        "---\nrepos: [beta, beta/Sources/UiFramework, Alias=beta/Sources/UiFramework]\n---\n",
        encoding="utf-8",
    )

    context = spec_check.load_context(spec, tmp_path)

    assert context.repos == {
        "beta": (tmp_path / "beta").resolve(),
        "UiFramework": (tmp_path / "beta" / "Sources" / "UiFramework").resolve(),
        "Alias": (tmp_path / "beta" / "Sources" / "UiFramework").resolve(),
    }
    assert context.primary == "beta"


def _spec_dir(tmp_path: Path) -> Path:
    spec = tmp_path / "spec"
    spec.mkdir(exist_ok=True)
    (spec / "index.md").write_text(
        "---\nrepos: [beta, beta/Sources/UiFramework]\n---\n", encoding="utf-8"
    )
    return spec


def test_commit_refs_resolve_in_a_submodule(tmp_path):
    module = tmp_path / "beta" / "Sources" / "UiFramework"
    module.mkdir(parents=True)
    for command in (
        ["git", "init", "-q"],
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "--allow-empty", "-qm", "one"],
    ):
        subprocess.run(command, cwd=module, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=module,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    context = spec_check.load_context(_spec_dir(tmp_path), tmp_path)
    findings = spec_check.check_commit_refs(
        [("index.md", f"UiFramework@{head}")], context.repos, context.primary
    )

    assert findings == []


def test_end_to_end_fixture(tmp_path):
    spec = tmp_path / "spec"
    src = tmp_path / "r" / "src"
    spec.mkdir()
    src.mkdir(parents=True)
    (src / "pkg.py").write_text("class Core:\n    pass\n", encoding="utf-8")
    (spec / "index.md").write_text("---\nrepos: [r]\n---\n", encoding="utf-8")
    (spec / "plan.md").write_text(
        "- [x] `Core` is there, tests/test_core.py\n", encoding="utf-8"
    )
    (spec / "decisions.md").write_text(
        "## Действующие решения\n\n- **D1** — one\n\n## D1. one — 2026-11-03\n",
        encoding="utf-8",
    )
    (spec / "problems.md").write_text("", encoding="utf-8")
    (spec / "todo.md").write_text("- [ ] one\n", encoding="utf-8")

    context = spec_check.load_context(spec, tmp_path)
    assert spec_check.run_checks(context) == []

    (spec / "plan.md").write_text(
        "- [x] `Missing` is there, tests/test_core.py\n", encoding="utf-8"
    )
    findings = spec_check.run_checks(context)

    assert [(finding.check, finding.level) for finding in findings] == [
        ("symbols", "error")
    ]


def _cpp_spec(tmp_path: Path, plan: str, code_roots: str = "") -> Path:
    """A spec over a C++ repository: Sources/ and Externals/, as in real projects."""
    spec = tmp_path / "spec"
    sources = tmp_path / "beta" / "Sources"
    spec.mkdir()
    sources.mkdir(parents=True)
    (sources / "quote_engine.cpp").write_text(
        "class QuoteEngine\n{\npublic:\n    void ResetFieldsByFlag();\n};\n",
        encoding="utf-8",
    )
    (sources / "QuoteEngine.hpp").write_text(
        "class QuoteEngine\n{\n    void ResetFieldsByFlag();\n};\n", encoding="utf-8"
    )
    front = "---\nrepos: [beta]\n" + (f"code_roots: [{code_roots}]\n" if code_roots else "")
    (spec / "index.md").write_text(front + "---\n", encoding="utf-8")
    (spec / "plan.md").write_text(plan, encoding="utf-8")
    return spec


def test_cpp_names_resolve_against_cpp_files(tmp_path):
    spec = _cpp_spec(
        tmp_path,
        "- [x] `QuoteEngine` and `ntpro::dto::QuoteEngine` and `ResetFieldsByFlag()` "
        "in `Sources/quote_engine.cpp`, tests/test_engine.cpp\n",
    )

    context = spec_check.load_context(spec, tmp_path)

    assert spec_check.run_checks(context) == []


def test_stale_cpp_name_is_an_error(tmp_path):
    spec = _cpp_spec(
        tmp_path,
        "- [x] `QuoteEnginee` in `Sources/quote_engine.cpp`, tests/test_engine.cpp\n",
    )

    findings = spec_check.run_checks(spec_check.load_context(spec, tmp_path))

    assert [(finding.check, finding.level, finding.where) for finding in findings] == [
        ("symbols", "error", "plan.md:1")
    ]
    assert "QuoteEnginee" in findings[0].message


def test_vendor_and_build_trees_are_not_code(tmp_path):
    spec = _cpp_spec(
        tmp_path,
        "- [x] `VendorOnly` in `Sources/quote_engine.cpp`, tests/test_engine.cpp\n",
    )
    for directory in ("Externals", "build"):
        path = tmp_path / "beta" / directory
        path.mkdir()
        (path / "third_party.cpp").write_text("class VendorOnly {};\n", encoding="utf-8")

    findings = spec_check.run_checks(spec_check.load_context(spec, tmp_path))

    assert [(finding.check, finding.level) for finding in findings] == [
        ("symbols", "error")
    ]


def test_code_roots_narrow_the_code_that_is_read(tmp_path):
    spec = _cpp_spec(
        tmp_path,
        "- [x] `Elsewhere` in `Sources/quote_engine.cpp`, tests/test_engine.cpp\n",
        code_roots="beta/Sources",
    )
    elsewhere = tmp_path / "beta" / "SystemTests"
    elsewhere.mkdir()
    (elsewhere / "helper.cpp").write_text("class Elsewhere {};\n", encoding="utf-8")

    findings = spec_check.run_checks(spec_check.load_context(spec, tmp_path))

    assert [(finding.check, finding.level) for finding in findings] == [
        ("symbols", "error")
    ]


def test_stage_checkboxes_are_checked_like_plan_items(tmp_path):
    spec = _cpp_spec(tmp_path, "# План\n\n- [ ] Позже.\n")
    stages = spec / "stages"
    stages.mkdir()
    (stages / "01-prototype.md").write_text(
        "# Этап 1\n\n- [x] `QuoteEngine` in `Sources/quote_engine.cpp`\n"
        "- [x] `Ghost` in `Sources/quote_engine.cpp`\n"
        "- [x] Вопрос решён без ссылок\n",
        encoding="utf-8",
    )

    findings = spec_check.run_checks(spec_check.load_context(spec, tmp_path))

    assert [(finding.check, finding.level, finding.where) for finding in findings] == [
        ("symbols", "error", "stages/01-prototype.md:4")
    ]


def test_checked_item_without_a_commit_or_a_test_warns(tmp_path):
    spec = _cpp_spec(
        tmp_path,
        "- [x] `QuoteEngine` готов\n"
        "- [x] `QuoteEngine` готов, `tests/test_engine.cpp`\n",
    )

    findings = spec_check.run_checks(spec_check.load_context(spec, tmp_path))

    assert [(finding.check, finding.level, finding.where) for finding in findings] == [
        ("evidence", "warn", "plan.md:1")
    ]


def test_cpp_file_refs_check_bounds(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "quote_engine.cpp").write_text("one\ntwo\n", encoding="utf-8")

    documents = [
        (
            "plan.md",
            "`src/quote_engine.cpp:2` ok, `src/quote_engine.cpp:99` past the end",
        )
    ]

    index = spec_check.index_files([tmp_path / "src"])
    findings = spec_check.check_file_refs(documents, index)

    assert [(finding.check, finding.level) for finding in findings] == [("paths", "warn")]
    assert "past the end" in findings[0].message


def test_cpp_shortcut_markers_are_read(tmp_path):
    spec = _cpp_spec(tmp_path, "")
    source = tmp_path / "beta" / "Sources" / "legacy.cpp"
    source.write_text(
        "// shortcut: only plain http is tunnelled. Revisit with P5.\n"
        "// shortcut: keeps everything in memory\n",
        encoding="utf-8",
    )

    findings = spec_check.run_checks(spec_check.load_context(spec, tmp_path))

    assert [finding.message for finding in findings] == [
        "shortcut states no limit or no return condition"
    ]
    assert findings[0].where.endswith("legacy.cpp:2")
