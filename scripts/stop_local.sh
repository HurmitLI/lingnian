#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
project_root="${script_dir:h}"
runtime_dir="$project_root/.runtime"

for service in frontend backend; do
  pid_file="$runtime_dir/$service.pid"
  if [[ ! -f "$pid_file" ]]; then
    continue
  fi
  pid=$(<"$pid_file")
  command_line=$(ps -p "$pid" -o command= 2>/dev/null || true)
  process_cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' || true)
  if [[ -n "$command_line" && "$process_cwd" == "$project_root"/* ]]; then
    kill "$pid"
    echo "已停止 $service（PID $pid）。"
    rm -f "$pid_file"
  else
    echo "$service 的 PID 已失效或不属于本项目，未执行停止。"
  fi
done
