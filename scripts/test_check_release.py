import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).with_name("check-release.py")
SPEC = importlib.util.spec_from_file_location("check_release", SCRIPT)
assert SPEC and SPEC.loader
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def test_reads_topmost_version():
    changelog = "# Changelog\n\n## 0.3.6 — 2026-11-12\n\n## 0.3.5 — 2026-11-09\n"
    assert check.current_version(changelog) == "0.3.6"


def test_ignores_headings_without_a_version():
    assert check.current_version("# Changelog\n\n## Unreleased\n\n- something\n") is None
    assert check.current_version("## 0.3\n") is None


def test_tag_name_prefixes_the_version():
    assert check.tag_name("0.3.6") == "v0.3.6"


def test_repository_changelog_starts_with_a_version():
    changelog = (Path(__file__).resolve().parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    assert check.current_version(changelog) is not None
