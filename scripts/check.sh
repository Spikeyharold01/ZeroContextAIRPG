#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(git -C "$script_dir" rev-parse --show-toplevel)"
cd "$repo_root"

export PYTHONDONTWRITEBYTECODE=1

python - <<'PY'
from pathlib import Path

source_root = Path("erp-dm-server")
for path in sorted(source_root.rglob("*.py")):
    compile(path.read_bytes(), str(path), "exec")
PY

python -m pytest -q erp-dm-server
# Exclude only the immutable historical schema snapshot; all normal source
# files remain covered by the whitespace check.
git diff --check -- . ':(exclude)erp-dm-server/database/baselines/001_schema.sql'

status="$(git status --short)"
if [[ -n "$status" ]]; then
    printf 'Working tree is not clean after acceptance checks:\n%s\n' "$status" >&2
    exit 1
fi
