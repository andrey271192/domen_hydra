"""HydraRoute Domain Manager — standalone web server."""
import logging
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
    import asyncio, subprocess
    routers = load_json(config.ROUTERS_FILE, {})
    server_url = str(request.base_url).rstrip("/")
    results = []
    for name, rcfg in routers.items():
        ip = rcfg.get("ip","")
        if not ip: results.append({"router":name,"ok":False,"msg":"нет IP"}); continue
        user = rcfg.get("user") or config.SSH_USER
        pwd  = rcfg.get("password") or config.SSH_PASS
        cmd = (
            f"curl -sf '{server_url}/hydra/domain.conf' -o /opt/etc/HydraRoute/domain.conf && "
            f"curl -sf '{server_url}/hydra/ip.list' -o /opt/etc/HydraRoute/ip.list && "
            f"neo restart"
        )
        try:
            r = await asyncio.to_thread(subprocess.run,
                ["sshpass","-p",pwd,"ssh","-o","StrictHostKeyChecking=no","-o","ConnectTimeout=10",
                 f"{user}@{ip}", cmd], capture_output=True, text=True, timeout=60)
            results.append({"router":name,"ok":r.returncode==0,"msg":r.stdout[:200]})
        except Exception as e:
            results.append({"router":name,"ok":False,"msg":str(e)})
    return {"results": results, "ok": sum(1 for r in results if r["ok"]), "failed": sum(1 for r in results if not r["ok"])}

# ── Routers CRUD ──────────────────────────────────────────────────────────────

@app.get("/api/routers")
async def get_routers():
    return load_json(config.ROUTERS_FILE, {})

@app.post("/api/routers/{name}")
async def upsert_router(name: str, body: dict, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    R = load_json(config.ROUTERS_FILE, {})
    R[name.strip().lower()] = body
    save_json(config.ROUTERS_FILE, R); return {"ok": True}

@app.delete("/api/routers/{name}")
async def delete_router(name: str, x_admin_password: str = Header("")):
    _chk(x_admin_password)
    R = load_json(config.ROUTERS_FILE, {})
    R.pop(name, None); save_json(config.ROUTERS_FILE, R); return {"ok": True}
