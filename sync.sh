#!/usr/bin/env bash
# sync.sh — Quick add, commit, and push to GitHub
set -euo pipefail
cd "$(dirname "$0")"

MSG="${1:-Auto-sync $(date '+%Y-%m-%d %H:%M')}"

echo "Syncing LLTimmy to GitHub..."
git add -A
git status --short

if git diff --cached --quiet; then
    echo "Nothing to commit."
    exit 0
fi

git commit -m "$MSG"
git push origin main
echo "Done — pushed to origin/main."
