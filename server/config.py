import os, json, logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("hydra")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

HOST           = os.getenv("HOST", "0.0.0.0")
PORT           = int(os.getenv("PORT", "8000"))
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
SSH_USER       = os.getenv("SSH_USER", "root")
SSH_PASS       = os.getenv("SSH_PASS", "keenetic")

HYDRA_FILE = DATA_DIR / "hydra_config.json"
ROUTERS_FILE = DATA_DIR / "routers.json"

def ensure_data():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    defaults = {
        HYDRA_FILE:   {"version":"1.0","domain_groups":[],"ip_groups":[]},
        ROUTERS_FILE: {},
    }
    for fp, default in defaults.items():
        if not fp.exists():
            fp.write_text(json.dumps(default, ensure_ascii=False, indent=2))

ensure_data()
