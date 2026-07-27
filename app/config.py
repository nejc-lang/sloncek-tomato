from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    API_USER: str = ""
    API_PASS: str = ""
    API_TAX_NUMBER: str = ""
    EVASCO_BASE_URL: str = ""

    # Skupni skrivni ključ za avtentikacijo GUI klienta proti temu backendu
    # (glej gui/.env: API_KEY) - posreduje se v headerju `X-API-Key`. Backend
    # brez nastavljenega ključa zavrne vse klice na /api/* (glej app/main.py).
    API_KEY: str = ""

    # Direktorij za persistenco (obdelani_dn.json, seje, arhiv izhodnih zahtevkov).
    # V Dockerju naj bo mountan kot volume, da se ohrani med restarti.
    DATA_DIR: str = "data"

    # Dolžina številke DN/prevzemnice z vodilnimi ničlami (StevilkaOd/Do).
    STEVILKA_PADDING: int = 5

    # TESTNO OBDOBJE (glej README.md): PUT /api/v1/FA/prevzemnica še ni
    # objavljen v eVasco swaggerju (stanje 2026-07-27), zato se dejanski PUT
    # klic (EVascoClient.update_prevzemnica) v app/process.py::run PRESKOČI,
    # dokler je ta zastavica True - sistem kljub temu pridobi vse podatke,
    # izračuna telo posodobitve in ga arhivira v
    # data/izhodni_zahtevki/prevzemnica_posodobitev_*.json, da je mogoče
    # natančno preveriti, kaj bi PUT poslal. Ko eVasco objavi PUT in je
    # EVascoClient.update_prevzemnica preverjen/popravljen, nastavi na False
    # v .env - brez posega v kodo. LOT/DATUM_UPORABNOSTI (FASifranti/lotPodatki)
    # so tem neodvisen, že delujoč endpoint in se pišejo ne glede na to zastavico.
    PUT_PREVZEMNICA_DRY_RUN: bool = True


settings = Settings()
