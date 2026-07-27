# Brief za Claude Code — DN-PREVZEMNICA-Tomato: obdelava delovnih nalogov in prevzemnic

Projekt `DN-PREVZEMNICA-Tomato` (skelet že postavljen: FastAPI backend na portu 8090 + eVasco API klient) implementira poslovno logiko + Qt6 GUI z enim gumbom ("Obdelaj").

## Namen sistema

**En korak** (od 2026-07-27, prej dvokoračen proces brisanje+kreiranje — glej git zgodovino): poišče vse delovne naloge (DN) današnjega dne, ki še niso bili obdelani, s pogoji `FAKTURIRANA = 7` in `SKLADISCE = 11`. Za vsak DN pridobi vrednost dodatnega polja `PRE_STEVILKA` in z njo poišče povezano prevzemnico (glava + postavke). Nato za vsako najdeno prevzemnico z enim **API PUT klicem** dopolni obstoječe vrednosti neposredno na tej isti prevzemnici — brez brisanja in ponovnega ustvarjanja. Dopolnjujejo se: `datum`, `ins_drzava`, `ins_drzava_porekla`, `komercialist`, `skladisce`, `skladisce_za_odpis`, `ins_vrsta_transporta`, `lot`, `datum_uporabnosti`, `cena_vnesena` (glej izračune spodaj).

**⚠️ PUT endpoint za `/api/v1/FA/prevzemnica` še ni objavljen v swaggerju** (stanje 2026-07-27; do zdaj sta bila na voljo samo `GET`/`POST`). Ko bo eVasco stran objavila natančno specifikacijo, je treba preveriti/popraviti `EVascoClient.update_prevzemnica` (pot, način identifikacije ciljne prevzemnice, natančna oblika telesa) — trenutna implementacija je najboljša ocena glede na obstoječ vzorec (`StevilkaOd`/`StevilkaDo` query parametra kot pri GET-u, telo v obliki `PrevzemnicaPostBaseRequest` s samo spremenjenimi polji).

## Sledenje že obdelanim DN

- Vsak zagon vedno išče DN v datumskem razponu **`DatumOd = danes - 1`, `DatumDo = danes`** (pokrije tudi primer, ko se program ne zažene točno vsak dan).
- Sistem persistentno hrani **seznam že obdelanih DN** (`stevilka.leto`, `data/obdelani_dn.json`). To ni "zadnja številka" (x1), ker datumski razpon pokriva 2 dni in številke DN znotraj njega niso nujno strogo naraščajoče/brez vrzeli glede na filter.
- Po pridobitvi DN iz API-ja (glej filter spodaj) sistem **client-side izloči** tiste, ki so že v seznamu obdelanih, preden nadaljuje z iskanjem prevzemnic.
- DN se v seznam obdelanih doda **šele ob uspešni PUT posodobitvi njegove prevzemnice** — DN z napako (manjka `PRE_STEVILKA`, prevzemnica ni najdena, napaka pri PUT-u ...) ostane zunaj seznama in se **samodejno poskusi znova** ob naslednjem zagonu. To je varno, ker je PUT idempotenten (v nasprotju s prejšnjim POST/create pristopom ni tveganja podvajanja zapisov ob ponovnem poskusu).

## Arhiv obdelanih zahtevkov

- Za vsako uspešno posodobljeno prevzemnico se telo PUT klica (+ lotPodatki načrt) arhivira v `data/izhodni_zahtevki/prevzemnica_posodobitev_<pre_stevilka>_<timestamp>.json` — služi kot trajen arhiv in sledljivost.
- Ker je vse en klic backenda (`POST /api/obdelaj`), ni več koncepta "seje, ki čaka na korak 2" — vsak zagon je samostojen in dokončan (uspeh/napaka je znan takoj, na ravni posamezne prevzemnice, ne "vse ali nič" za cel batch).

## eVasco API klici (glej `evasco_client.py`, dopolni)

**Avtentikacija**: `POST /api/v1/Avtentikacija` (username/password/taxNumber iz `.env`) → `apiKey`, veljaven 30 min, cache + auto-refresh (že implementirano/predvideno v skeletu).

**Iskanje DN**: `GET /api/v1/FA/dn/nalog`
- Parametri: `DatumOd = danes-1`, `DatumDo = danes` (oba `T00:00:00`), `StevilkaOd`/`StevilkaDo` obvezna kljub datumskemu filtru — uporabi širok razpon za obe leti, ki lahko nastopita v razponu (`00001.{lansko_leto}` do `99999.{letosnje_leto}`, ali smiselno prilagojeno), `VrniPostavke=1`, `DodatnaPolja=FAKTURIRANA,SKLADISCE,PRE_STEVILKA`, `DodatnoPoljeFilter` (**array** stringov, ne en JSON string!):
  ```python
  params["DodatnoPoljeFilter"] = [
      json.dumps({"Polje": "FAKTURIRANA", "Simbol": "=", "Vrednost": 7}),
      json.dumps({"Polje": "SKLADISCE", "Simbol": "=", "Vrednost": 11}),
  ]
  ```
- Za vsak DN v odgovoru poišči `PRE_STEVILKA` v `dodatnaPolja[]`.
- Po prejemu odgovora izloči DN, ki so že v perzistentnem seznamu obdelanih (`stevilka.leto`).

**Poizvedba prevzemnice**: `GET /api/v1/FA/prevzemnica`
- `StevilkaOd`/`StevilkaDo` = `f"{pre_stevilka}.{leto}"` (če `PRE_STEVILKA` že vsebuje piko z letom, uporabi kot je; sicer dodaj isto leto kot DN)
- `VrniPostavke=1`, `VrniOznakoPriloge=1`

**Dodatni podatki na vsaki postavki (LOT, DATUM_UPORABNOSTI, CENA_VNESENA)**

Za vsako postavko obstoječe prevzemnice je treba izračunati in zapisati tri dodatne vrednosti. LOT in CENA_VNESENA gresta preko `postavke[].dodatnaPolja[]` v PUT telesu (glej telo spodaj) — **razen `DATUM_UPORABNOSTI`, ki ni polje na postavki prevzemnice, ampak gre v ločen šifrant lotov** (glej spodaj).

1. **LOT** — format `ddmmyy`, izračunan iz datuma **obstoječe prevzemnice** (potrjeno). Npr. za 12.3.2026 → `"120326"`.
   ```python
   lot = prevzemnica_original["datum"].strftime("%d%m%y")
   ```

2. **DATUM_UPORABNOSTI** — `datum originalne prevzemnice + FA_ARTIKEL.PROSTO_R5 (dni)`, za šifro artikla **te postavke** (ne sestavin):
   ```python
   artikel = get_artikel(sifra=postavka["sifra"], dodatna_polja=["PROSTO_R5"])
   prosto_r5_dni = int(float(artikel["dodatnaPolja"][0]["vrednost"]))  # dodatnaPolja[naziv=="PROSTO_R5"]
   datum_uporabnosti = prevzemnica_original["datum"] + timedelta(days=prosto_r5_dni)
   ```
   **Kam se zapiše**: `DATUM_UPORABNOSTI` **ni** stolpec tabele `FA_PREVZEMNICA_KNJIZBA` (ni ga v seznamu iz `/prevzemnica/postavka/polja`) — gre v ločen centralni šifrant lotov: `POST`/`PUT /api/v1/FASifranti/lotPodatki`, telo `{"sifra": <sifra artikla>, "lot": <LOT iz točke 1>, "datumUporabnosti": <izračunan datum>}`.
   - Pred vpisom **najprej preveri z GET-om** (`GET /api/v1/FASifranti/lotPodatki?Sifra=<sifra>&Lot=<lot>`), ali kombinacija šifra+lot že obstaja (POST vrne `409`, če že obstaja) — če obstaja, uporabi `PUT` (ažuriranje) namesto `POST`, ali preskoči, če je vrednost že enaka.

3. **CENA_VNESENA** — vrednost gre v `postavke[].dodatnaPolja` kot `{"naziv": "CENA_VNESENA", "vrednost": cena_izdelka}` (ni nativno polje v `PrevzemnicaPostavkePutPostBaseRequest`). Izračun: `cena_izdelka = cena_mat + cena_op`, nato zaokroženo na **največ 5 številskih mest (significant digits, ne decimalnih mest)** pred pošiljanjem preko API klica — npr. `2.043656` → `2.0437`, `123.456789` → `123.46` (potrjeno s strani uporabnika 2026-07-24, implementirano v `_round_cena` v `app/process.py`).

   **Kontrola prodajne cene** (potrjeno s strani uporabnika 2026-07-24, smer primerjave popravljena isti dan): po izračunu se `CENA_VNESENA` primerja z najnižjo od dveh dodatnih polj `PRODAJNA_CENA3`/`PRODAJNA_CENA4` (`GET /api/v1/FASifranti/artikel?Sifra=<sifra_izdelka>&DodatnaPolja=PRODAJNA_CENA3,PRODAJNA_CENA4`, glej `_fetch_prodajne_cene_izdelka_lookup`). Če je `CENA_VNESENA` **višja** od te najnižje prodajne cene (izdelek bi se prodajal z izgubo), se doda opozorilo (prefiks `CENA_OPOZORILO:` v `warnings`) - GUI ga izpiše z **rdečim tekstom** (glej `gui/main.py::append_log`). `0`/manjkajoča vrednost na `PRODAJNA_CENA3`/`4` se šteje za "cena ni nastavljena" (isti dogovor kot pri `NABAVNA_CENA`) in se izloči iz primerjave; če nobena od obeh ni nastavljena, se primerjava izpusti. (Prvotna verzija je pomotoma opozarjala v obratnem primeru - ko je bila cena_vnesena NIŽJA od prodajne cene, kar je pravzaprav normalno/dobro stanje.)

   **cena_mat** — sestavine artikla (t. i. "SIFRA_MAT") se pridobijo preko `GET /api/v1/FASifranti/artikel/normativ?Normativ=<sifra_postavke>` (⚠️ **NE** `Artikel=` — ta parameter vrača obratno smer: kje je `<sifra_postavke>` uporabljena kot sestavina v receptu NEKEGA DRUGEGA artikla, kar je bil bug, odkrit in popravljen 2026-07-24 na živih podatkih artikla 04949) → vrne seznam `{"normativ": <sifra_postavke>, "artikel": <sifra_sestavine>, "kolicina": ..., "procentKala": ...}` (polji `normativ`/`artikel` sta pri poizvedbi z `Normativ=` obrnjeni glede na to, kar bi pričakovali od imena parametra). Cena posamezne sestavine (`NABAVNA_CENA`) se pridobi preko `GET /api/v1/FASifranti/artikel?Sifra=<sifra_sestavine>&DodatnaPolja=NABAVNA_CENA` (**ni** nativno polje `ArtikelResponse`, samo dodatno polje; `prodajnaCena` je za te artikle vedno `0.0` in ni uporabno).
   ```python
   cena_mat = sum(
       nabavna_cena[sestavina["artikel"]] * sestavina["kolicina"] * (1 + sestavina["procentKala"] / 100)
       for sestavina in normativ_sestavin
   )
   ```
   **Potrjeno**: `procentKala` je pravi odstotek (npr. `12.0` = 12%) in se **deli s 100** — glej živi primer P03350 (procentKala=12.0 na sestavini 60519); brez deljenja bi to pomenilo 13x nabavno ceno namesto realnega ~12% kala.

   **cena_op** — operacije artikla (t. i. "SIFRA_OPER") preko `GET /api/v1/FASifranti/artikel/normativOperacije?Normativ=<sifra_postavke>` (enak `Normativ=`/`Artikel=` obrat kot zgoraj) → polja `artikel` (šifra same operacije, npr. `P50000`), `redneUre` (= `FA_NOR_OPER.REDNE_URE`). Na artiklih brez lastnih operacij ta klic vrne prazen seznam - `cena_op = 0` je v tem primeru pravilno.

   **Popravek 2026-07-24 (2)**: `pripravneUre` se **ne** upošteva v `cena_op` (potrjeno s strani uporabnika) - samo `redneUre`. Če je `redneUre = 0` za neko operacijo, se doda opozorilo v `warnings` (GUI ga prikaže) - informativno, ne pomeni napake v izračunu.

   **Urna postavka** (potrjeno s strani uporabnika 2026-07-24): `urna_postavka` = `FA_ARTIKEL.PRODAJNA_CENA` **šifre same operacije**, ne fiksna konfiguracijska vrednost. Native polje `prodajnaCena` na `GET /api/v1/FASifranti/artikel?Sifra=<sifra_operacije>` (brez potrebe po `DodatnaPolja`). Primer: operacija `P50000` ("P strošek za francosko solato") ima `prodajnaCena=0.2` → za izdelek 04949 (`redneUre=1.8`) je `cena_op = 1.8 * 0.2 = 0.36`.
   ```python
   cena_op = sum(
       operacija["redneUre"] * urna_postavka[operacija["artikel"]]
       for operacija in normativ_operacij_izdelka
   )
   ```


PUT telo namenoma vsebuje SAMO polja, ki se dejansko spreminjajo — `dobavitelj`/`rabat1` in `kolicina`/`nabavnaCena`/`stopnjaDdv` na postavkah ostanejo nedotaknjeni (obstoječa prevzemnica se ne prepiše v celoti, samo dopolni). `DOKUMENT_DOBAVNICA` ni več potreben (sledljivost do izvirnega DN ni potrebna — prevzemnica ostane ista, ne nastane nova). Sestavi takole (glej `app/process.py::_prepare_prevzemnica_update`):

```python
body = {
    "datum": prevzemnica_original["datum"],
    "komercialist": "011",                          # string, max 5 znakov
    "skladisce": 11,
    "dodatnaPolja": [
        {"naziv": "INS_DRZAVA", "vrednost": 705},
        {"naziv": "INS_DRZAVA_POREKLA", "vrednost": 705},
        {"naziv": "SKLADISCE_ZA_ODPIS", "vrednost": 11},
        {"naziv": "INS_VRSTA_TRANSPORTA", "vrednost": 3},
    ],
    "postavke": [
        {
            "sifra": p["sifra"],
            "dodatnaPolja": [
                {"naziv": "LOT", "vrednost": lot},
                {"naziv": "CENA_VNESENA", "vrednost": cena_izdelka},  # glej izračun zgoraj; samo za izdelke
            ],
        }
        for p in prevzemnica_original["postavke"]
    ],
}
```

`DATUM_UPORABNOSTI` gre ločeno, preko `FASifranti/lotPodatki` (ni del zgornjega telesa) — glej razdelek zgoraj.

- **Potrjeno**: `stopnjaDdv` se pri PUT-u ne pošilja — ostaja nespremenjen.
- PUT klic identificira ciljno prevzemnico (glej `EVascoClient.update_prevzemnica` — trenutno `StevilkaOd`/`StevilkaDo` query parametra, enako kot pri GET-u; **preveriti, ko bo PUT objavljen v swaggerju**).

## Qt6 GUI

- Gumb **"Obdelaj"**: pošlje `POST /api/obdelaj`, backend izvede zgornjo logiko (najde DN, poišče prevzemnice, dopolni jih s PUT-om), GUI prikaže povzetek (katere prevzemnice so bile uspešno posodobljene, morebitne napake in cenovna opozorila).
- Gumb naj bo med izvajanjem onemogočen (loading indikator), da se prepreči podvojeno sprožanje.
