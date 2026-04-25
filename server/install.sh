#!/bin/bash
set -e
echo "🌐 HydraRoute Manager — установка"
cd "$(dirname "$0")/.."

apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv sshpass

python3 -m venv .venv
.venv/bin/pip install -q -r server/requirements.txt

if [ ! -f server/.env ]; then
  cp server/.env.example server/.env
  echo "⚠️  Создан server/.env из шаблона — замени ADMIN_PASSWORD и SSH_PASS на свои значения, затем: systemctl restart hydra-manager"
fi

# Читаем HOST/PORT из server/.env (только для unit; пароли не трогаем).
_env_val() {
  local k="$1" d="$2" line v
  line=$(grep -E "^[[:space:]]*${k}=" server/.env 2>/dev/null | head -1) || true
  v="${line#*=}"
  v="${v%%$'\r'}"
  v="${v#"${v%%[![:space:]]*}"}"
  v="${v%"${v##*[![:space:]]}"}"
  [ -n "$v" ] && printf '%s' "$v" || printf '%s' "$d"
}
HOST_BIND="$(_env_val HOST 0.0.0.0)"
PORT="$(_env_val PORT 8000)"

cat > /etc/systemd/system/hydra-manager.service <<EOF
[Unit]
Description=HydraRoute Manager
After=network.target

[Service]
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/.venv/bin/uvicorn server.main:app --host ${HOST_BIND} --port ${PORT}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable hydra-manager
systemctl start hydra-manager

echo "✅ Запущен на http://$(curl -sf ifconfig.me 2>/dev/null || echo IP):${PORT}"
