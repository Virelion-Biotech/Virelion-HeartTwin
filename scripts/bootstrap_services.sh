#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-.virelion/services}"
mkdir -p "$ROOT"
SCRIPT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# Use the same service inventory as the compatibility matrix. A second list
# previously omitted anatomy and five mechanistic services.
repo_names="$(python3 - "$SCRIPT_ROOT/configs/compatibility-lock.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    repositories = json.load(handle)["repositories"]
if not repositories:
    raise ValueError("Compatibility matrix has no service repositories")
for repository in repositories:
    name = repository["name"]
    if not name.startswith("Virelion-") or not name.removeprefix("Virelion-").isalnum():
        raise ValueError(f"Unexpected service repository: {name!r}")
    print(name.removeprefix("Virelion-"))
PY
)"
mapfile -t repos <<< "$repo_names"
for name in "${repos[@]}"; do
  dir="$ROOT/$name"
  url="https://github.com/Virelion-Biotech/Virelion-$name.git"
  if [[ -d "$dir/.git" ]]; then
    git -C "$dir" fetch origin main --quiet
    git -C "$dir" checkout main --quiet
    git -C "$dir" merge --ff-only origin/main --quiet
  else
    git clone --depth 1 --branch main "$url" "$dir"
  fi
done
printf '\nAll Virelion service repositories are available under %s\n' "$ROOT"
