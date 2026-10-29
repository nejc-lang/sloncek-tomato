import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app import storage

# Lokalni (SI) čas dnevnega arhiviranja - glej storage.dnevno_arhiviranje.
# Eksplicitna cona namesto zanašanja na TZ okolja kontejnerja (ki teče v UTC).
ARHIV_CONA = ZoneInfo("Europe/Ljubljana")
ARHIV_URA = 18


def _sekunde_do_naslednjega_arhiviranja() -> float:
    zdaj = datetime.now(ARHIV_CONA)
    naslednji = zdaj.replace(hour=ARHIV_URA, minute=0, second=0, microsecond=0)
    if naslednji <= zdaj:
        naslednji += timedelta(days=1)
    return (naslednji - zdaj).total_seconds()


async def dnevno_arhiviranje_loop() -> None:
    """Ozadenjska naloga (glej lifespan v app/main.py) - vsak dan ob 18:00 (Europe/Ljubljana)
    pokliče storage.dnevno_arhiviranje(). Izpisuje na stdout (glej `docker compose logs`) -
    projekt namerno ne uvaja ločenega logging okvira, glej ostale module.
    """
    while True:
        await asyncio.sleep(_sekunde_do_naslednjega_arhiviranja())
        try:
            rezultat = storage.dnevno_arhiviranje()
            print(f"[{datetime.now(ARHIV_CONA).isoformat()}] Dnevno arhiviranje opravljeno: {rezultat}")
        except Exception as exc:  # noqa: BLE001 - ne sme prekiniti ozadenjske zanke
            print(f"[{datetime.now(ARHIV_CONA).isoformat()}] Dnevno arhiviranje NI uspelo: {exc}")
