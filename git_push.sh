#!/bin/bash
set -e
cd /Users/riddhiparakh/Desktop/py/Projects/SignalEdge

echo "=== GIT STATUS ==="
git status --short | head -20

echo ""
echo "=== STAGED FILES ==="
git diff --cached --name-only | head -20

echo ""
echo "=== LOG ==="
git log --oneline 2>/dev/null | head -5 || echo "(no commits yet)"

echo ""
echo "=== REMOTE ==="
git remote -v
