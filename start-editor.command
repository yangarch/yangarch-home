#!/bin/zsh
set -eu
cd "$(dirname "$0")"
python3 scripts/setup_local.py
if [[ ! -x .local/editor-venv/bin/python ]]; then
  python3 -m venv .local/editor-venv
fi
if ! .local/editor-venv/bin/python -c 'import PIL; assert PIL.__version__ == "13.0.0"' >/dev/null 2>&1; then
  .local/editor-venv/bin/python -m pip install --requirement requirements-editor.txt
fi
exec .local/editor-venv/bin/python scripts/editor.py --open
