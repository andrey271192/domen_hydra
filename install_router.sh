#!/bin/sh
set -e
echo "🌐 HydraRoute Manager — установка на роутер"
[ -z "$SERVER_URL" ] && printf "URL сервера (http://IP:8000): " && read SERVER_URL
[ -z "$SERVER_URL" ] && echo "❌ SERVER_URL обязателен" && exit 1

mkdir -p /opt/bin /opt/var/log /opt/var/run

echo "$SERVER_URL" > /opt/etc/hydra_server_url

REPO="https://raw.githubusercontent.com/andrey271192/domen_hydra/main"
curl -fsSL "$REPO/hydra_update.sh" -o /opt/bin/hydra_update.sh
chmod +x /opt/bin/hydra_update.sh

# Add cron: update domains daily at 02:00
CT="/tmp/cron_hydra"
crontab -l 2>/dev/null | grep -v hydra_update > "$CT" || true
echo "0 2 * * * /opt/bin/hydra_update.sh >> /opt/var/log/hydra_update.log 2>&1" >> "$CT"
crontab "$CT" && rm -f "$CT"

# Run immediately
sh /opt/bin/hydra_update.sh

echo "✅ Установлено. Обновление каждый день в 02:00"
