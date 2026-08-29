#!/bin/zsh
set -euo pipefail

script_dir="${0:A:h}"
project_root="${script_dir:h}"
runtime_dir="$project_root/.runtime"
cert_root="$runtime_dir/phone-cert"
public_cert_dir="$cert_root/public"
backend_pid=""
frontend_pid=""
certificate_server_pid=""

phone_ip="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
phone_host="$(scutil --get LocalHostName 2>/dev/null || true).local"
if [[ -z "$phone_ip" ]]; then
  echo "没有找到当前 Mac 的局域网地址，请先连接与手机相同的 Wi-Fi。"
  exit 1
fi

for port in 8011 3011 3012; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "端口 $port 已被占用。请先运行：$project_root/scripts/stop_local.sh"
    exit 1
  fi
done

cleanup() {
  [[ -n "$frontend_pid" ]] && kill "$frontend_pid" >/dev/null 2>&1 || true
  [[ -n "$backend_pid" ]] && kill "$backend_pid" >/dev/null 2>&1 || true
  [[ -n "$certificate_server_pid" ]] && kill "$certificate_server_pid" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

mkdir -p "$cert_root" "$public_cert_dir"
chmod 700 "$cert_root"
export NIANNIAN_PHONE_IP="$phone_ip"
if [[ "$phone_host" == ".local" ]]; then
  phone_host="localhost"
fi
/usr/bin/sed -e "s/__PHONE_IP__/$phone_ip/g" -e "s/__PHONE_HOST__/$phone_host/g" "$script_dir/phone-server.ext" > "$cert_root/server.generated.ext"

if [[ ! -f "$cert_root/ca.key" || ! -f "$cert_root/ca.pem" ]]; then
  openssl req -x509 -newkey rsa:2048 -sha256 -nodes \
    -keyout "$cert_root/ca.key" \
    -out "$cert_root/ca.pem" \
    -days 365 \
    -subj "/CN=Niannian Phone Acceptance CA" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign" >/dev/null 2>&1
fi

openssl req -newkey rsa:2048 -sha256 -nodes \
  -keyout "$cert_root/server.key" \
  -out "$cert_root/server.csr" \
  -subj "/CN=$phone_ip" >/dev/null 2>&1
openssl x509 -req -sha256 \
  -in "$cert_root/server.csr" \
  -CA "$cert_root/ca.pem" \
  -CAkey "$cert_root/ca.key" \
  -CAserial "$cert_root/ca.srl" \
  -CAcreateserial \
  -out "$cert_root/server.pem" \
  -days 30 \
  -extfile "$cert_root/server.generated.ext" >/dev/null 2>&1
openssl x509 -in "$cert_root/ca.pem" -outform der -out "$public_cert_dir/niannian-phone-ca.cer"
chmod 600 "$cert_root"/*.key

(
  cd "$project_root/backend"
  FRONTEND_ORIGIN="https://$phone_host:3011" .venv/bin/alembic -c alembic.ini upgrade head
  FRONTEND_ORIGIN="https://$phone_host:3011" .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8011 > "$runtime_dir/phone-backend.log" 2>&1
) &
backend_pid=$!

(
  cd "$project_root/frontend"
  NIANNIAN_BACKEND_URL="http://127.0.0.1:8011" ./node_modules/.bin/next dev \
    --hostname 0.0.0.0 \
    --port 3011 \
    --experimental-https \
    --experimental-https-key "$cert_root/server.key" \
    --experimental-https-cert "$cert_root/server.pem" \
    --experimental-https-ca "$cert_root/ca.pem" > "$runtime_dir/phone-frontend.log" 2>&1
) &
frontend_pid=$!

python3 -m http.server 3012 --bind 0.0.0.0 --directory "$public_cert_dir" > "$runtime_dir/phone-cert-server.log" 2>&1 &
certificate_server_pid=$!

for _ in {1..120}; do
  if curl -kfsS "https://$phone_ip:3011/api/v1/health" >/dev/null 2>&1; then
    echo "手机验收服务已启动。"
    echo "1. 手机与 Mac 连接同一 Wi-Fi。"
    echo "2. 用手机 Safari 打开 http://$phone_ip:3012/niannian-phone-ca.cer 并允许下载描述文件。"
    echo "3. 在手机设置中安装该描述文件，再到“通用 → 关于本机 → 证书信任设置”启用完全信任。"
    echo "4. 用 Safari 打开 https://$phone_host:3011"
    echo "完成后在这里按 Control-C 停止服务。"
    wait "$frontend_pid" "$backend_pid" "$certificate_server_pid"
    exit 0
  fi
  sleep 1
done

echo "手机验收服务没有按时就绪，请查看 $runtime_dir/phone-*.log。"
exit 1
