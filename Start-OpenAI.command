#!/bin/zsh
cd "$(dirname "$0")" || exit 1
python3 scripts/start_openai.py
