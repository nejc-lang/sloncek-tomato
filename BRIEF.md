# Brief za Claude Code — Sloncek-Tomato: obdelava delovnih nalogov in prevzemnic

Projekt `Sloncek-Tomato` (skelet že postavljen: FastAPI backend na portu 8090 + eVasco API klient) implementira poslovno logiko + Qt6 GUI z enim gumbom ("Obdelaj").

## Namen sistema

**En korak** (od 2026-07-27, prej dvokoračen proces brisanje+kreiranje — glej git zgodovino): poišče vse delovne naloge (DN) današnjega dne, ki še niso bili obdelani, s pogoji `FAKTURIRANA = 7` in `SKLADISCE = 11`. Za vsak DN pridobi vrednost dodatnega polja `PRE_STEVILKA` in z njo poišče povezano prevzemnico (glava + postavke). Nato za vsako najdeno prevzemnico z **dvema API PUT klicema** dopolni obstoječe vrednosti neposredno na tej isti prevzemnici — brez brisanja in ponovnega ustvarjanja:
- `PUT /api/v1/FA/prevzemnica` (glava): `datum`, `ins_drzava`, `komercialist`, `skladisce`, `skladisce_za_odpis`, `ins_vrsta_transporta`.
- `PUT /api/v1/FA/prevzemnica/spremembaPostavk` (postavke): `lot`, `ins_drzava_porekla`, `cena_vnesena` (glej izračune spodaj).

`datum_uporabnosti` gre ločeno preko `FASifranti/lotPodatki` (glej spodaj).

**Oba PUT endpointa sta objavljena v swaggerju od 2026-07-27** (glej `swagger.json`, shemi `PrevzemnicaPutRequest` in `PrevzemnicaPutVecPostavkBaseRequest`/`PrevzemnicaPostavkaPutRequest`). Ključne ugotovitve glede na prejšnjo (nedokumentirano) oceno:
- To sta **dva ločena endpointa**, ne en klic z gnezdenimi `postavke` — `PrevzemnicaPutRequest` (glava) ima `additionalProperties: false` in NIMA `postavke` polja.
- Ciljna prevzemnica se identificira preko `stevilka`/`leto` **v telesu**, ne preko `StevilkaOd`/`StevilkaDo` query parametrov kot pri GET-u.
- Vsaka postavka v `spremembaPostavk` MORA vsebovati `zs` (zaporedna številka postavke, native polje na GET odgovoru postavke) poleg `sifra`.
- `INS_DRZAVA_POREKLA` je **postavka-nivojsko** polje (tabela `FA_PREVZEMNICA_KNJIZBA`, potrjeno preko `GET /api/v1/FA/prevzemnica/postavka/polja`), ne glava-nivojsko kot je predvidevala prejšnja ocena.
- `EVascoClient.update_prevzemnica` (glava) in `EVascoClient.update_prevzemnica_postavke` (postavke) sta implementirana po tej dokumentirani shemi; `PUT_PREVZEMNICA_DRY_RUN` (glej `app/config.py`) ostaja `True`, dokler dry-run izpis ni ročno preverjen na živih podatkih.

## Sledenje že obdelanim DN

- **TESTNO OBDOBJE**: `settings.DN_STEVILKA_OD` (`.env`, privzeto `1` = brez omejitve) je spodnja meja (vključno) `stevilka` delovnega naloga - DN z nižjo numerično vrednostjo se client-side izločijo takoj po pridobitvi iz API-ja (`app/process.py::run`), preden gre sistem naprej v iskanje prevzemnic. Namen: med ročnim testiranjem preprečiti poseg v DN/prevzemnice, ki obstajajo od prej. Primerjava je numerična (`int(dn["stevilka"])`, API vrača ne-zero-padded string npr. `"52870"`), ne string.
- Vsak zagon išče DN v datumskem razponu **`DatumOd = danes - 1`, `DatumDo = danes`** (pokrije tudi primer, ko se program ne zažene točno vsak dan), **RAZEN v ponedeljek** (potrjeno s strani uporabnika 2026-08-10): takrat `DatumOd` ostane na **danes** (ne pade nazaj na petek) - v ponedeljek se pregledujejo samo delovni nalogi od današnjega dne, ne za nazaj čez cel vikend. To je ločen pogoj od petkovega +2 dni popravka na LOT/DATUM_UPORABNOSTI/DATUM (glej spodaj), ki se nanaša na obdelavo najdenih prevzemnic, ne na iskanje DN.
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

Za vsako postavko obstoječe prevzemnice je treba izračunati in zapisati tri dodatne vrednosti. LOT, INS_DRZAVA_POREKLA in CENA_VNESENA gresta preko `postavke[].dodatnaPolja[]` v telesu `PUT /api/v1/FA/prevzemnica/spremembaPostavk` (glej telo spodaj) — **razen `DATUM_UPORABNOSTI`, ki ni polje na postavki prevzemnice, ampak gre v ločen šifrant lotov** (glej spodaj).

1. **LOT** — format `ddmmyy`, izračunan iz datuma **obstoječe prevzemnice** (potrjeno), RAZEN v petek (glej spodaj). Npr. za 12.3.2026 → `"120326"`.
   ```python
   lot_osnova = prevzemnica_original["datum"]
   lot = lot_osnova.strftime("%d%m%y")
   ```
   **Petkov pogoj (potrjeno s strani uporabnika 2026-08-07)**: če je dan, ko se delovni nalogi OBDELUJEJO (`datetime.now()`, ne datum prevzemnice), PETEK, se `lot_osnova` namesto iz datuma prevzemnice izračuna kot `danes + 2 dni`. Primer: obdelava v petek 7.8.2026 → `lot_osnova = 9.8.2026` → `lot = "090826"`.
   ```python
   danes = datetime.now()
   je_petek = danes.weekday() == 4
   lot_osnova = danes + timedelta(days=2) if je_petek else prevzemnica_original["datum"]
   lot = lot_osnova.strftime("%d%m%y")
   ```
   **Dodatek (potrjeno s strani uporabnika 2026-08-07)**: SAMO ob petku se ista vrednost `lot_osnova` zapiše tudi v glavo prevzemnice - `FA_PREVZEMNICA.DATUM` (nadomesti `original["datum"]` v `header_body["datum"]` glej `PUT /api/v1/FA/prevzemnica` telo spodaj) IN dodatno kot dve novi `dodatnaPolja` postavki na glavi: `DATUM_VALUTE` in `DATUM_ZA_DDV_KNJIGO` (obe = `lot_osnova`). Ob ne-petkovih dneh se `DATUM_VALUTE`/`DATUM_ZA_DDV_KNJIGO` sploh NE pošljeta (telo ostane nespremenjeno glede na prejšnjo verzijo).

2. **DATUM_UPORABNOSTI** — `lot_osnova (glej točko 1, torej datum prevzemnice ALI petek+2 dni) + FA_ARTIKEL.PROSTO_R5 (dni)`, za šifro artikla **te postavke** (ne sestavin):
   ```python
   artikel = get_artikel(sifra=postavka["sifra"], dodatna_polja=["PROSTO_R5"])
   prosto_r5_dni = int(float(artikel["dodatnaPolja"][0]["vrednost"]))  # dodatnaPolja[naziv=="PROSTO_R5"]
   datum_uporabnosti = lot_osnova + timedelta(days=prosto_r5_dni)
   ```
   **Pogoj (potrjeno s strani uporabnika 2026-07-27)**: zapiše se SAMO, če je `PROSTO_R5 >= 1`. Če je `PROSTO_R5 == 0` (ali manjka), se `DATUM_UPORABNOSTI` za to postavko sploh NE zapiše (izdelek brez roka uporabnosti) - `lotPodatki` zapis se zanjo ne pripravi. Primer: danes 27.07.2026, izdelek s `PROSTO_R5 = 2` → `datum_uporabnosti = 29.07.2026`.
   **Kam se zapiše**: `DATUM_UPORABNOSTI` **ni** stolpec tabele `FA_PREVZEMNICA_KNJIZBA` (ni ga v seznamu iz `/prevzemnica/postavka/polja`) in ni del nobenega od dveh `PUT /api/v1/FA/prevzemnica*` teles zgoraj/spodaj — gre v ločen centralni šifrant lotov: `POST`/`PUT /api/v1/FASifranti/lotPodatki`, telo `{"sifra": <sifra artikla>, "lot": <LOT iz točke 1>, "datumUporabnosti": <izračunan datum>}`. Ta klic (glej `app/process.py::run`, blok "LOT / DATUM_UPORABNOSTI") se izvede NEODVISNO od `PUT_PREVZEMNICA_DRY_RUN` (glej `app/config.py`) - torej dejansko piše v produkcijo že zdaj, za vsako postavko s `PROSTO_R5 >= 1`.
   - Pred vpisom **najprej preveri z GET-om** (`GET /api/v1/FASifranti/lotPodatki?Sifra=<sifra>&Lot=<lot>`), ali kombinacija šifra+lot že obstaja (POST vrne `409`, če že obstaja) — če obstaja, uporabi `PUT` (ažuriranje) namesto `POST`, ali preskoči, če je vrednost že enaka.

3. **CENA_VNESENA** — vrednost gre v `postavke[].dodatnaPolja` kot `{"naziv": "CENA_VNESENA", "vrednost": cena_izdelka}` (ni nativno polje v `PrevzemnicaPostavkaPutRequest`, samo dodatno polje - potrjeno preko `GET /api/v1/FA/prevzemnica/postavka/polja`). Izračun: `cena_izdelka = cena_mat + cena_op`, nato zaokroženo na **največ 5 številskih mest (significant digits, ne decimalnih mest)** pred pošiljanjem preko API klica — npr. `2.043656` → `2.0437`, `123.456789` → `123.46` (potrjeno s strani uporabnika 2026-07-24, implementirano v `_round_cena` v `app/process.py`).

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


PUT telesi namenoma vsebujeta SAMO polja, ki se dejansko spreminjajo — `dobavitelj`/`rabat1` in `kolicina`/`nabavnaCena`/`stopnjaDdv` na postavkah ostanejo nedotaknjeni (obstoječa prevzemnica se ne prepiše v celoti, samo dopolni). `DOKUMENT_DOBAVNICA` (glava-nivojsko dodatno polje) je **potreben** — vrednost `"{dn_stevilka} DN"` (potrjeno s strani uporabnika 2026-07-27), kjer je `dn_stevilka` številka DELOVNEGA NALOGA (ne prevzemnice). Sestavi takole (glej `app/process.py::_prepare_prevzemnica_update`):

```python
# PUT /api/v1/FA/prevzemnica (glava, PrevzemnicaPutRequest)
header_body = {
    "stevilka": prevzemnica_original["stevilka"],   # identifikacija V TELESU, ne query parametrov
    "leto": prevzemnica_original["leto"],
    "datum": lot_osnova if je_petek else prevzemnica_original["datum"],  # ob petku prepisano na danes+2, glej zgoraj
    "komercialist": "011",                          # string, max 5 znakov
    "skladisce": 11,
    "dodatnaPolja": [
        {"naziv": "INS_DRZAVA", "vrednost": 705},
        {"naziv": "SKLADISCE_ZA_ODPIS", "vrednost": 11},
        {"naziv": "INS_VRSTA_TRANSPORTA", "vrednost": 3},
        {"naziv": "DOKUMENT_DOBAVNICA", "vrednost": f"{dn_stevilka} DN"},
        # DATUM_VALUTE/DATUM_ZA_DDV_KNJIGO se dodata SAMO ob petku (glej zgoraj):
        # {"naziv": "DATUM_VALUTE", "vrednost": lot_osnova},
        # {"naziv": "DATUM_ZA_DDV_KNJIGO", "vrednost": lot_osnova},
    ],
}

# PUT /api/v1/FA/prevzemnica/spremembaPostavk (postavke, PrevzemnicaPutVecPostavkBaseRequest)
postavke_body = {
    "stevilka": prevzemnica_original["stevilka"],
    "leto": prevzemnica_original["leto"],
    "postavke": [
        {
            "sifra": p["sifra"],
            "zs": p["zs"],                           # obvezno - zaporedna št. postavke, native polje na GET-u
            "dodatnaPolja": [
                {"naziv": "LOT", "vrednost": lot},
                {"naziv": "INS_DRZAVA_POREKLA", "vrednost": 705},
                {"naziv": "CENA_VNESENA", "vrednost": cena_izdelka},  # glej izračun zgoraj; samo za izdelke
            ],
        }
        for p in prevzemnica_original["postavke"]
    ],
}
```

`DATUM_UPORABNOSTI` gre ločeno, preko `FASifranti/lotPodatki` (ni del zgornjih teles) — glej razdelek zgoraj.

- **Potrjeno**: `stopnjaDdv` se pri PUT-u ne pošilja — ostaja nespremenjen.
- Oba PUT klica identificirata ciljno prevzemnico preko `stevilka`/`leto` **v telesu** (glej `EVascoClient.update_prevzemnica` / `update_prevzemnica_postavke`), ne preko `StevilkaOd`/`StevilkaDo` query parametrov kot pri GET-u.

## Qt6 GUI

- Gumb **"Obdelaj"**: pošlje `POST /api/obdelaj`, backend izvede zgornjo logiko (najde DN, poišče prevzemnice, dopolni jih s PUT-om), GUI prikaže povzetek (katere prevzemnice so bile uspešno posodobljene, morebitne napake in cenovna opozorila).
- Gumb naj bo med izvajanjem onemogočen (loading indikator), da se prepreči podvojeno sprožanje.
