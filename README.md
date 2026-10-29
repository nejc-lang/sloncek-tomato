# Sloncek-Tomato

Integracija z eVasco API za obdelavo delovnih nalogov (DN) in prevzemnic.
Sestavljen je iz dveh delov:

- **backend** (`app/`) — FastAPI, teče v Dockerju, izpostavlja `POST
  /api/obdelaj`.
- **GUI** (`gui/`) — Qt6 (PySide6) desktop aplikacija z enim gumbom
  ("Obdelaj"), ki kliče backend preko HTTP.

## Poslovna logika

Od 2026-07-27 gre celoten proces v **enem koraku** (prej dvokoračen proces
brisanje obstoječe prevzemnice + kreiranje nove — glej git zgodovino).
Sistem poišče vse DN v datumskem razponu **danes-1..danes** (pokrije primer,
ko se program ne zažene točno vsak dan) s pogojema `FAKTURIRANA = 7` in
`SKLADISCE = 11`, ki še niso bili obdelani. Sledenje obdelanim DN se opira na
persistenten seznam natančnih ključev `stevilka.leto`
(`data/obdelani_dn.json`), ne na "zadnjo številko" (x1) - ker datumski
razpon pokriva 2 dni, številke DN znotraj njega niso nujno strogo
naraščajoče/brez vrzeli glede na filter. Za vsak nov DN se prebere dodatno
polje `PRE_STEVILKA` in z njo pridobi povezano prevzemnico (glava +
postavke).

Za vsako najdeno prevzemnico sistem takoj:

1. izračuna vse potrebne vrednosti (LOT, DATUM_UPORABNOSTI, CENA_VNESENA -
   glej spodaj),
2. vpiše LOT/DATUM_UPORABNOSTI v centralni šifrant lotov
   (`POST`/`PUT /api/v1/FASifranti/lotPodatki`),
3. z **enim PUT klicem dopolni OBSTOJEČO prevzemnico** (namesto da bi jo
   izbrisal in ustvaril novo): `datum`, `komercialist`, `skladisce` ter
   dodatna polja `INS_DRZAVA=705`, `INS_DRZAVA_POREKLA=705`,
   `SKLADISCE_ZA_ODPIS=11`, `INS_VRSTA_TRANSPORTA=3` (glava), `LOT` in
   `CENA_VNESENA` (na vsaki postavki, po šifri artikla).

DN se v seznam obdelanih doda **šele ob uspešni posodobitvi** njegove
prevzemnice - DN z napako (manjka `PRE_STEVILKA`, prevzemnica ni najdena,
napaka pri PUT-u ...) ostane zunaj seznama in se **samodejno poskusi znova**
ob naslednjem zagonu. To je varno, ker je PUT idempotenten - v nasprotju s
prejšnjim POST/create pristopom ponovni poskus ne podvoji zapisov.

**⚠️ PUT endpoint za `/api/v1/FA/prevzemnica` še ni objavljen v swaggerju**
(stanje 2026-07-27 - do zdaj sta bila na voljo samo `GET`/`POST`). Klic v
`EVascoClient.update_prevzemnica` je zato najboljša ocena glede na obstoječ
vzorec (identifikacija ciljne prevzemnice preko `StevilkaOd`/`StevilkaDo`
query parametrov, enako kot pri GET-u) - **ko eVasco objavi natančno
specifikacijo, je treba pot/parametre/obliko telesa preveriti in po potrebi
popraviti**.

### Testno obdobje — `PUT_PREVZEMNICA_DRY_RUN`

Dokler PUT ni objavljen, `settings.PUT_PREVZEMNICA_DRY_RUN` (`.env`,
privzeto `True`) preskoči **samo** dejanski PUT klic - vse ostalo (iskanje
DN, branje prevzemnice, izračun LOT/DATUM_UPORABNOSTI/CENA_VNESENA, pisanje
`FASifranti/lotPodatki` - ta endpoint že deluje, ni del te zastavice) teče
enako kot v produkciji. Telo, ki bi bilo poslano na PUT, se arhivira v
`data/izhodni_zahtevki/prevzemnica_posodobitev_<pre_stevilka>_<timestamp>.json`
(polje `"dry_run": true`), rezultat v odgovoru/GUI dobi status `pripravljeno`
namesto `uspesno`. Prizadeti DN se **namenoma NE** doda v
`obdelani_dn.json` - ko se `PUT_PREVZEMNICA_DRY_RUN` nastavi na `False` (brez
posega v kodo), se isti DN samodejno dejansko obdela ob naslednjem zagonu.

`DOKUMENT_DOBAVNICA` (sledljivost do izvirnega DN, uporabljena v prejšnjem
POST-pristopu) ni več potrebna - prevzemnica se ne ustvari na novo, ostane
ista, samo dopolnjena, zato sledljivost do DN ni izgubljena.

Izračuni na postavko (vsi izvedeni pred PUT klicem, preko
`_prepare_prevzemnica_update` v `app/process.py`):

- **`LOT`** (format `ddmmyy`) se izračuna iz datuma **obstoječe
  prevzemnice**, enak za vse postavke iste prevzemnice.
- **`DATUM_UPORABNOSTI`** = datum obstoječe prevzemnice + `PROSTO_R5` dni
  za šifro artikla te postavke (`FA_ARTIKEL.PROSTO_R5`, dodatno polje).
  Ni stolpec prevzemnice-postavke, ampak gre v ločen centralni šifrant
  lotov (`FASifranti/lotPodatki`, ključ šifra+lot) - glej zgoraj.
- **`CENA_VNESENA`** se zapiše **samo za artikle tipa izdelek** - artikel je
  "izdelek" natanko takrat, ko ima lasten normativ (recept), t.j. nastopa kot
  `artikel` v `GET /api/v1/FASifranti/artikel/normativ`. Artikli tipa
  **material** (nastopajo samo kot sestavina/`normativ` v receptu nekega
  izdelka, nimajo lastnega recepta) svoj `CENA_VNESENA` ne dobijo - njihova
  cena (`NABAVNA_CENA`) se samo **pridobi** in uporabi pri izračunu cene
  nadrejenega izdelka, na lastno postavko materiala pa se ne zapiše. Za
  izdelke je `CENA_VNESENA = cena_mat + cena_op`:
  - `cena_mat` - sestavine izdelka, utežene s količino in kalo
    (`nabavna_cena * kolicina * (1 + procentKala / 100)` - `procentKala` je
    pravi odstotek, npr. `12.0` = 12%, zato se deli s 100; potrjeno na živih
    podatkih na artiklu P03350/sestavina 60519 s `procentKala=12.0` - brez
    deljenja bi to pomenilo 13x nabavno ceno sestavine namesto realnega
    ~12% kala), nabavna cena posamezne sestavine pa iz
    `GET /api/v1/FASifranti/artikel` (dodatno polje `NABAVNA_CENA`). Če
    sestavina nima `NABAVNA_CENA` (`None`/`0`), se doda opozorilo v
    `warnings` (GUI ga prikaže) - `cena_mat` je v tem primeru podcenjena, ne
    pa napačno prekinjena (živi podatki 2026-07-23: 26/418 sestavin v
    šifrantu nima `NABAVNA_CENA`).
  - `cena_op` - `Σ redneUre * urna_postavka` po vseh operacijah izdelka, iz
    `GET /api/v1/FASifranti/artikel/normativOperacije?Normativ=<sifra>`
    (⚠️ `Normativ=`, ne `Artikel=` - ta parameter vrača obratno smer, glej
    bug opisan pri `cena_mat`), privzeto `0`, če izdelek nima operacij.
    `pripravneUre` se **ne** upošteva - samo `redneUre` (potrjeno s strani
    uporabnika). Če je `redneUre = 0` za neko operacijo, se doda
    informativno opozorilo v `warnings` - ne pomeni napake, `cena_op` za to
    operacijo je pač `0`.

    **Urna postavka**: pridobi se iz `FA_ARTIKEL.PRODAJNA_CENA` **šifre
    same operacije** (native polje `prodajnaCena` na
    `GET /api/v1/FASifranti/artikel?Sifra=<sifra_operacije>`), ne iz fiksne
    konfiguracijske vrednosti - npr. operacija `P50000` ("P strošek za
    francosko solato") ima `prodajnaCena=0.2`. Če šifra operacije nima
    `prodajnaCena` (`None`/`0`), se doda opozorilo v `warnings` (`cena_op`
    je v tem primeru podcenjen za to operacijo).

  Pred pošiljanjem preko API klica se `cena_izdelka` (`cena_mat + cena_op`)
  zaokroži na **največ 5 številskih mest (significant digits, ne decimalnih
  mest)** - npr. `2.043656` → `2.0437`, `123.456789` → `123.46`
  (`_round_cena` v `app/process.py`).

  **Kontrola prodajne cene**: po izračunu se `CENA_VNESENA` primerja z
  najnižjo od dveh dodatnih polj `PRODAJNA_CENA3`/`PRODAJNA_CENA4` na
  izdelku samem (`GET
  /api/v1/FASifranti/artikel?Sifra=<sifra_izdelka>&DodatnaPolja=PRODAJNA_CENA3,PRODAJNA_CENA4`).
  Če je `CENA_VNESENA` **višja** od te najnižje prodajne cene (izdelek bi se
  prodajal z izgubo), se doda opozorilo (`warnings`, prefiks
  `CENA_OPOZORILO:`) - GUI ga izpiše z **rdečim tekstom**
  (`gui/main.py::append_log`, prek `QTextCharFormat`). `0`/manjkajoča
  vrednost na `PRODAJNA_CENA3`/`4` pomeni "cena ni nastavljena" in se izloči
  iz primerjave; če nobena od obeh ni nastavljena, se primerjava izpusti (ni
  s čim primerjati).

### Arhiv obdelanih zahtevkov

Za vsako uspešno posodobljeno prevzemnico se telo PUT klica (+ lotPodatki
načrt - odločitev POST/PUT/brez sprememb za vsak LOT zapis) zapiše v
izhodno JSON datoteko
(`data/izhodni_zahtevki/prevzemnica_posodobitev_<pre_stevilka>_<timestamp>.json`)
- služi kot trajen arhiv in sledljivost delovanja aplikacije.

Ker je vse en klic backenda (`POST /api/obdelaj`), ni več koncepta "seje, ki
čaka na naslednji korak" - vsak zagon je samostojen in dokončan. Uspeh/napaka
je znan takoj, na ravni **posamezne prevzemnice** (ne "vse ali nič" za cel
batch, kot je veljalo za prejšnji Korak 2) - napaka pri eni prevzemnici ne
prepreči obdelave ostalih, prizadeta pa se samodejno poskusi znova ob
naslednjem zagonu (glej zgoraj).

## Persistenca

Vsi podatki se hranijo v `data/` (JSON datoteke), ki je v Dockerju mountan
kot volume (`./data:/app/data` v `docker-compose.yml`), da se ohranijo med
restarti:

- `data/obdelani_dn.json` — persistenten seznam že USPEŠNO obdelanih DN
  (ključi `stevilka.leto`), ne "zadnja številka" (x1) — glej zgoraj.
- `data/izhodni_zahtevki/prevzemnica_posodobitev_<pre_stevilka>_<timestamp>.json`
  — arhiv telesa PUT zahtevka (+ lotPodatki načrt) za vsako obdelano
  prevzemnico, za sledljivost delovanja aplikacije.

## Avtentikacija GUI <-> backend

Backend v LAN posluša na vseh vmesnikih gostitelja (`0.0.0.0:8090`) brez
TLS, zato je zaščiten s skupnim skrivnim ključem (`API_KEY`), ne le z
zanašanjem na to, da je omrežje "zaupanja vredno":

- Ključ je ena naključna vrednost, nastavljena **enako** v backend `.env`
  (`API_KEY=...`) in v GUI `gui/.env` (`API_KEY=...`).
- GUI ga ob vsakem klicu pošlje v headerju `X-API-Key: <ključ>`
  (`gui/main.py::ApiWorker.run`).
- Backend ga preveri v `app/main.py::require_api_key` (FastAPI
  `Depends`, uporabljeno na `POST /api/obdelaj`) prek
  `hmac.compare_digest` (zaščita pred timing napadi). `GET /` ostane brez
  avtentikacije - vrne samo statičen status, uporaben kot health-check iz
  omrežja.
- Manjkajoč/napačen ključ → `401 Unauthorized`. Če backend nima
  nastavljenega `API_KEY` (prazna vrednost v `.env`), zavrne **vse**
  klice na `/api/*` z `500` - to prepreči nehoten zagon brez zaščite.

**Generiranje ključa:**

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

Zgenerirano vrednost prekopiraj v `API_KEY=` v `.env` (backend) **in** v
`gui/.env` (GUI) - vrednosti se morata ujemati.

**Varnostni obseg:** ta ključ ščiti pred nepooblaščenim dostopom do
backend endpointov znotraj lokalnega omrežja (npr. drug uporabnik/naprava
v isti podmreži), ne pa pred prisluškovanjem prometa - promet med GUI in
backendom **ni šifriran** (navaden HTTP). Za to okolje (zaupanja vredno
lokalno omrežje, en backend, ena GUI naprava) je to zadosti; če bi GUI
kdaj dostopal preko interneta ali manj zaupanja vrednega omrežja, bi bilo
treba dodati TLS (npr. reverse proxy z HTTPS pred containerjem).
Rotacija ključa: zamenjaj vrednost v obeh `.env` datotekah in ponovno
zaženi backend (`docker compose up -d --build`) ter GUI.

## Zagon backenda (Docker)

1. Izpolni `.env` (`API_USER`, `API_PASS`, `API_TAX_NUMBER`,
   `EVASCO_BASE_URL`, `API_KEY` - glej "Avtentikacija GUI <-> backend"
   zgoraj).

2. Zgradi in zaženi container:

   ```bash
   docker compose up -d --build
   ```

   Backend posluša na `http://<IP-strežnika>:8090` (`POST /api/obdelaj`) —
   ker docker-compose objavi vrata na vseh vmesnikih gostitelja, je
   dosegljiv tudi iz drugih računalnikov v lokalnem omrežju (glej spodaj).

## Zagon GUI (Qt6, na ločenem računalniku)

GUI ni del Docker containerja (Qt6 GUI v containerju bi zahteval X11
forwarding) in **teče na drugem računalniku v lokalnem omrežju** kot
backend — kliče ga preko HTTP na naslov, ki ga nastaviš v `gui/.env`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r gui/requirements.txt

cp gui/.env.example gui/.env
# uredi gui/.env in nastavi:
#   API_BASE_URL na naslov (LAN IP) računalnika, kjer teče backend, npr.
#     API_BASE_URL=http://192.168.1.50:8090
#   API_KEY na isto vrednost, kot je nastavljena v backend .env (glej
#     "Avtentikacija GUI <-> backend" zgoraj)

python -m gui.main
```

Preveri, da je vrata 8090 na računalniku z backendom dosegljivo iz
lokalnega omrežja (odprto v požarnem zidu gostitelja) — Docker sam po sebi
vrata `8090:8090` objavi na vseh omrežnih vmesnikih gostitelja.

Gumb **"Obdelaj"** pošlje `POST /api/obdelaj` in prikaže povzetek (katere
prevzemnice so bile uspešno posodobljene, morebitne napake in cenovna
opozorila). Med izvajanjem je onemogočen (spinner), da se prepreči
podvojeno sprožanje.

## Status implementacije

- [x] Struktura projekta, Docker konfiguracija backenda + persistenten
      `data/` volume
- [x] Qt6 GUI klient — gumb "Obdelaj", spinner, log okno, konfiguracija za
      ločen računalnik preko `gui/.env`
- [x] `EVascoClient` — avtentikacija, `get_dn_nalogi`,
      `get_prevzemnica_by_stevilka`, `get_artikli` (+`DodatnaPolja`),
      `get_artikel_normativ`, `get_artikel_normativ_operacije`,
      `get_lot_podatki`/`create_lot_podatki`/`update_lot_podatki`,
      `update_prevzemnica` (PUT)
- [x] Enokorakovna obdelava (`app/process.py::run`) — iskanje DN (razpon
      danes-1..danes, obe možni leti), sledenje persistentnemu seznamu
      obdelanih DN (`stevilka.leto`, dodan šele ob uspehu), izračun
      LOT/DATUM_UPORABNOSTI/CENA_VNESENA za vsako postavko, vpis lotPodatki
      in PUT posodobitev prevzemnice, arhiviranje zahtevkov
- [x] Testni način (`PUT_PREVZEMNICA_DRY_RUN=True`, privzeto) — PUT klic se
      preskoči, telo se izračuna in arhivira, GUI prikaže "pripravljeno"
- [ ] **PUT `/api/v1/FA/prevzemnica` še ni objavljen v swaggerju** —
      preveriti in po potrebi popraviti pot/parametre/obliko telesa v
      `EVascoClient.update_prevzemnica`, nato `PUT_PREVZEMNICA_DRY_RUN=False`

## Struktura

```
app/
  main.py             FastAPI app (backend, v Dockerju) - POST /api/obdelaj
  config.py           branje env spremenljivk (DATA_DIR, STEVILKA_PADDING, ...)
  evasco_client.py     eVasco API klient (avtentikacija, DN, prevzemnica, artikel, normativ, lotPodatki)
  storage.py           persistenca: obdelani_dn.json, arhiv zahtevkov
  process.py           run() - poslovna logika (iskanje DN, izračuni, PUT posodobitev prevzemnice)
gui/
  main.py             Qt6 GUI klient - gumb Obdelaj
  requirements.txt      odvisnosti GUI klienta (host)
  .env.example         predloga za API_BASE_URL (naslov backenda v LAN)
  gui.spec              PyInstaller spec (gui/main.py -> onefile .exe)
  build_exe.bat         Windows: zgradi gui\dist\Sloncek-Tomato-GUI.exe
  setup.iss             Inno Setup skripta (namestitveni jezik: EN)
  build_installer.bat   Windows: build_exe.bat + Inno Setup -> Setup.exe
  INSTALLER.md           navodila za gradnjo namestitvenega paketa
requirements.txt        odvisnosti backenda (Docker)
data/                    persistenca (volume v Dockerju, .gitignore na hostu)
```

## Gradnja Windows namestitvenega paketa (.exe) za GUI

GUI (`gui/main.py`) je mogoče spakirati v samostojen Windows `.exe` in
namestitveni program - glej `gui/INSTALLER.md` za podroben opis. Na kratko
(zagnati na Windows, ne v tem repozitoriju/Dockerju):

```bat
cd gui
build_installer.bat
```

Namestitveni program (`dist_installer\Sloncek-Tomato-GUI-Setup.exe`)
teče v angleščini, namesti vse potrebne Python odvisnosti GUI-ja (vgrajene v
`.exe` preko PyInstaller - ni potreben ločen Python na ciljnem računalniku)
ter po potrebi (samo če manjka) samodejno namesti Microsoft Visual C++
Redistributable, ki ga PySide6/Qt6 potrebuje na Windows.
