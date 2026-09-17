#!/usr/bin/env bash
# Regenerates the Garmin session token and pushes it straight to the
# GARMIN_SESSION_B64 GitHub secret via the gh CLI — no browser copy/paste.
#
# One-time setup (only needed once, ever):
#   brew install gh
#   gh auth login
#
# Usage (whenever the daily workflow starts failing with a 401/auth error):
#   ./scripts/refresh_garmin_secret.sh
#
# You still log in to Garmin yourself, locally, in this terminal — your
# password never goes anywhere else and is never seen by Claude.

set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is not installed. Run: brew install gh"
  echo "Then: gh auth login"
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "gh is installed but not logged in. Run: gh auth login"
  exit 1
fi

echo "Generating a fresh Garmin session (you'll be prompted to log in)..."
python3 scripts/generate_garmin_session.py

if [ ! -f garmin_session_secret.b64 ]; then
  echo "garmin_session_secret.b64 was not created — something went wrong above."
  exit 1
fi

echo "Uploading it to the GARMIN_SESSION_B64 secret on GitHub..."
gh secret set GARMIN_SESSION_B64 < garmin_session_secret.b64

rm garmin_session_secret.b64
echo "Done. Secret updated and local copy deleted."
echo "Re-run the workflow now if you want to confirm: gh workflow run daily-update.yml"
