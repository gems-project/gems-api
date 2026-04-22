#!/bin/sh
# Oryx installs dependencies into antenv/ during zip deploy. The streamlit
# console script is not always on PATH — use the venv's python -m streamlit.
# Use /bin/sh (POSIX): some App Service Python images do not ship bash.
#
# App path is not always /home/site/wwwroot: Oryx may extract output.tar.zst
# under /tmp/... and run this script from there. Resolve ROOT from this file.
ROOT=$(cd "$(dirname "$0")" && pwd)
PY="${ROOT}/antenv/bin/python"
if [ ! -x "$PY" ]; then
  PY="python3"
fi
exec "$PY" -m streamlit run "${ROOT}/app.py" \
  --server.port "${PORT:-8000}" \
  --server.address 0.0.0.0 \
  --server.headless true \
  --browser.gatherUsageStats false
