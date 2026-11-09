import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).with_name("check-frontmatter.py")
SPEC = importlib.util.spec_from_file_location("check_frontmatter", SCRIPT)
assert SPEC and SPEC.loader
check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check)


def reasons(lines: list[str]) -> list[str]:
    return [reason for _, _, reason in check.unsafe_lines(lines)]


def test_flags_colon_inside_plain_scalar():
    assert check.unsafe_lines(["description: review of the code: plan claims"]) == [
        (2, "description: review of the code: plan claims", "colon in an unquoted value")
    ]


def test_flags_trailing_colon_and_hash_truncation():
    assert reasons(["description: review:"] ) == ["colon in an unquoted value"]
    assert reasons(["description: see #1 for details"]) == ["`#` after a space cuts the value"]
    assert reasons(["description: #hash"]) == ["value is a comment"]


def test_flags_leading_indicators():
    for value in ("@mention", "`code`", "%directive", "!tag text", "&anchor", "*alias", "] x", "} x", ", x"):
        assert reasons([f"description: {value}"]) == [f"value starts with `{value[0]}`"]

    for value in ("-", "?", ":", "- item", "? query", ": value"):
        assert reasons([f"description: {value}"]) == [f"value starts with `{value[0]}`"]


def test_allows_flow_collections():
    assert reasons(["repos: []", "permission: {}", "aliases: [alfa, beta]"]) == []


def test_flags_tab_and_duplicate_key():
    assert reasons(["description: a\tb"]) == ["tab in frontmatter"]
    assert reasons(["mode: subagent", "mode: primary"]) == ["duplicate key `mode`"]
    # One indent level is a separate namespace: `metadata:` and its child may share nothing.
    assert reasons(["permission:", "  read: allow", "  grep: allow"]) == []


def test_allows_quoted_values_and_block_scalars():
    assert (
        reasons(
            [
                'description: "review of the code: plan"',
                "notes: |",
                "  review of the code: plan",
                "  # hash and colon: kept as text",
            ]
        )
        == []
    )


def test_flags_broken_quotes():
    assert reasons(['description: "review']) == ["unterminated quote"]
    assert reasons(['description: "review" tail']) == ["text after the closing quote"]


def test_repository_frontmatter_is_clean():
    assert check.main() == 0
