#!/bin/sh
SERVER=$(cat /opt/etc/hydra_server_url 2>/dev/null)
[ -z "$SERVER" ] && echo "❌ /opt/etc/hydra_server_url не задан" && exit 1

HYDRA_DIR="/opt/etc/HydraRoute"
[ ! -d "$HYDRA_DIR" ] && HYDRA_DIR="/opt/etc/hydra"
[ ! -d "$HYDRA_DIR" ] && mkdir -p /opt/etc/HydraRoute && HYDRA_DIR="/opt/etc/HydraRoute"

echo "$(date) Обновляю конфиг с $SERVER..."
curl -sf "$SERVER/hydra/domain.conf" -o "$HYDRA_DIR/domain.conf" && echo "✅ domain.conf" || echo "❌ domain.conf"
curl -sf "$SERVER/hydra/ip.list"    -o "$HYDRA_DIR/ip.list"     && echo "✅ ip.list"    || echo "❌ ip.list"

neo restart 2>/dev/null && echo "✅ neo restarted" || echo "⚠️ neo restart failed"
