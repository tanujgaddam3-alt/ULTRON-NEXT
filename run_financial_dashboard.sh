#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
export ULTRON_EXECUTION_BACKEND="${ULTRON_EXECUTION_BACKEND:-simulation}"

exec ./.venv/bin/streamlit run dashboard/financial_dashboard.py
