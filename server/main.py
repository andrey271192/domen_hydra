"""HydraRoute Domain Manager — standalone web server."""
import asyncio
import json as _json
import logging
import os
import re
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pydantic import BaseModel
from . import config
from .database import load_json, save_json
from .hydra_manager import (load_hydra_config, save_hydra_config,
    generate_domain_conf, generate_ip_list, get_config_version,
    parse_domain_conf, parse_ip_list)
from .models import DomainGroup, IpGroup, HydraConfig

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="HydraRoute Domain Manager")

def _chk(pwd: str):
    if config.ADMIN_PASSWORD and pwd != config.ADMIN_PASSWORD:
        raise HTTPException(401, "Неверный пароль")


def _router_key(name: str) -> str:
    return name.strip().lower()


def _get_router_cfg(name: str) -> dict:
    routers = load_json(config.ROUTERS_FILE, {})
    key = _router_key(name)
    if key not in routers:
        raise HTTPException(404, "роутер не найден")
    return routers[key]


def _normalize_router_ip(value: str) -> str:
    """Убрать https:// из поля IP: для SSH нужен хост или IP, не веб-URL."""
    s = (value or "").strip()
    if not s:
        return ""
    if "://" in s:
        p = urlparse(s if s.startswith(("http://", "https://")) else "https://" + s)
        return (p.hostname or "").strip() or s.split("/")[0].split("@")[-1].strip()
    return s.split("/")[0].strip()


async def _ssh_on_router(rcfg: dict, remote_cmd: str, timeout: int = 45) -> tuple[int, str, str]:
    """Выполнить команду на роутере по SSH (прямо или через тоннель). Возвращает (код, stdout, stderr)."""
    tunnel_port = rcfg.get("tunnel_port")
    if tunnel_port:
        ssh_host = "127.0.0.1"
        extra_args = ["-p", str(int(tunnel_port))]
    else:
        ip = (rcfg.get("ip") or "").strip()
        if not ip:
            return 1, "", "нет IP и нет тоннеля (добавь IP или настрой тоннель)"
        ssh_host = ip
        extra_args = []
        # Optional direct SSH port (default: 22). Tunnel uses its own port args above.
        try:
            p = int(rcfg.get("ssh_port") or 22)
            if p and p != 22:
                extra_args = ["-p", str(p)]
        except Exception:
            pass
    user = rcfg.get("user") or config.SSH_USER
    pwd = rcfg.get("password") or config.SSH_PASS
    ssh_opts = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
    ]
    try:
        r = await asyncio.to_thread(
            subprocess.run,
            [
                "sshpass", "-p", pwd,
                "ssh", *ssh_opts, "-o", "ConnectTimeout=12",
                *extra_args,
                f"{user}@{ssh_host}", remote_cmd,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except Exception as e:
        return 1, "", str(e)


async def _push_one_router(server_url: str, router_key: str, rcfg: dict) -> dict:
    """Скачать с manager domain.conf + ip.list на роутер по SSH (прямо или через тоннель) и neo restart."""
    ip = rcfg.get("ip", "")
    tunnel_port = rcfg.get("tunnel_port")
    if not ip and not tunnel_port:
        return {"router": router_key, "ok": False, "msg": "нет IP и нет тоннеля"}
    user = rcfg.get("user") or config.SSH_USER
    pwd = rcfg.get("password") or config.SSH_PASS
    ssh_opts = [
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
    ]
    cmd = (
        f"curl -sf '{server_url}/hydra/domain.conf' -o /opt/etc/HydraRoute/domain.conf && "
        f"curl -sf '{server_url}/hydra/ip.list' -o /opt/etc/HydraRoute/ip.list && "
        f"neo restart"
    )
    if tunnel_port:
        ssh_cmd = ["sshpass", "-p", pwd, "ssh",
                   *ssh_opts, "-o", "ConnectTimeout=10",
                   "-p", str(int(tunnel_port)), f"{user}@127.0.0.1", cmd]
    else:
        port_args: list[str] = []
        try:
            p = int(rcfg.get("ssh_port") or 22)
            if p and p != 22:
                port_args = ["-p", str(p)]
        except Exception:
            port_args = []
        ssh_cmd = ["sshpass", "-p", pwd, "ssh",
                   *ssh_opts, "-o", "ConnectTimeout=10",
                   *port_args,
                   f"{user}@{ip}", cmd]
    try:
        r = await asyncio.to_thread(subprocess.run, ssh_cmd, capture_output=True, text=True, timeout=60)
        tail = ((r.stdout or "") + (r.stderr or ""))[:400]
        return {
            "router": router_key,
            "ok": r.returncode == 0,
            "msg": tail or ("ok" if r.returncode == 0 else "exit " + str(r.returncode)),
        }
    except Exception as e:
        return {"router": router_key, "ok": False, "msg": str(e)}


# ── Pages ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    with open(config.BASE_DIR / "templates" / "index.html", encoding="utf-8") as f:
        return f.read()

# ── Auth ─────────────────────────────────────────────────────────────────────

@app.get("/api/auth")
async def auth(x_admin_password: str = Header("")):
    _chk(x_admin_password); return {"ok": True}

@app.post("/api/set_password")
async def set_password(body: dict, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    new_pwd = (body.get("password") or "").strip()
    if len(new_pwd) < 4: raise HTTPException(400, "Минимум 4 символа")
    import re
    env_path = config.BASE_DIR / ".env"
    if env_path.exists():
        txt = env_path.read_text()
        txt = re.sub(r"ADMIN_PASSWORD=.*", f"ADMIN_PASSWORD={new_pwd}", txt) if "ADMIN_PASSWORD" in txt else txt + f"\nADMIN_PASSWORD={new_pwd}\n"
        env_path.write_text(txt)
    config.ADMIN_PASSWORD = new_pwd
    return {"ok": True}

# ── HydraRoute files (served to routers) ─────────────────────────────────────

@app.get("/hydra/domain.conf")
async def domain_conf():
    return Response(content=generate_domain_conf(load_hydra_config()), media_type="text/plain")

@app.get("/hydra/ip.list")
async def ip_list():
    return Response(content=generate_ip_list(load_hydra_config()), media_type="text/plain")

@app.get("/hydra/version")
async def version():
    return Response(content=get_config_version(load_hydra_config()), media_type="text/plain")

@app.get("/hydra/config")
async def hydra_config():
    return load_hydra_config().model_dump()

# ── Domain groups ─────────────────────────────────────────────────────────────

@app.post("/api/domain-group")
async def upsert_domain_group(g: DomainGroup, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    cfg = load_hydra_config()
    cfg.domain_groups = [x for x in cfg.domain_groups if x.name != g.name]
    cfg.domain_groups.append(g)
    save_hydra_config(cfg); return {"ok": True}

@app.delete("/api/domain-group/{name}")
async def delete_domain_group(name: str, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    cfg = load_hydra_config()
    cfg.domain_groups = [x for x in cfg.domain_groups if x.name != name]
    save_hydra_config(cfg); return {"ok": True}

# ── IP groups ─────────────────────────────────────────────────────────────────

@app.post("/api/ip-group")
async def upsert_ip_group(g: IpGroup, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    cfg = load_hydra_config()
    cfg.ip_groups = [x for x in cfg.ip_groups if x.name != g.name]
    cfg.ip_groups.append(g)
    save_hydra_config(cfg); return {"ok": True}

@app.delete("/api/ip-group/{name}")
async def delete_ip_group(name: str, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    cfg = load_hydra_config()
    cfg.ip_groups = [x for x in cfg.ip_groups if x.name != name]
    save_hydra_config(cfg); return {"ok": True}

# ── Import ────────────────────────────────────────────────────────────────────

class ImportBody(BaseModel):
    domain_conf: str = ""; ip_list: str = ""

@app.post("/api/import")
async def import_config(body: ImportBody, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    cfg = load_hydra_config()
    if body.domain_conf.strip():
        cfg.domain_groups = parse_domain_conf(body.domain_conf)
    if body.ip_list.strip():
        cfg.ip_groups = parse_ip_list(body.ip_list)
    save_hydra_config(cfg); return {"ok": True, "domains": len(cfg.domain_groups), "ips": len(cfg.ip_groups)}

# ── Push to all routers via SSH ───────────────────────────────────────────────

@app.post("/api/push_all")
async def push_all(request: Request, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    routers = load_json(config.ROUTERS_FILE, {})
    server_url = str(request.base_url).rstrip("/")
    results = []
    for name, rcfg in routers.items():
        results.append(await _push_one_router(server_url, name, rcfg))
    return {"results": results, "ok": sum(1 for r in results if r["ok"]), "failed": sum(1 for r in results if not r["ok"])}

# ── Routers CRUD ──────────────────────────────────────────────────────────────

@app.get("/api/routers")
async def get_routers():
    return load_json(config.ROUTERS_FILE, {})

@app.post("/api/routers/{name}")
async def upsert_router(name: str, body: dict, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    body = dict(body)
    if isinstance(body.get("ip"), str):
        body["ip"] = _normalize_router_ip(body["ip"])
    R = load_json(config.ROUTERS_FILE, {})
    R[_router_key(name)] = body
    save_json(config.ROUTERS_FILE, R); return {"ok": True}


@app.post("/api/routers/{name}/test")
async def test_router(name: str, x_admin_password: str = Header("")):
    """Проверка SSH: echo + каталог HydraRoute + наличие файлов."""
    _chk(x_admin_password)
    rcfg = _get_router_cfg(name)
    script = (
        "echo HM_SSH_OK; (uname -n 2>/dev/null || hostname 2>/dev/null || echo unknown); "
        "found=0; for d in /opt/etc/HydraRoute /opt/etc/hydra; do "
        "if test -d \"$d\"; then found=1; echo HM_HR_DIR:$d; "
        "if test -f \"$d/domain.conf\"; then echo HM_HAS_DOMAIN; else echo HM_NO_DOMAIN; fi; "
        "if test -f \"$d/ip.list\"; then echo HM_HAS_IP; else echo HM_NO_IP; fi; "
        "break; fi; done; "
        "if test \"$found\" = 0; then echo HM_NO_HR_DIR; fi"
    )
    code, out, err = await _ssh_on_router(rcfg, script)
    text = (out + (("\n" + err) if err.strip() else "")).strip()
    ok = code == 0 and "HM_SSH_OK" in out
    return {"ok": ok, "exit_code": code, "detail": text or (err or "пустой вывод")}


@app.get("/api/routers/{name}/fetch")
async def fetch_from_router(name: str, x_admin_password: str = Header("")):
    """Считать domain.conf и ip.list с роутера (HydraRoute или hydra)."""
    _chk(x_admin_password)
    rcfg = _get_router_cfg(name)
    read_one = (
        "sh -c 'for d in /opt/etc/HydraRoute /opt/etc/hydra; do "
        "if test -f \"$d/{file}\"; then cat \"$d/{file}\"; exit 0; fi; done; exit 1'"
    )
    code_d, domain_conf, err_d = await _ssh_on_router(rcfg, read_one.format(file="domain.conf"))
    code_i, ip_list, err_i = await _ssh_on_router(rcfg, read_one.format(file="ip.list"))
    return {
        "domain_conf": domain_conf,
        "ip_list": ip_list,
        "domain_ok": code_d == 0,
        "ip_ok": code_i == 0,
        "errors": {"domain": err_d if code_d else "", "ip": err_i if code_i else ""},
    }


@app.post("/api/routers/{name}/push")
async def push_one_router(name: str, request: Request, x_admin_password: str = Header("")):
    """Отправить текущий domain.conf + ip.list только на один роутер."""
    _chk(x_admin_password)
    key = _router_key(name)
    rcfg = _get_router_cfg(name)
    server_url = str(request.base_url).rstrip("/")
    return await _push_one_router(server_url, key, rcfg)


@app.delete("/api/routers/{name}")
async def delete_router(name: str, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    R = load_json(config.ROUTERS_FILE, {})
    R.pop(_router_key(name), None)
    save_json(config.ROUTERS_FILE, R)
    return {"ok": True}


# ── Tunnel helpers ────────────────────────────────────────────────────────────

def _kill_tunnel_port(port: int) -> None:
    """Убить процессы, удерживающие reverse-tunnel порт на VPS (127.0.0.1:PORT)."""
    try:
        # fuser -k работает на большинстве Linux
        subprocess.run(["fuser", "-k", f"{port}/tcp"],
                       capture_output=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        # Fallback: ss + kill
        r = subprocess.run(
            ["bash", "-c",
             f"ss -tlnp 'sport = :{port}' | grep -oP 'pid=\\K[0-9]+' | xargs -r kill"],
            capture_output=True, timeout=5,
        )
        _ = r  # noqa
    except Exception:
        pass


def _gen_ed25519_keypair(name: str) -> tuple[str, str]:
    """Генерировать ed25519 keypair на VPS через ssh-keygen. Возвращает (private_pem, public_openssh)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        keyfile = os.path.join(tmpdir, "k")
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", keyfile, "-N", "", "-q",
             "-C", f"hydra-tunnel-{name}"],
            check=True, timeout=10,
        )
        priv = Path(keyfile).read_text()
        pub = Path(keyfile + ".pub").read_text().strip()
    return priv, pub


def _add_pubkey_to_authorized_keys(name: str, pubkey: str) -> None:
    """Добавить pubkey в ~/.ssh/authorized_keys (де-дуп по комменту hydra-tunnel-{name})."""
    auth_dir = Path.home() / ".ssh"
    auth_dir.mkdir(mode=0o700, exist_ok=True)
    auth_path = auth_dir / "authorized_keys"
    comment = f"hydra-tunnel-{name}"
    lines: list[str] = []
    if auth_path.exists():
        lines = [l for l in auth_path.read_text().splitlines() if comment not in l and l.strip()]
    lines.append(pubkey)
    auth_path.write_text("\n".join(lines) + "\n")
    auth_path.chmod(0o600)
    try:
        auth_dir.chmod(0o700)
    except OSError:
        pass


# ── Tunnel endpoints ──────────────────────────────────────────────────────────

@app.get("/api/routers/{name}/tunnel-cmd")
async def tunnel_cmd(name: str, x_admin_password: str = Header("")):
    """Назначить порт, сгенерить keypair, вернуть curl|sh команду для роутера."""
    _chk(x_admin_password)
    if not config.VPS_SSH_HOST:
        raise HTTPException(400, "VPS_SSH_HOST не задан в server/.env — укажи публичный IP/домен VPS")

    key = _router_key(name)
    R = load_json(config.ROUTERS_FILE, {})
    if key not in R:
        raise HTTPException(404, "Роутер не найден")
    rcfg = dict(R[key])

    # Порт
    if rcfg.get("tunnel_port"):
        port = int(rcfg["tunnel_port"])
        # Убить старые зависшие соединения на этом порту (VPS-сторона)
        await asyncio.to_thread(_kill_tunnel_port, port)
    else:
        used = {int(v.get("tunnel_port")) for v in R.values() if v.get("tunnel_port")}
        port = config.TUNNEL_PORT_START
        while port in used:
            port += 1
        rcfg["tunnel_port"] = port

    # Keypair — генерируем один раз, но authorized_keys обновляем всегда
    if not rcfg.get("tunnel_priv_key") or not rcfg.get("tunnel_pub_key"):
        try:
            priv, pub = await asyncio.to_thread(_gen_ed25519_keypair, name)
        except FileNotFoundError as e:
            raise HTTPException(500, "ssh-keygen не найден на VPS — установи openssh-client") from e
        except subprocess.CalledProcessError as e:
            raise HTTPException(500, f"ssh-keygen упал: {e}") from e
        rcfg["tunnel_priv_key"] = priv
        rcfg["tunnel_pub_key"] = pub
    # Всегда синхронизируем authorized_keys (защита от ручного удаления)
    await asyncio.to_thread(_add_pubkey_to_authorized_keys, name, rcfg["tunnel_pub_key"])

    # Одноразовый токен (10 мин)
    reg_token = secrets.token_urlsafe(32)
    rcfg["tunnel_reg_token"] = reg_token
    rcfg["tunnel_reg_token_exp"] = int(time.time()) + 600

    R[key] = rcfg
    save_json(config.ROUTERS_FILE, R)

    http_url = f"http://{config.VPS_SSH_HOST}:{config.PORT}"
    one_liner = f"curl -fsS '{http_url}/api/routers/{name}/tunnel-script?token={reg_token}' | sh"

    return {"tunnel_port": port, "cmd": one_liner}


@app.get("/api/routers/{name}/tunnel-script")
async def tunnel_script(name: str, token: str):
    """Установочный скрипт для роутера (с приватным ключом внутри). Auth — одноразовый токен."""
    key = _router_key(name)
    R = load_json(config.ROUTERS_FILE, {})
    if key not in R:
        raise HTTPException(404, "Роутер не найден")
    rcfg = dict(R[key])

    saved = rcfg.get("tunnel_reg_token")
    if not saved or not secrets.compare_digest(saved, token or ""):
        raise HTTPException(403, "Неверный или израсходованный токен")
    if time.time() > int(rcfg.get("tunnel_reg_token_exp") or 0):
        raise HTTPException(403, "Токен истёк (10 мин). Открой модалку заново.")
    if not rcfg.get("tunnel_priv_key") or not rcfg.get("tunnel_port"):
        raise HTTPException(500, "Тоннель не подготовлен — открой модалку заново")

    # Токен одноразовый — расходуем
    rcfg.pop("tunnel_reg_token", None)
    rcfg.pop("tunnel_reg_token_exp", None)
    R[key] = rcfg
    save_json(config.ROUTERS_FILE, R)

    port = int(rcfg["tunnel_port"])
    priv_key = rcfg["tunnel_priv_key"].strip()
    vps_host = config.VPS_SSH_HOST
    vps_port = config.VPS_SSH_PORT
    vps_user = config.VPS_SSH_USER

    script = f"""#!/bin/sh
set -e
export PATH="/opt/bin:/opt/sbin:/bin:/sbin:/usr/bin:/usr/sbin:$PATH"

echo '[1/4] autossh...'
opkg install autossh openssh-client 2>/dev/null || true
command -v autossh >/dev/null 2>&1 || {{ echo 'ОШИБКА: autossh не установлен. Запусти opkg update и повтори.'; exit 1; }}

echo '[2/4] Приватный ключ...'
mkdir -p /opt/etc
cat > /opt/etc/hydra_tk <<'KEYEOF'
{priv_key}
KEYEOF
chmod 600 /opt/etc/hydra_tk

echo '[3/4] Скрипт тоннеля + автозапуск...'
cat > /opt/bin/hydra_tun <<'RUNEOF'
#!/bin/sh
PATH="/opt/bin:/opt/sbin:/bin:/sbin:/usr/bin:/usr/sbin:$PATH"
export AUTOSSH_GATETIME=0
export AUTOSSH_LOGFILE=/tmp/hydra_tun.log
exec autossh -M 0 \\
  -i /opt/etc/hydra_tk \\
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \\
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \\
  -o ExitOnForwardFailure=yes -o IdentitiesOnly=yes \\
  -N -R {port}:localhost:22 {vps_user}@{vps_host} -p {vps_port}
RUNEOF
chmod +x /opt/bin/hydra_tun

cat > /opt/etc/init.d/S99hydra_tun <<'INITEOF'
#!/bin/sh
case "$1" in
  start)
    killall -0 autossh 2>/dev/null && exit 0
    /opt/sbin/start-stop-daemon -S -b -x /opt/bin/hydra_tun 2>/dev/null || \
      ( nohup /opt/bin/hydra_tun </dev/null >/dev/null 2>&1 & )
    ;;
  stop)    killall autossh 2>/dev/null ;;
  restart) killall autossh 2>/dev/null; sleep 1
    /opt/sbin/start-stop-daemon -S -b -x /opt/bin/hydra_tun 2>/dev/null || \
      ( nohup /opt/bin/hydra_tun </dev/null >/dev/null 2>&1 & )
    ;;
esac
INITEOF
chmod +x /opt/etc/init.d/S99hydra_tun

# Cron-watchdog: каждую минуту проверяет autossh и перезапускает если упал
CRON_JOB="* * * * * killall -0 autossh 2>/dev/null || /opt/etc/init.d/S99hydra_tun start"
( crontab -l 2>/dev/null | grep -v 'S99hydra_tun'; echo "$CRON_JOB" ) | crontab -
echo 'Watchdog cron установлен'

echo '[4/4] Запуск...'
killall autossh 2>/dev/null || true
sleep 1
rm -f /tmp/hydra_tun.log
if /opt/sbin/start-stop-daemon -S -b -x /opt/bin/hydra_tun 2>/dev/null; then
  :
elif command -v setsid >/dev/null 2>&1; then
  setsid /opt/bin/hydra_tun </dev/null >/dev/null 2>&1 &
else
  ( nohup /opt/bin/hydra_tun </dev/null >/dev/null 2>&1 & )
fi
sleep 5
if killall -0 autossh 2>/dev/null; then
  echo
  echo '=== OK ==='
  echo 'Тоннель: localhost:22 (роутер) -> VPS:{port}'
  echo 'Лог: /tmp/hydra_tun.log'
  echo 'Возвращайся в браузер и жми "Проверить связь"'
else
  echo
  echo '=== ОШИБКА: autossh не запустился в фоне ==='
  echo '--- /tmp/hydra_tun.log ---'
  cat /tmp/hydra_tun.log 2>/dev/null || echo '(лог пустой)'
  echo '-------------------------'
  echo 'Тест вручную: /opt/bin/hydra_tun  (Ctrl+C для выхода)'
  exit 1
fi
"""
    return PlainTextResponse(script, media_type="text/plain; charset=utf-8")


@app.get("/api/routers/{name}/tunnel-status")
async def tunnel_status(name: str, x_admin_password: str = Header("")):
    """Проверить: слушает ли тоннельный порт на localhost VPS."""
    _chk(x_admin_password)
    key = _router_key(name)
    R = load_json(config.ROUTERS_FILE, {})
    if key not in R:
        raise HTTPException(404, "Роутер не найден")
    port = R[key].get("tunnel_port")
    if not port:
        return {"active": False, "reason": "tunnel_port не назначен"}

    def _check() -> bool:
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=2):
                return True
        except OSError:
            return False

    active = await asyncio.to_thread(_check)
    return {"active": active, "tunnel_port": port}


@app.delete("/api/routers/{name}/tunnel")
async def tunnel_remove(name: str, x_admin_password: str = Header("")):
    """Снять тоннельный порт с роутера."""
    _chk(x_admin_password)
    key = _router_key(name)
    R = load_json(config.ROUTERS_FILE, {})
    if key not in R:
        raise HTTPException(404, "Роутер не найден")
    rcfg = dict(R[key])
    rcfg.pop("tunnel_port", None)
    R[key] = rcfg
    save_json(config.ROUTERS_FILE, R)
    return {"ok": True}


# ── WireGuard VPN ─────────────────────────────────────────────────────────────

_WG_DATA = config.DATA_DIR / "wireguard.json"
_WG_SUBNET = "10.8.0"


def _load_wg() -> dict:
    return load_json(_WG_DATA, {"server": {}, "peers": {}})


def _save_wg(d: dict):
    save_json(_WG_DATA, d)


def _wg_genkey() -> tuple[str, str]:
    r = subprocess.run(["wg", "genkey"], capture_output=True, text=True, timeout=5)
    if r.returncode != 0:
        raise RuntimeError("wg не найден. На VPS: apt install wireguard-tools")
    priv = r.stdout.strip()
    pub = subprocess.run(["wg", "pubkey"], input=priv, capture_output=True, text=True, timeout=5).stdout.strip()
    return priv, pub


def _wg_next_ip(peers: dict) -> str:
    used = {int(v["ip"].split(".")[-1]) for v in peers.values() if v.get("ip")}
    for i in range(2, 255):
        if i not in used:
            return f"{_WG_SUBNET}.{i}"
    raise RuntimeError("Нет свободных IP в WG-подсети")


def _wg_server_conf(data: dict) -> str:
    srv = data["server"]
    try:
        r = subprocess.run(["ip", "route", "get", "8.8.8.8"], capture_output=True, text=True, timeout=5)
        m = re.search(r"dev (\S+)", r.stdout)
        iface = m.group(1) if m else "eth0"
    except Exception:
        iface = "eth0"
    lines = [
        "[Interface]",
        f"PrivateKey = {srv['private_key']}",
        f"Address = {_WG_SUBNET}.1/24",
        f"ListenPort = {srv.get('port', 51820)}",
        f"PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o {iface} -j MASQUERADE",
        f"PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o {iface} -j MASQUERADE",
        "",
    ]
    for rname, peer in data.get("peers", {}).items():
        lines += [f"# {rname}", "[Peer]", f"PublicKey = {peer['public_key']}",
                  f"AllowedIPs = {peer['ip']}/32", ""]
    return "\n".join(lines)


def _wg_client_conf(data: dict, rkey: str, vps_host: str) -> str:
    srv = data["server"]
    peer = data["peers"][rkey]
    return (
        "[Interface]\n"
        f"PrivateKey = {peer['private_key']}\n"
        f"Address = {peer['ip']}/32\n"
        "DNS = 8.8.8.8\n\n"
        "[Peer]\n"
        f"PublicKey = {srv['public_key']}\n"
        f"Endpoint = {vps_host}:{srv.get('port', 51820)}\n"
        "AllowedIPs = 0.0.0.0/0\n"
        "PersistentKeepalive = 25\n"
    )


def _wg_reload(conf: str):
    p = Path("/etc/wireguard/wg0.conf")
    p.write_text(conf)
    p.chmod(0o600)
    subprocess.run(
        ["bash", "-c", "wg syncconf wg0 <(wg-quick strip /etc/wireguard/wg0.conf) 2>/dev/null || true"],
        timeout=10,
    )


@app.get("/api/wireguard")
async def wg_get(x_admin_password: str = Header("")):
    _chk(x_admin_password)
    data = _load_wg()
    srv = data.get("server", {})
    running = False
    if srv.get("private_key"):
        try:
            running = subprocess.run(["wg", "show", "wg0"], capture_output=True, timeout=5).returncode == 0
        except Exception:
            pass
    return {
        "initialized": bool(srv.get("private_key")),
        "running": running,
        "public_key": srv.get("public_key", ""),
        "port": srv.get("port", 51820),
        "peers": {k: {"ip": v.get("ip"), "public_key": v.get("public_key")}
                  for k, v in data.get("peers", {}).items()},
    }


@app.post("/api/wireguard/init")
async def wg_init_server(x_admin_password: str = Header("")):
    _chk(x_admin_password)
    data = _load_wg()
    if not data["server"].get("private_key"):
        priv, pub = await asyncio.to_thread(_wg_genkey)
        data["server"] = {"private_key": priv, "public_key": pub, "port": 51820}
        _save_wg(data)
    conf = _wg_server_conf(data)

    def _setup():
        subprocess.run(["apt-get", "install", "-y", "--no-install-recommends", "wireguard"],
                       capture_output=True, timeout=120)
        subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"], capture_output=True, timeout=5)
        Path("/etc/sysctl.d/99-wg.conf").write_text("net.ipv4.ip_forward=1\n")
        Path("/etc/wireguard").mkdir(parents=True, exist_ok=True)
        _wg_reload(conf)
        subprocess.run(["systemctl", "enable", "wg-quick@wg0"], capture_output=True, timeout=10)
        subprocess.run(["systemctl", "restart", "wg-quick@wg0"], capture_output=True, timeout=30)

    await asyncio.to_thread(_setup)
    running = subprocess.run(["wg", "show", "wg0"], capture_output=True, timeout=5).returncode == 0
    return {"ok": True, "running": running, "public_key": data["server"]["public_key"]}


@app.get("/api/wireguard/server-config", response_class=PlainTextResponse)
async def wg_server_config(x_admin_password: str = Header("")):
    _chk(x_admin_password)
    data = _load_wg()
    if not data["server"].get("private_key"):
        raise HTTPException(400, "WireGuard не инициализирован")
    return _wg_server_conf(data)


@app.post("/api/routers/{name}/wireguard")
async def wg_add_peer(name: str, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    data = _load_wg()
    if not data["server"].get("private_key"):
        raise HTTPException(400, "Сначала инициализируй WireGuard сервер")
    key = _router_key(name)
    peers = data.setdefault("peers", {})
    if key not in peers:
        priv, pub = await asyncio.to_thread(_wg_genkey)
        peers[key] = {"private_key": priv, "public_key": pub, "ip": _wg_next_ip(peers)}
        _save_wg(data)
        await asyncio.to_thread(_wg_reload, _wg_server_conf(data))
    return {"ok": True, "ip": peers[key]["ip"], "public_key": peers[key]["public_key"]}


@app.get("/api/routers/{name}/wireguard-config", response_class=PlainTextResponse)
async def wg_router_config(name: str, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    data = _load_wg()
    key = _router_key(name)
    if key not in data.get("peers", {}):
        raise HTTPException(404, "Пир не найден")
    return _wg_client_conf(data, key, config.VPS_SSH_HOST or "VPS_IP")


@app.post("/api/routers/{name}/wireguard/deploy")
async def wg_deploy(name: str, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    data = _load_wg()
    key = _router_key(name)
    if key not in data.get("peers", {}):
        raise HTTPException(400, "Сначала добавь роутер в WireGuard")
    vps_host = config.VPS_SSH_HOST or ""
    if not vps_host:
        raise HTTPException(400, "VPS_SSH_HOST не задан в .env")
    rcfg = _get_router_cfg(name)
    peer = data["peers"][key]
    srv = data["server"]
    wg_conf = _wg_client_conf(data, key, vps_host)
    priv_key = peer["private_key"]
    pub_key = srv["public_key"]
    port = srv.get("port", 51820)
    client_ip = peer["ip"]

    router_pass = (rcfg.get("password") or config.SSH_PASS).replace("'", r"'\''")
    # Экранируем для встраивания в heredoc shell-скрипта (одинарные кавычки уже обработаны выше)
    priv_key_sh = priv_key.replace("\\", "\\\\")
    pub_key_sh  = pub_key.replace("\\", "\\\\")

    script = f"""\
echo '[1/3] Установка wireguard-tools...'
opkg update 2>/dev/null || true
opkg install wireguard-tools 2>/dev/null || true
command -v wg >/dev/null 2>&1 || {{ echo 'ОШИБКА: wg не установлен'; exit 1; }}

echo '[2/3] Запись конфига...'
mkdir -p /opt/etc/wireguard
printf '%s\\n' '{priv_key_sh}' > /opt/etc/wireguard/wg0.key
chmod 600 /opt/etc/wireguard/wg0.key

echo '[3/3] Настройка WireGuard через Keenetic RCI...'
DONE=0

# === Keenetic RCI API (нативная интеграция, интерфейс виден в UI) ===
# Keenetic слушает на LAN IP роутера — определяем его динамически
RCI_BASE=""
AUTH=""
# Собираем все локальные IPv4 (кроме 127.x) из ip addr
_local_ips=$(ip addr show 2>/dev/null | grep 'inet ' | awk '{{print $2}}' | cut -d/ -f1 | grep -v '^127\\.')
for _ip in 127.0.0.1 $_local_ips; do
  _auth=$(curl -s --connect-timeout 2 "http://$_ip/auth" 2>/dev/null)
  if printf '%s' "$_auth" | grep -q '"realm"'; then
    RCI_BASE="http://$_ip"
    AUTH="$_auth"
    echo "RCI найден на http://$_ip"
    break
  fi
done
echo "RCI base: [$RCI_BASE]  Auth: $AUTH"
REALM=$(printf '%s' "$AUTH" | grep -o '"realm":"[^"]*"' | cut -d'"' -f4)
CHAL=$(printf '%s' "$AUTH" | grep -o '"challenge":"[^"]*"' | cut -d'"' -f4)
echo "Realm=[$REALM] Challenge=[$CHAL]"

if [ -n "$REALM" ] && [ -n "$CHAL" ] && [ -n "$RCI_BASE" ]; then
  HASH=$(printf 'admin:%s:{router_pass}' "$REALM" | md5sum | cut -c1-32)
  RESP=$(printf '%s%s' "$HASH" "$CHAL" | md5sum | cut -c1-32)
  LOGIN=$(curl -s -c /tmp/rci.jar "$RCI_BASE/auth" \\
    -H 'Content-Type: application/json' \\
    -d '{{"login":"admin","password":"'"$RESP"'"}}')
  echo "Login: $LOGIN"

  # Найти следующий свободный последовательный индекс WireGuard
  MAX_WG=-1
  for i in $(seq 0 20); do
    wg show nwg$i >/dev/null 2>&1 && MAX_WG=$i
  done
  WG_IDX=$((MAX_WG + 1))
  echo "Создаём Wireguard${{WG_IDX}} (nwg${{WG_IDX}})"

  # JSON с правильными именами полей Keenetic: allowed-address, keepalive
  cat > /tmp/wg_rci.json << RCIEOF
{{"interface":{{"Wireguard${{WG_IDX}}":{{"description":"HydraVPN","up":true,"address":[{{"ip":"{client_ip}","mask":"255.255.255.255"}}],"wireguard":{{"private-key":"{priv_key_sh}","peer":[{{"public-key":"{pub_key_sh}","endpoint":{{"address":"{vps_host}","port":{port}}},"allowed-address":[{{"ip":"0.0.0.0","mask":"0.0.0.0"}}],"keepalive":25}}]}}}}}}}}
RCIEOF
  echo "RCI JSON: $(cat /tmp/wg_rci.json)"

  CFG=$(curl -s -b /tmp/rci.jar -X POST "$RCI_BASE/rci/" \\
    -H 'Content-Type: application/json' \\
    --data-binary @/tmp/wg_rci.json)
  echo "RCI response: $CFG"

  curl -s -b /tmp/rci.jar -X POST "$RCI_BASE/rci/" \\
    -H 'Content-Type: application/json' \\
    -d '{{"system":{{"configuration":{{"save":true}}}}}}' >/dev/null

  sleep 2
  if wg show nwg${{WG_IDX}} >/dev/null 2>&1; then
    DONE=1
    echo "OK: Wireguard${{WG_IDX}} (nwg${{WG_IDX}}) создан — виден в UI"
  else
    echo "RCI не создал интерфейс. Смотри response выше."
  fi
fi
rm -f /tmp/rci.jar /tmp/wg_rci.json 2>/dev/null

# === Fallback: wg + ip (nwg50+, без UI) ===
if [ "$DONE" = "0" ]; then
  echo 'RCI не сработал → fallback wg + ip (nwg50+)...'
  WG_IDX=50
  while wg show nwg${{WG_IDX}} >/dev/null 2>&1; do
    WG_IDX=$((WG_IDX+1))
  done
  echo "Fallback: создаём nwg${{WG_IDX}}"
  ip link add nwg${{WG_IDX}} type wireguard 2>/dev/null || true
  wg set nwg${{WG_IDX}} private-key /opt/etc/wireguard/wg0.key \\
    peer '{pub_key_sh}' allowed-ips 0.0.0.0/0 \\
    endpoint '{vps_host}:{port}' persistent-keepalive 25 && DONE=1 || true
  ip addr add '{client_ip}/32' dev nwg${{WG_IDX}} 2>/dev/null || true
  ip link set up dev nwg${{WG_IDX}} 2>/dev/null || true

  cat > /opt/etc/init.d/S50hydravpn << INITEOF
#!/bin/sh
case "\\$1" in
  start)
    ip link show nwg${{WG_IDX}} >/dev/null 2>&1 || ip link add nwg${{WG_IDX}} type wireguard 2>/dev/null || exit 0
    wg set nwg${{WG_IDX}} private-key /opt/etc/wireguard/wg0.key \\
      peer '{pub_key_sh}' allowed-ips 0.0.0.0/0 \\
      endpoint '{vps_host}:{port}' persistent-keepalive 25
    ip addr add '{client_ip}/32' dev nwg${{WG_IDX}} 2>/dev/null || true
    ip link set up dev nwg${{WG_IDX}}
    ;;
  stop)  ip link set down dev nwg${{WG_IDX}} 2>/dev/null; ip link delete nwg${{WG_IDX}} 2>/dev/null ;;
  restart) \\$0 stop; sleep 1; \\$0 start ;;
esac
INITEOF
  chmod +x /opt/etc/init.d/S50hydravpn
  [ "$DONE" = "1" ] && echo "OK (fallback): nwg${{WG_IDX}} активен. Добавь в Keenetic вручную через UI."
fi

[ "$DONE" = "0" ] && {{ echo 'ОШИБКА: VPN не удалось настроить'; exit 1; }}
echo '=== OK ==='
echo "VPN IP: {client_ip}"
wg show 2>/dev/null || true
"""

    rc, out, err = await _ssh_on_router(rcfg, script, timeout=120)
    return {"ok": rc == 0, "output": (out + err)[:1000]}


@app.delete("/api/routers/{name}/wireguard")
async def wg_remove_peer(name: str, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    data = _load_wg()
    key = _router_key(name)
    data.get("peers", {}).pop(key, None)
    _save_wg(data)
    await asyncio.to_thread(_wg_reload, _wg_server_conf(data))
    return {"ok": True}


@app.post("/api/routers/{name}/wireguard/uninstall")
async def wg_uninstall(name: str, x_admin_password: str = Header("")):
    """Удалить всё установленное сервером с роутера через SSH."""
    _chk(x_admin_password)
    rcfg = _get_router_cfg(name)
    script = """\
echo '=== Удаление HydraVPN с роутера ==='

killall autossh 2>/dev/null && echo 'autossh остановлен' || true

if [ -f /opt/etc/init.d/S50hydravpn ]; then
  /opt/etc/init.d/S50hydravpn stop 2>/dev/null || true
  rm -f /opt/etc/init.d/S50hydravpn && echo 'S50hydravpn удалён'
fi

if [ -f /opt/etc/init.d/S99hydra_tun ]; then
  /opt/etc/init.d/S99hydra_tun stop 2>/dev/null || true
  rm -f /opt/etc/init.d/S99hydra_tun && echo 'S99hydra_tun удалён'
fi

rm -f /opt/bin/hydra_tun && echo 'hydra_tun удалён' || true
rm -f /opt/etc/hydra_tk && echo 'SSH-ключ туннеля удалён' || true
rm -f /opt/etc/wireguard/wg0.conf /opt/etc/wireguard/wg0.key 2>/dev/null && echo 'WireGuard конфиг удалён' || true
rm -f /tmp/hydra_tun.log /tmp/rci.jar /tmp/wg_rci.json 2>/dev/null || true

# Удалить watchdog из crontab
( crontab -l 2>/dev/null | grep -v 'S99hydra_tun' ) | crontab - && echo 'Watchdog cron удалён' || true

for i in $(seq 50 70); do
  ip link show nwg$i >/dev/null 2>&1 && ip link delete nwg$i && echo "nwg$i удалён"
done

echo '=== OK: всё удалено ==='
echo 'Нативные WireGuard-интерфейсы (peer-1, HydraVPN...) удали вручную:'
echo '  Интернет → Другие подключения → WireGuard → Удалить подключение'
"""
    rc, out, err = await _ssh_on_router(rcfg, script, timeout=60)
    return {"ok": rc == 0, "output": (out + err)[:1500]}
