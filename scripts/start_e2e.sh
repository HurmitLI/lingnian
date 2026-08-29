#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
project_root="${script_dir:h}"
e2e_runtime_root="$(mktemp -d "${TMPDIR:-/tmp}/niannian-e2e.XXXXXX")"
backend_pid=""
frontend_pid=""

cleanup() {
  [[ -n "$frontend_pid" ]] && kill "$frontend_pid" >/dev/null 2>&1 || true
  [[ -n "$backend_pid" ]] && kill "$backend_pid" >/dev/null 2>&1 || true
  [[ -d "$e2e_runtime_root" && "$e2e_runtime_root" == *"niannian-e2e."* ]] && rm -rf "$e2e_runtime_root"
}
trap cleanup EXIT INT TERM

export APP_ENV="e2e"
export DATABASE_URL="sqlite:///$e2e_runtime_root/e2e.db"
export ASSET_ROOT="$e2e_runtime_root/data"
export ASR_PROVIDER="mock"
export LLM_PROVIDER="mock"
export FRONTEND_ORIGIN="http://127.0.0.1:3021"
export NIANNIAN_BACKEND_URL="http://127.0.0.1:8021"

(
  cd "$project_root/backend"
  .venv/bin/alembic -c alembic.ini upgrade head > "$e2e_runtime_root/migration.log" 2>&1
  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8021 > "$e2e_runtime_root/backend.log" 2>&1
) &
backend_pid=$!

(
  cd "$project_root/frontend"
  npm run build > "$e2e_runtime_root/build.log" 2>&1
  ./node_modules/.bin/next start --hostname 127.0.0.1 --port 3021 > "$e2e_runtime_root/frontend.log" 2>&1
) &
frontend_pid=$!

backend_ready=0
frontend_ready=0
for _ in {1..120}; do
  curl -fsS http://127.0.0.1:8021/api/v1/health >/dev/null 2>&1 && backend_ready=1
  curl -fsS http://127.0.0.1:3021/ >/dev/null 2>&1 && frontend_ready=1
  [[ $backend_ready -eq 1 && $frontend_ready -eq 1 ]] && break
  sleep 1
done

if [[ $backend_ready -ne 1 || $frontend_ready -ne 1 ]]; then
  echo "隔离验收服务没有按时就绪，临时日志目录：$e2e_runtime_root"
  exit 1
fi

wait "$backend_pid" "$frontend_pid"
