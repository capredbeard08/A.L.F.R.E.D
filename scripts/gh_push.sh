#!/usr/bin/env bash
# Push the current branch to origin without the token ever appearing in argv,
# in logs, or in chat output.
#
# Usage:
#   1. Put a fine-grained PAT (Contents: Read and write) in a private file.
#        ~/.github_token          <- default location
#      or: GITHUB_TOKEN_FILE=/path/to/token scripts/gh_push.sh
#   2. chmod 600 that file
#   3. run:  scripts/gh_push.sh
#
# The token is read into a git credential helper at call time and never printed.
set -euo pipefail

TOKEN_FILE="${GITHUB_TOKEN_FILE:-$HOME/.github_token}"
BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"

if [[ ! -s "$TOKEN_FILE" ]]; then
  echo "No token file at $TOKEN_FILE" >&2
  echo "Create it with a fine-grained PAT (Contents: Read and write), then: chmod 600 $TOKEN_FILE" >&2
  exit 1
fi
if [[ ! -r "$TOKEN_FILE" ]]; then
  echo "Token file $TOKEN_FILE is not readable." >&2
  exit 1
fi

# The helper emits credentials from the file. `echo` here is inside the helper,
# so the value goes to git's stdin, never to our argv or stdout.
cred_helper='!f() { echo "username=x-access-token"; echo "password=$(cat '"$TOKEN_FILE"')"; }; f'

echo "Pushing $BRANCH to origin (token: $TOKEN_FILE, value hidden)..."
git -c credential.helper="$cred_helper" push -u origin "$BRANCH"
echo "Push complete."
