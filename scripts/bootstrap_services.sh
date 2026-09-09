#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-.virelion/services}"
mkdir -p "$ROOT"
repos=(
  CardiAtlas CardiBench CardiEval ElectroTrace MyoTrace OptiCell CardioScore CardiLearn CardiSim CardiTrace CardiBridge CardiAgent CardiVex CardiStudio DCCP
)
for name in "${repos[@]}"; do
  dir="$ROOT/$name"
  url="https://github.com/Virelion-Biotech/Virelion-$name.git"
  if [[ -d "$dir/.git" ]]; then
    git -C "$dir" fetch origin main --quiet
    git -C "$dir" checkout main --quiet
    git -C "$dir" reset --hard origin/main --quiet
  else
    git clone --depth 1 --branch main "$url" "$dir"
  fi
done
printf '\nAll Virelion service repositories are available under %s\n' "$ROOT"
