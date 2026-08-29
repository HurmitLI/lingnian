#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
project_root="${script_dir:h}"

cd "$project_root"

required_backend_patterns=(
  ".env"
  ".venv/"
  ".vefaas/"
  "data/"
  ".model-cache/"
)
required_frontend_patterns=(
  ".env"
  ".vefaas/"
  "node_modules/"
  ".next/"
  "test-results/"
)

for pattern in "${required_backend_patterns[@]}"; do
  if ! grep -Fxq "$pattern" backend/.vefaasignore; then
    echo "后端部署排除规则缺少：$pattern"
    exit 1
  fi
done

for pattern in "${required_frontend_patterns[@]}"; do
  if ! grep -Fxq "$pattern" frontend/.vefaasignore; then
    echo "前端部署排除规则缺少：$pattern"
    exit 1
  fi
done

tracked_sensitive_paths=$(
  git ls-files \
    | grep -E '(^|/)(\.env($|\.)|\.vefaas/|data/db/|data/assets/|\.model-cache/)' \
    | grep -Ev '(^|/)\.env\.example$|(^|/)\.gitkeep$' \
    || true
)
if [[ -n "$tracked_sensitive_paths" ]]; then
  echo "发现不应进入 Git 或部署包的路径："
  echo "$tracked_sensitive_paths"
  exit 1
fi

if ! command -v vefaas >/dev/null 2>&1; then
  echo "没有找到 vefaas CLI。"
  exit 1
fi

version=$(vefaas --version)
echo "5B 本地安全预检通过。veFaaS CLI：$version"
echo "注意：运行时兼容、登录、持久化、密钥和真实资料上云仍需按 5B 手册完成。"
