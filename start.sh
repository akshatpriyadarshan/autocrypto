#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
[ -d venv ] || { echo "Run setup_local.sh first"; exit 1; }
source venv/bin/activate
mkdir -p data
streamlit run app.py --server.port 8501 --server.headless false
