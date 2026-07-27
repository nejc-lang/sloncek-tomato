import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.config import settings

TOKEN_LIFETIME_MINUTES = 30
# Refresh a bit early to avoid edge-case expiry mid-request.
TOKEN_REFRESH_MARGIN = timedelta(minutes=1)


class EVascoClient:
    """Client for the eVasco API.

    NOTE: EVASCO_BASE_URL is not defined in the swagger spec (no server URL
    was provided). It MUST be set via the EVASCO_BASE_URL env variable before
    this client can be used. See README.md.
    """

    def __init__(self) -> None:
        if not settings.EVASCO_BASE_URL:
            raise RuntimeError(
                "EVASCO_BASE_URL is not configured. Set it in .env before using EVascoClient."
            )
        self._base_url = settings.EVASCO_BASE_URL.rstrip("/")
        self._client = httpx.Client(base_url=self._base_url, timeout=30.0)
        self._api_key: str | None = None
        self._expiration: datetime | None = None

    def _is_token_valid(self) -> bool:
        if not self._api_key or not self._expiration:
            return False
        return datetime.now(timezone.utc) < (self._expiration - TOKEN_REFRESH_MARGIN)

    def authenticate(self) -> str:
        """Log in to eVasco and cache the API key in memory.

        Called automatically by _ensure_authenticated() when the cached key
        is missing or about to expire (tokens are valid for 30 minutes).
        """
        response = self._client.post(
            "/api/v1/Avtentikacija",
            json={
                "username": settings.API_USER,
                "password": settings.API_PASS,
                "taxNumber": settings.API_TAX_NUMBER,
            },
        )
        response.raise_for_status()
        data = response.json()

        self._api_key = data["apiKey"]

        expiration_raw = data.get("expiration")
        if expiration_raw:
            self._expiration = datetime.fromisoformat(expiration_raw)
        else:
            self._expiration = datetime.now(timezone.utc) + timedelta(minutes=TOKEN_LIFETIME_MINUTES)

        return self._api_key

    def _ensure_authenticated(self) -> str:
        if not self._is_token_valid():
            self.authenticate()
        return self._api_key

    def _auth_headers(self) -> dict[str, str]:
        api_key = self._ensure_authenticated()
        return {"Authorization": f"Bearer {api_key}"}

    def _raise_with_body(self, response: httpx.Response) -> None:
        """response.raise_for_status() samo pove status kodo, ne pa TELESA napake -
        eVasco vrača konkretno validacijsko sporočilo (npr. katera šifra/sifrant
        ni veljaven) v response body, ki bi ga sicer izgubili (glej napako pri
        POST /api/v1/FA/prevzemnica 2026-07-24 - "komercialist": "11" namesto
        "011" je vrnilo gol 400 Bad Request, brez tega bi bilo iskanje vzroka
        veliko počasnejše).
        """
        if response.is_error:
            raise httpx.HTTPStatusError(
                f"{response.status_code} {response.reason_phrase} za {response.request.url}: {response.text}",
                request=response.request,
                response=response,
            )

    def get(self, path: str, params: dict | None = None) -> httpx.Response:
        response = self._client.get(path, params=params, headers=self._auth_headers())
        self._raise_with_body(response)
        return response

    def post(self, path: str, json: dict | None = None) -> httpx.Response:
        response = self._client.post(path, json=json, headers=self._auth_headers())
        self._raise_with_body(response)
        return response

    def put(self, path: str, json: dict | None = None, params: dict | None = None) -> httpx.Response:
        response = self._client.put(path, params=params, json=json, headers=self._auth_headers())
        self._raise_with_body(response)
        return response

    def get_dn_nalogi(self, *, datum_od: str, datum_do: str, stevilka_od: str, stevilka_do: str) -> list[dict]:
        """GET /api/v1/FA/dn/nalog filtered on FAKTURIRANA=7 and SKLADISCE=11.

        DodatnoPoljeFilter must be passed as a list of individual JSON
        strings (repeated query param), not a single JSON array string.
        """
        params = {
            "DatumOd": datum_od,
            "DatumDo": datum_do,
            "StevilkaOd": stevilka_od,
            "StevilkaDo": stevilka_do,
            "VrniPostavke": 1,
            "DodatnaPolja": "FAKTURIRANA,SKLADISCE,PRE_STEVILKA",
            "DodatnoPoljeFilter": [
                json.dumps({"Polje": "FAKTURIRANA", "Simbol": "=", "Vrednost": 7}),
                json.dumps({"Polje": "SKLADISCE", "Simbol": "=", "Vrednost": 11}),
            ],
        }
        return self.get("/api/v1/FA/dn/nalog", params=params).json()

    def get_prevzemnica_by_stevilka(self, stevilka_leto: str) -> list[dict]:
        """GET /api/v1/FA/prevzemnica for a single stevilka.leto value."""
        params = {
            "StevilkaOd": stevilka_leto,
            "StevilkaDo": stevilka_leto,
            "VrniPostavke": 1,
            "VrniOznakoPriloge": 1,
        }
        return self.get("/api/v1/FA/prevzemnica", params=params).json()

    def get_artikli(self, sifre: list[str], dodatna_polja: list[str] | None = None) -> list[dict]:
        """GET /api/v1/FASifranti/artikel - šifrant artiklov.

        `stopnjaDDV` je native polje (brez dodatna_polja). Polja kot
        PROSTO_R5 / NABAVNA_CENA niso native - zahtevajo DodatnaPolja
        parameter, sicer jih odgovor ne vsebuje.
        Max 100 šifer na klic (omejitev eVasco API), zato batchamo po 100.
        """
        if not sifre:
            return []
        results: list[dict] = []
        for i in range(0, len(sifre), 100):
            chunk = sifre[i : i + 100]
            params: dict[str, Any] = {"Sifra": ",".join(chunk)}
            if dodatna_polja:
                params["DodatnaPolja"] = ",".join(dodatna_polja)
            results.extend(self.get("/api/v1/FASifranti/artikel", params=params).json())
        return results

    def get_artikel_normativ(self, sifre: list[str]) -> list[dict]:
        """GET /api/v1/FASifranti/artikel/normativ - sestavine (SIFRA_MAT) za šifre postavk (izdelkov).

        NAMERNO uporablja `Normativ=` in ne `Artikel=` kot query parameter:
        `Artikel=<sifra>` vrne obratno smer (kje je `<sifra>` uporabljena kot
        sestavina v receptu NEKEGA DRUGEGA artikla), medtem ko `Normativ=<sifra>`
        vrne dejanski recept ZA `<sifra>` (živi podatki, 2026-07-24, artikel
        04949: `Artikel=04949` je vrnil bogus vrstico normativ=60598, medtem ko
        `Normativ=04949` je vrnil pravih 11 sestavin, potrjenih s strani
        uporabnika).

        Ko se poizveduje z `Normativ=`, ima vsak element `normativ` (= šifra
        postavke, ki smo jo poizvedovali) in `artikel` (šifra sestavine),
        `kolicina`, `procentKala` - torej OBRATNO od poimenovanja polj, ki bi
        ga pričakovali glede na ime parametra. Max 100 šifer na klic.
        """
        if not sifre:
            return []
        results: list[dict] = []
        for i in range(0, len(sifre), 100):
            chunk = sifre[i : i + 100]
            params = {"Normativ": ",".join(chunk)}
            results.extend(self.get("/api/v1/FASifranti/artikel/normativ", params=params).json())
        return results

    def get_artikel_normativ_operacije(self, sifre: list[str]) -> list[dict]:
        """GET /api/v1/FASifranti/artikel/normativOperacije - operacije (SIFRA_OPER) za šifre postavk.

        Glej opombo v `get_artikel_normativ` - uporablja `Normativ=`, ne
        `Artikel=`, iz istega razloga (obratna smer podatkov). Vsak element
        ima `normativ` (šifra postavke), `artikel` (šifra operacije/vira) in
        `redneUre`/`pripravneUre` (= FA_NOR_OPER.REDNE_URE/PRIPRAVNE_URE).
        Prazen seznam je pričakovan/veljaven rezultat, če artikel nima
        operacij (glej cena_op v BRIEF.md).
        """
        if not sifre:
            return []
        results: list[dict] = []
        for i in range(0, len(sifre), 100):
            chunk = sifre[i : i + 100]
            params = {"Normativ": ",".join(chunk)}
            results.extend(self.get("/api/v1/FASifranti/artikel/normativOperacije", params=params).json())
        return results

    def get_lot_podatki(self, sifra: str, lot: str) -> list[dict]:
        """GET /api/v1/FASifranti/lotPodatki - preveri, ali kombinacija šifra+lot že obstaja."""
        return self.get("/api/v1/FASifranti/lotPodatki", params={"Sifra": sifra, "Lot": lot}).json()

    def create_lot_podatki(self, body: dict) -> dict:
        """POST /api/v1/FASifranti/lotPodatki - vpiše nov šifra+lot zapis (DATUM_UPORABNOSTI).

        Vrne 409, če kombinacija šifra+lot že obstaja - v tem primeru je
        treba uporabiti update_lot_podatki (PUT) namesto tega klica.
        """
        return self.post("/api/v1/FASifranti/lotPodatki", json=body).json()

    def update_lot_podatki(self, body: dict) -> None:
        """PUT /api/v1/FASifranti/lotPodatki - ažurira obstoječ šifra+lot zapis."""
        self.put("/api/v1/FASifranti/lotPodatki", json=body)

    def update_prevzemnica(self, body: dict) -> dict | None:
        """PUT /api/v1/FA/prevzemnica - posodobi GLAVO obstoječe prevzemnice (`PrevzemnicaPutRequest`).

        Objavljeno v swaggerju 2026-07-27. Ciljna prevzemnica se identificira
        preko `stevilka`/`leto` V TELESU (ne query parametrov kot pri GET-u).
        Shema ima `additionalProperties: false` in NE vsebuje `postavke` -
        za spremembo postavk (LOT/CENA_VNESENA/...) glej
        `update_prevzemnica_postavke`. Telo naj vsebuje samo polja, ki se
        dejansko spreminjajo - PUT tu pomeni delno dopolnjevanje obstoječega
        zapisa, ne zamenjavo celotne prevzemnice.
        """
        response = self.put("/api/v1/FA/prevzemnica", json=body)
        if not response.content:
            return None
        return response.json()

    def update_prevzemnica_postavke(self, body: dict) -> dict | None:
        """PUT /api/v1/FA/prevzemnica/spremembaPostavk - posodobi POSTAVKE obstoječe prevzemnice.

        Objavljeno v swaggerju 2026-07-27 (`PrevzemnicaPutVecPostavkBaseRequest`,
        ločen endpoint od glave). Vsaka postavka v `body["postavke"]` MORA
        vsebovati `sifra` IN `zs` (zaporedna številka postavke - native polje
        `zs` na GET odgovoru postavke, glej get_prevzemnica_by_stevilka) -
        `sifra` sama ni dovolj za identifikacijo. Vrne 201 Created z
        `{leto, stevilka}` ob uspehu.
        """
        response = self.put("/api/v1/FA/prevzemnica/spremembaPostavk", json=body)
        if not response.content:
            return None
        return response.json()

    def close(self) -> None:
        self._client.close()
