import json
from pathlib import Path

from app.config import settings


def _data_dir() -> Path:
    return Path(settings.DATA_DIR)


def _obdelani_dn_file() -> Path:
    return _data_dir() / "obdelani_dn.json"


def _izhodni_zahtevki_dir() -> Path:
    return _data_dir() / "izhodni_zahtevki"


def _ensure_dirs() -> None:
    _izhodni_zahtevki_dir().mkdir(parents=True, exist_ok=True)


def load_obdelani_dn() -> set[str]:
    """Persistenten seznam že obdelanih DN (ključ `stevilka.leto`).

    Namerno NI "zadnja številka" (x1): iskanje DN vedno preveri v datumskem
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
    """Doda nove ključe (`stevilka.leto`) v persistentni seznam obdelanih DN.

    Kliče se samo za DN, katerih prevzemnica je bila USPEŠNO posodobljena
    (PUT) - DN z napako pri posodobitvi ostane izven seznama, da se njegova
    prevzemnica samodejno poskusi znova ob naslednjem zagonu (PUT je
    idempotenten, v nasprotju s prejšnjim POST/create pristopom ni tveganja
    podvajanja zapisov).
    """
    if not keys:
        return
    _ensure_dirs()
    existing = load_obdelani_dn()
    existing |= keys
    _obdelani_dn_file().write_text(
        json.dumps({"obdelani": sorted(existing)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def output_update_path(pre_stevilka: str, timestamp: str) -> Path:
    """Pot do arhivske JSON datoteke s telesom PUT zahtevka za posodobitev prevzemnice.

    Zapisuje se za vsako obdelano prevzemnico - služi kot trajen arhiv in
    sledljivost delovanja aplikacije.
    """
    _ensure_dirs()
    safe_pre_stevilka = pre_stevilka.replace("/", "_").replace(".", "_")
    return _izhodni_zahtevki_dir() / f"prevzemnica_posodobitev_{safe_pre_stevilka}_{timestamp}.json"
