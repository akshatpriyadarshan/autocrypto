#!/usr/bin/env bash
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
G='\033[0;32m'; N='\033[0m'
ok() { echo -e "${G}[OK]${N} $1"; }

command -v python3 >/dev/null 2>&1 || { echo "Python 3.10+ required"; exit 1; }
PY=$(python3 -c "import sys; print(sys.version_info.minor)")
[ "$PY" -ge 10 ] || { echo "Python 3.10+ required (got 3.$PY)"; exit 1; }
ok "Python 3.$PY"

[ -d venv ] || python3 -m venv venv
source venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
ok "Dependencies installed"

mkdir -p data

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${G}  Setup complete!${N}"
echo "  Run: source venv/bin/activate && streamlit run app.py"
echo "  Or:  bash start.sh"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
