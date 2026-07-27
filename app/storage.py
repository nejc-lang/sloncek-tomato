import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import settings

STATUS_WAITING = "cakajoce_korak2"
STATUS_DONE = "obdelano"


def _data_dir() -> Path:
    return Path(settings.DATA_DIR)


def _obdelani_dn_file() -> Path:
    return _data_dir() / "obdelani_dn.json"


def _sessions_dir() -> Path:
    return _data_dir() / "sessions"


def _izhodni_zahtevki_dir() -> Path:
    return _data_dir() / "izhodni_zahtevki"


def _ensure_dirs() -> None:
    _sessions_dir().mkdir(parents=True, exist_ok=True)
    _izhodni_zahtevki_dir().mkdir(parents=True, exist_ok=True)


def load_obdelani_dn() -> set[str]:
    """Persistenten seznam že obdelanih DN (ključ `stevilka.leto`).

    Namerno NI "zadnja številka" (x1): Korak 1 vedno išče v datumskem
    razponu danes-1..danes, znotraj katerega številke DN niso nujno strogo
    naraščajoče/brez vrzeli glede na filter (FAKTURIRANA/SKLADISCE), zato je
    potreben eksaktni seznam že videnih DN, ne samo najvišja doslej videna
    številka.
    """
    _ensure_dirs()
    obdelani_file = _obdelani_dn_file()
    if not obdelani_file.exists():
        return set()
    data = json.loads(obdelani_file.read_text(encoding="utf-8"))
    return set(data.get("obdelani", []))


def add_obdelani_dn(keys: set[str]) -> None:
    """Doda nove ključe (`stevilka.leto`) v persistentni seznam obdelanih DN."""
    if not keys:
        return
    _ensure_dirs()
    existing = load_obdelani_dn()
    existing |= keys
    _obdelani_dn_file().write_text(
        json.dumps({"obdelani": sorted(existing)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def new_session_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _session_path(session_id: str) -> Path:
    return _sessions_dir() / f"seja_{session_id}.json"


def save_session(session: dict[str, Any]) -> None:
    _ensure_dirs()
    _session_path(session["session_id"]).write_text(
        json.dumps(session, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def find_latest_session_with_status(status: str) -> dict[str, Any] | None:
    _ensure_dirs()
    for path in sorted(_sessions_dir().glob("seja_*.json"), reverse=True):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") == status:
            return data
    return None


def output_request_path(pre_stevilka: str, timestamp: str) -> Path:
    """Pot do arhivske JSON datoteke s telesom zahtevka za novo prevzemnico.

    Zapisuje se za vsako obdelano prevzemnico v Koraku 2, ne glede na to,
    ali je dejanski POST klic proti eVasco aktiven ali zakomentiran (glej
    app/process.py) - služi kot trajen arhiv in sledljivost delovanja
    aplikacije.
    """
    _ensure_dirs()
    safe_pre_stevilka = pre_stevilka.replace("/", "_").replace(".", "_")
    return _izhodni_zahtevki_dir() / f"prevzemnica_zahtevek_{safe_pre_stevilka}_{timestamp}.json"
