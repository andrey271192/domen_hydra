#!/bin/bash
set -e
echo "Удаление HydraRoute Manager (domen_hydra)…"
systemctl stop hydra-manager 2>/dev/null || true
systemctl disable hydra-manager 2>/dev/null || true
rm -f /etc/systemd/system/hydra-manager.service
systemctl daemon-reload 2>/dev/null || true
rm -rf /opt/domen-hydra
echo "Готово."
