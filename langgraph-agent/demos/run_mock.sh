#!/usr/bin/env bash
# 无 API Key 时走确定性 mock LLM
set -euo pipefail
cd "$(dirname "$0")/.."
export OPENAI_API_KEY=
if [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
else
  PY=python3
fi
"$PY" -m final_review_agent --demo --format text
