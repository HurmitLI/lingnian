#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
project_root="${script_dir:h}"

export DEMO_PUBLIC_MODE="1"
export DEMO_INVITE_CODES="LINGNIAN-E2E-2026"
export DEMO_SESSION_SECRET="lingnian-e2e-session-secret-32-characters-minimum"

cd "$project_root/frontend"
npm run build
exec ./node_modules/.bin/next start --hostname 127.0.0.1 --port 3022
