#!/bin/sh
set -eu
# One process owns the bounded queue. Azure SQL stores private conversation history.
export PYTHONPATH="$PWD/src:$PWD/vendor/api${PYTHONPATH:+:$PYTHONPATH}"
exec python -m uvicorn pokerfill_ai.service:app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log
