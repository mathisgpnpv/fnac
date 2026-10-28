#!/usr/bin/env bash
# Lanceur pour un VPS : charge config.env, évite deux exécutions en même temps.
set -euo pipefail
cd "$(dirname "$0")"
set -a; [ -f config.env ] && . ./config.env; set +a
exec flock -n /tmp/fnac-zelda-bot.lock .venv/bin/python check.py
