#!/bin/sh
# Fail when a unit carries a machine-specific path. Skills must stay portable:
# spec roots come from SPECS_ROOT / PROJECTS_ROOT, never from a literal disk path.
set -eu

cd "$(dirname "$0")/.."

pattern='(^|[[:space:]"'"'"'`(])[A-Za-z]:[\\/]|/home/[a-z]|/Users/[A-Za-z]'
status=0

hits=$(grep -rnE "$pattern" \
    --exclude-dir=.pytest_cache --exclude-dir=__pycache__ \
    --include='*.md' --include='*.py' --include='*.sh' --include='*.yml' \
    --include='*.yaml' --include='*.json' --include='*.jsonc' \
    skills commands agents templates 2>/dev/null || true)

if [ -n "$hits" ]; then
    echo "machine-specific paths found:" >&2
    echo "$hits" >&2
    status=1
fi

if [ "$status" -eq 0 ]; then
    echo "portability check: ok"
fi

exit "$status"
