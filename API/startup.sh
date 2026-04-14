#!/bin/bash
# Keep Unix (LF) line endings only — CRLF breaks bash on Azure Linux.
PORT="${PORT:-8000}"
exec gunicorn main:app --workers 2 --worker-class uvicorn.workers.UvicornWorker --bind "0.0.0.0:${PORT}"
