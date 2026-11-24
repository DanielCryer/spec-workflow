#!/bin/sh
# Connect the versioned hooks/ directory and remember where the linter lives.
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
skill="${1:-${SPEC_WORKFLOW:-}}"

if [ -z "$skill" ]; then
    echo "usage: sh scripts/install-hooks.sh <path to the spec-workflow clone>" >&2
    exit 2
fi

linter="$skill/skills/spec-driven-development/scripts/spec_lint.py"
if [ ! -f "$linter" ]; then
    echo "linter not found: $linter" >&2
    exit 2
fi

git -C "$root" config core.hooksPath hooks
printf '%s\n' "$linter" > "$root/.git/spec-lint-path"
echo "pre-push hook installed; linter: $linter"
