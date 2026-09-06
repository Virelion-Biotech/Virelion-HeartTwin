#!/usr/bin/env bash
set -euo pipefail
ROOT="${1:-.virelion/services}"
mkdir -p "$ROOT"
repos=(
  CardiAtlas CardiBench CardiEval ElectroTrace MyoTrace OptiCell CardioScore CardiLearn CardiSim CardiTrace CardiBridge CardiAgent CardiVex
)
for name in "${repos[@]}"; do
  dir="$ROOT/$name"
  url="https://github.com/Virelion-Biotech/Virelion-$name.git"
  # CardiAtlas/Bench/Eval/Sim/Trace/Bridge/Learn/Agent/Vex use the Cardi* naming;
  # measurement repositories intentionally keep their public names.
  if [[ "$name" == "ElectroTrace" || "$name" == "MyoTrace" || "$name" == "OptiCell" || "$name" == "CardioScore" ]]; then
    url="https://github.com/Virelion-Biotech/Virelion-$name.git"
  fi
  if [[ -d "$dir/.git" ]]; then
    git -C "$dir" fetch origin main --quiet
    git -C "$dir" checkout main --quiet
    git -C "$dir" reset --hard origin/main --quiet
  else
    git clone --depth 1 --branch main "$url" "$dir"
  fi
done
printf '\nAll Virelion service repositories are available under %s\n' "$ROOT"
