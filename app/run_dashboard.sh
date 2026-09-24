#!/usr/bin/env sh
# Start the local housing dashboard. Extra args pass through, e.g. --port 9000 --profile ~/my-profile
cd "$(dirname "$0")/.." || exit 1
PYTHONDONTWRITEBYTECODE=1 exec python3 -B app/server.py "$@"
