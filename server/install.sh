#!/bin/bash
set -e
echo "🌐 HydraRoute Manager — установка"
cd "$(dirname "$0")/.."

apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv sshpass

python3 -m venv .venv
.venv/bin/pip install -q -r server/requirements.txt

[ ! -f server/.env ] && cp server/.env.example server/.env && echo "⚠️  Настрой server/.env и перезапусти"

cat > /etc/systemd/system/hydra-manager.service <<EOF
[Unit]
Description=HydraRoute Manager
After=network.target

[Service]
WorkingDirectory=$(pwd)
ExecStart=$(pwd)/.venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable hydra-manager
systemctl start hydra-manager

echo "✅ Запущен на http://$(curl -sf ifconfig.me 2>/dev/null || echo IP):8000"
