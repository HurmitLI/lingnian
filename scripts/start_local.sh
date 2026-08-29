#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
project_root="${script_dir:h}"
runtime_dir="$project_root/.runtime"
mkdir -p "$runtime_dir"

for port in 8011 3011; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "端口 $port 已被其他程序占用，未启动念念。"
    exit 1
  fi
done

if [[ ! -x "$project_root/backend/.venv/bin/python" ]]; then
  echo "后端虚拟环境不存在，请先按 README 安装依赖。"
  exit 1
fi

if [[ ! -d "$project_root/frontend/node_modules" ]]; then
  echo "前端依赖不存在，请先按 README 安装依赖。"
  exit 1
fi

(
  cd "$project_root/backend"
  .venv/bin/alembic -c alembic.ini upgrade head
  nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8011 \
    > "$runtime_dir/backend.log" 2>&1 &
  echo $! > "$runtime_dir/backend.pid"
)

(
  cd "$project_root/frontend"
  nohup npm run dev > "$runtime_dir/frontend.log" 2>&1 &
  echo $! > "$runtime_dir/frontend.pid"
)

backend_ready=0
frontend_ready=0
for _ in {1..60}; do
  if curl -fsS http://127.0.0.1:8011/api/v1/health >/dev/null 2>&1; then
    backend_ready=1
  fi
  if curl -fsS http://127.0.0.1:3011/ >/dev/null 2>&1; then
    frontend_ready=1
  fi
  if [[ $backend_ready -eq 1 && $frontend_ready -eq 1 ]]; then
    break
  fi
  sleep 1
done

if [[ $backend_ready -ne 1 || $frontend_ready -ne 1 ]]; then
  echo "服务没有按时就绪，请查看 $runtime_dir 下的日志。"
  exit 1
fi

echo "念念已在本机启动：http://127.0.0.1:3011"
echo "API 文档：http://127.0.0.1:8011/docs"
echo "停止服务：$project_root/scripts/stop_local.sh"

