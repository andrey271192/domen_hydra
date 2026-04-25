"""HydraRoute Domain Manager — standalone web server."""
import asyncio
import logging
import subprocess
from urllib.parse import urlparse

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import HTMLResponse, Response
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
    """Выполнить команду на роутере по SSH. Возвращает (код, stdout, stderr)."""
    ip = (rcfg.get("ip") or "").strip()
    if not ip:
        return 1, "", "нет IP (нужен SSH: IP или hostname без https://)"
    user = rcfg.get("user") or config.SSH_USER
    pwd = rcfg.get("password") or config.SSH_PASS
    try:
        r = await asyncio.to_thread(
            subprocess.run,
            [
                "sshpass", "-p", pwd,
                "ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=12",
                f"{user}@{ip}", remote_cmd,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except Exception as e:
        return 1, "", str(e)


async def _push_one_router(server_url: str, router_key: str, rcfg: dict) -> dict:
    """Скачать с manager domain.conf + ip.list на роутер по SSH и neo restart."""
    ip = rcfg.get("ip", "")
    if not ip:
        return {"router": router_key, "ok": False, "msg": "нет IP"}
    user = rcfg.get("user") or config.SSH_USER
    pwd = rcfg.get("password") or config.SSH_PASS
    cmd = (
        f"curl -sf '{server_url}/hydra/domain.conf' -o /opt/etc/HydraRoute/domain.conf && "
        f"curl -sf '{server_url}/hydra/ip.list' -o /opt/etc/HydraRoute/ip.list && "
        f"neo restart"
    )
    try:
        r = await asyncio.to_thread(
            subprocess.run,
            [
                "sshpass", "-p", pwd, "ssh",
                "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                f"{user}@{ip}", cmd,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
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
