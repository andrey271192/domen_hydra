import json, logging
from pathlib import Path
logger = logging.getLogger("hydra")

def load_json(path: Path, default=None):
    if default is None: default = {}
    if not isinstance(path, Path): path = Path(path)
    try:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if text.strip(): return json.loads(text)
        return default
    except Exception as e:
        logger.error(f"Corrupt {path.name}: {e}"); return default

def save_json(path: Path, data):
    if not isinstance(path, Path): path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        logger.error(f"Save fail {path}: {e}")
