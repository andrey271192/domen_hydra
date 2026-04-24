import hashlib
from .models import HydraConfig, DomainGroup, IpGroup
from . import config
from .database import load_json, save_json

def load_hydra_config():
    d = load_json(config.HYDRA_FILE, {"version":"1.0","domain_groups":[],"ip_groups":[]})
    return HydraConfig(**d)

def save_hydra_config(cfg):
    save_json(config.HYDRA_FILE, cfg.model_dump())

def generate_domain_conf(cfg):
    lines = []
    for g in cfg.domain_groups:
        lines.append(f"##{g.name}")
        e = ",".join(g.entries)
        lines.append(f"{e}/{g.policy}" if g.enabled else f"{e}#/{g.policy}")
    return "\n".join(lines)+"\n" if lines else ""

def generate_ip_list(cfg):
    lines = []
    for g in cfg.ip_groups:
        lines.append(f"##{g.name}")
        lines.append(f"/{g.policy}" if g.enabled else f"#/{g.policy}")
        for e in g.entries: lines.append(e)
    return "\n".join(lines)+"\n" if lines else ""

def get_config_version(cfg):
    return hashlib.sha256((generate_domain_conf(cfg)+generate_ip_list(cfg)).encode()).hexdigest()[:12]

def parse_domain_conf(text):
    groups=[]; cn=None
    for line in text.strip().split("\n"):
        line=line.strip()
        if not line: continue
        if line.startswith("##"): cn=line[2:].strip(); continue
        if "/" in line and cn is not None:
            en=True
            if "#/" in line: p=line.split("#/",1); es=p[0]; pol=p[1]; en=False
            else: p=line.rsplit("/",1); es=p[0]; pol=p[1] if len(p)>1 else "HydraRoute"
            entries=[e.strip() for e in es.split(",") if e.strip()]
            et="geosite" if any(e.startswith("geosite:") for e in entries) else "domain"
            groups.append(DomainGroup(name=cn,entries=entries,policy=pol,entry_type=et,enabled=en)); cn=None
    return groups

def parse_ip_list(text):
    groups=[]; cn=None; cp=None; ce=[]; en=True
    def flush():
        if cn and cp:
            et="geoip" if any(e.startswith("geoip:") for e in ce) else "ip"
            groups.append(IpGroup(name=cn,entries=list(ce),policy=cp,entry_type=et,enabled=en))
    for line in text.strip().split("\n"):
        line=line.strip()
        if not line: continue
        if line.startswith("##"): flush(); cn=line[2:].strip(); cp=None; ce=[]; en=True; continue
        if line.startswith("#/"): en=False; cp=line[2:].strip(); continue
        if line.startswith("/"): cp=line[1:].strip(); continue
        if cp: ce.append(line)
    flush(); return groups
