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

    # TESTNO OBDOBJE: spodnja meja (vključno) številke delovnega naloga, ki
    # se obdeluje - DN z nižjo `stevilka` se client-side izločijo (glej
    # app/process.py::run), da se med ročnim testiranjem sistem ne dotika
    # DN/prevzemnic, obdelanih pred uvedbo te verzije. `1` = brez omejitve
    # (privzeto). Primerjava je numerična, ne string.
    DN_STEVILKA_OD: int = 1

    # TESTNO OBDOBJE (glej README.md): PUT /api/v1/FA/prevzemnica in
    # PUT /api/v1/FA/prevzemnica/spremembaPostavk sta od 2026-07-27 objavljena
    # v eVasco swaggerju (glej swagger.json) in EVascoClient.update_prevzemnica
    # / update_prevzemnica_postavke sta implementirana po tej dokumentirani
    # shemi - kljub temu dejanska PUT klica v app/process.py::run še vedno
    # PRESKOČIMO, dokler je ta zastavica True (privzeto), da se implementacija
    # najprej preveri na dry-run izpisu (data/izhodni_zahtevki/*.json), preden
    # gre v produkcijo. Sistem kljub dry-runu pridobi vse podatke, izračuna
    # obe telesi posodobitve in ju arhivira. Ko je izpis ročno preverjen in
    # potrjen, nastavi na False v .env - brez posega v kodo.
    # LOT/DATUM_UPORABNOSTI (FASifranti/lotPodatki) so od tega neodvisen, že
    # delujoč endpoint in se pišejo ne glede na to zastavico.
    PUT_PREVZEMNICA_DRY_RUN: bool = True

    # SSCC zapis na dobavnice (POST /api/v1/FA/SSCCDobavnica, glej
    # app/process.py::_obdelaj_sscc): samo dobavnice kupca SSCC_PARTNER in
    # samo postavke iz skladišča SSCC_SKLADISCE (potrjeno s strani
    # uporabnika 2026-10-29). SSCC_KODA je konstanta za vse postavke.
    SSCC_PARTNER: int = 3202
    SSCC_SKLADISCE: int = 11
    SSCC_KODA: str = "938300396399999999"

    # Dokler je True, se POST SSCCDobavnica preskoči - telesa se samo
    # izračunajo in arhivirajo (data/izhodni_zahtevki/sscc_dobavnice_*.json).
    # Nastavi na False v .env po ročni potrditvi izpisa.
    SSCC_DOBAVNICA_DRY_RUN: bool = True


settings = Settings()
