import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import settings

# Zapisi v obdelani_dn.json, starejsi od toliko dni, se ob dnevnem arhiviranju
# (glej app/scheduler.py) premaknejo v data/arhiv/yyyy-mm-dd/obdelani_dn_arhiv.json.
OBDELANI_DN_ARHIV_DNI = 7


def _data_dir() -> Path:
    return Path(settings.DATA_DIR)


def _obdelani_dn_file() -> Path:
    return _data_dir() / "obdelani_dn.json"


def _obdelane_dobavnice_file() -> Path:
    return _data_dir() / "obdelane_dobavnice.json"


def _izhodni_zahtevki_dir() -> Path:
    return _data_dir() / "izhodni_zahtevki"


def _arhiv_dir() -> Path:
    return _data_dir() / "arhiv"


def _ensure_dirs() -> None:
    _izhodni_zahtevki_dir().mkdir(parents=True, exist_ok=True)


def _load_obdelani_dn_zapisi(obdelani_file: Path | None = None) -> dict[str, str]:
    """Persistenten seznam že obdelanih DN kot {ključ (`stevilka.leto`): ISO datum obdelave}.

    Datoteke, zapisane pred uvedbo arhiviranja (2026-07-29), imajo samo plosek
    seznam brez datumov (`{"obdelani": [...]}`) - taki zapisi se ob branju
    migrirajo na TRENUTNI datum. To je namerno konservativno: ker dejanskega
    datuma obdelave ne poznamo, jih ne označimo za prezgodnje arhiviranje -
    7-dnevno odštevanje zanje steče šele od zdaj.
    """
    _ensure_dirs()
    obdelani_file = obdelani_file or _obdelani_dn_file()
    if not obdelani_file.exists():
        return {}
    data = json.loads(obdelani_file.read_text(encoding="utf-8"))
    obdelani = data.get("obdelani")
    if isinstance(obdelani, list):
        zdaj = datetime.now(timezone.utc).isoformat()
        return {kljuc: zdaj for kljuc in obdelani}
    return dict(obdelani or {})


def _write_obdelani_dn_zapisi(zapisi: dict[str, str], obdelani_file: Path | None = None) -> None:
    _ensure_dirs()
    (obdelani_file or _obdelani_dn_file()).write_text(
        json.dumps({"obdelani": dict(sorted(zapisi.items()))}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_obdelani_dn() -> set[str]:
    """Persistenten seznam ključev (`stevilka.leto`) že obdelanih DN.

    Namerno NI "zadnja številka" (x1): iskanje DN vedno preveri v datumskem
    razponu danes-1..danes, znotraj katerega številke DN niso nujno strogo
    naraščajoče/brez vrzeli glede na filter (FAKTURIRANA/SKLADISCE), zato je
    potreben eksaktni seznam že videnih DN, ne samo najvišja doslej videna
    številka. Arhiviranje (glej archive_stare_obdelane_dn) starejših zapisov
    NE vpliva na pravilnost tega seznama znotraj obdobja dejanskega delovanja
    filtra (danes-1..danes je vedno znotraj zadnjih 7 dni).
    """
    return set(_load_obdelani_dn_zapisi().keys())


def add_obdelani_dn(keys: set[str]) -> None:
    """Doda nove ključe (`stevilka.leto`) v persistentni seznam obdelanih DN, z datumom zdaj.

    Kliče se samo za DN, katerih prevzemnica je bila USPEŠNO posodobljena
    (PUT) - DN z napako pri posodobitvi ostane izven seznama, da se njegova
    prevzemnica samodejno poskusi znova ob naslednjem zagonu (PUT je
    idempotenten, v nasprotju s prejšnjim POST/create pristopom ni tveganja
    podvajanja zapisov).
    """
    if not keys:
        return
    zapisi = _load_obdelani_dn_zapisi()
    zdaj = datetime.now(timezone.utc).isoformat()
    for kljuc in keys:
        zapisi.setdefault(kljuc, zdaj)
    _write_obdelani_dn_zapisi(zapisi)


def load_obdelane_dobavnice() -> set[str]:
    """Persistenten seznam ključev (`stevilka.leto`) dobavnic, na katere je SSCC že zapisan.

    Isti format/mehanizem kot load_obdelani_dn (datoteka obdelane_dobavnice.json);
    služi temu, da se ista dobavnica ne obdela večkrat (POST SSCCDobavnica ni idempotenten).
    """
    return set(_load_obdelani_dn_zapisi(_obdelane_dobavnice_file()).keys())


def add_obdelane_dobavnice(keys: set[str]) -> None:
    """Doda ključe (`stevilka.leto`) v persistentni seznam obdelanih dobavnic, z datumom zdaj.

    Kliče se samo za dobavnice, kjer so VSE relevantne postavke zapisane
    (ali so SSCC že imele) - dobavnica z napako ostane izven seznama in se
    ob naslednjem zagonu poskusi znova.
    """
    if not keys:
        return
    datoteka = _obdelane_dobavnice_file()
    zapisi = _load_obdelani_dn_zapisi(datoteka)
    zdaj = datetime.now(timezone.utc).isoformat()
    for kljuc in keys:
        zapisi.setdefault(kljuc, zdaj)
    _write_obdelani_dn_zapisi(zapisi, datoteka)


def output_update_path(pre_stevilka: str, timestamp: str) -> Path:
    """Pot do arhivske JSON datoteke s telesom PUT zahtevka za posodobitev prevzemnice.

    Zapisuje se za vsako obdelano prevzemnico - služi kot trajen arhiv in
    sledljivost delovanja aplikacije. Datoteka ostane v data/izhodni_zahtevki/
    do naslednjega dnevnega arhiviranja (glej archive_izhodni_zahtevki), ki jo
    premakne v data/arhiv/yyyy-mm-dd/.
    """
    _ensure_dirs()
    safe_pre_stevilka = pre_stevilka.replace("/", "_").replace(".", "_")
    return _izhodni_zahtevki_dir() / f"prevzemnica_posodobitev_{safe_pre_stevilka}_{timestamp}.json"


def output_sscc_path(timestamp: str) -> Path:
    """Pot do arhivske JSON datoteke s telesi SSCC zapisov (POST FA/SSCCDobavnica) iz enega zagona."""
    _ensure_dirs()
    return _izhodni_zahtevki_dir() / f"sscc_dobavnice_{timestamp}.json"


def archive_stare_obdelane_dn(dni: int = OBDELANI_DN_ARHIV_DNI, obdelani_file: Path | None = None) -> dict[str, str]:
    """Iz obdelani_dn.json odstrani in vrne zapise, obdelane pred več kot `dni` dnevi.

    Zapisi, obdelani znotraj zadnjih `dni` dni, OSTANEJO v aktivni datoteki
    (in torej v load_obdelani_dn()). Klicatelj (glej dnevno_arhiviranje) vrnjene
    zapise shrani v arhivsko datoteko - ta funkcija jih samo izloči iz aktivne.
    """
    zapisi = _load_obdelani_dn_zapisi(obdelani_file)
    meja = datetime.now(timezone.utc) - timedelta(days=dni)
    stari: dict[str, str] = {}
    novi: dict[str, str] = {}
    for kljuc, datum_str in zapisi.items():
        datum = datetime.fromisoformat(datum_str)
        if datum.tzinfo is None:
            datum = datum.replace(tzinfo=timezone.utc)
        if datum < meja:
            stari[kljuc] = datum_str
        else:
            novi[kljuc] = datum_str
    if stari:
        _write_obdelani_dn_zapisi(novi, obdelani_file)
    return stari


def archive_izhodni_zahtevki(cilj_dir: Path) -> int:
    """Premakne vse datoteke iz data/izhodni_zahtevki/ v `cilj_dir`. Vrne število premaknjenih datotek."""
    izvor = _izhodni_zahtevki_dir()
    if not izvor.exists():
        return 0
    datoteke = [p for p in izvor.iterdir() if p.is_file()]
    if not datoteke:
        return 0
    cilj_dir.mkdir(parents=True, exist_ok=True)
    for datoteka in datoteke:
        shutil.move(str(datoteka), str(cilj_dir / datoteka.name))
    return len(datoteke)


def dnevno_arhiviranje(dni_obdelani_dn: int = OBDELANI_DN_ARHIV_DNI) -> dict[str, int]:
    """Dnevno arhivsko opravilo (klicano ob 18:00 po Europe/Ljubljana, glej app/scheduler.py):

    1. Vse datoteke iz data/izhodni_zahtevki/ premakne v data/arhiv/yyyy-mm-dd/
       (današnji datum).
    2. Iz obdelani_dn.json odstrani zapise, starejše od `dni_obdelani_dn` dni,
       in jih doda v data/arhiv/yyyy-mm-dd/obdelani_dn_arhiv.json (združi z
       morebitno obstoječo vsebino te datoteke - varno za ponovni zagon istega dne).
    """
    danes_dir = _arhiv_dir() / datetime.now().strftime("%Y-%m-%d")

    stevilo_datotek = archive_izhodni_zahtevki(danes_dir)

    stari_obdelani = archive_stare_obdelane_dn(dni_obdelani_dn)
    if stari_obdelani:
        danes_dir.mkdir(parents=True, exist_ok=True)
        arhiv_pot = danes_dir / "obdelani_dn_arhiv.json"
        obstojeci: dict[str, str] = {}
        if arhiv_pot.exists():
            obstojeci = json.loads(arhiv_pot.read_text(encoding="utf-8")).get("obdelani", {})
        obstojeci.update(stari_obdelani)
        arhiv_pot.write_text(
            json.dumps({"obdelani": dict(sorted(obstojeci.items()))}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # Obdelane dobavnice (SSCC) - isti 7-dnevni rok in isti arhivski direktorij.
    stare_dobavnice = archive_stare_obdelane_dn(dni_obdelani_dn, _obdelane_dobavnice_file())
    if stare_dobavnice:
        danes_dir.mkdir(parents=True, exist_ok=True)
        arhiv_pot = danes_dir / "obdelane_dobavnice_arhiv.json"
        obstojeci = {}
        if arhiv_pot.exists():
            obstojeci = json.loads(arhiv_pot.read_text(encoding="utf-8")).get("obdelani", {})
        obstojeci.update(stare_dobavnice)
        arhiv_pot.write_text(
            json.dumps({"obdelani": dict(sorted(obstojeci.items()))}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "izhodni_zahtevki": stevilo_datotek,
        "obdelani_dn": len(stari_obdelani),
        "obdelane_dobavnice": len(stare_dobavnice),
    }
