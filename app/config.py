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


settings = Settings()
