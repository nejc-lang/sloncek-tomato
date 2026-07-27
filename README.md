# DN-PREVZEMNICA-Tomato

Integracija z eVasco API za obdelavo delovnih nalogov (DN) in prevzemnic.
Sestavljen je iz dveh delov:

- **backend** (`app/`) — FastAPI, teče v Dockerju, izpostavlja `POST
  /api/step1` in `POST /api/step2`.
- **GUI** (`gui/`) — Qt6 (PySide6) desktop aplikacija z dvema gumboma
  ("Korak 1", "Korak 2"), ki kliče backend preko HTTP.

## Poslovna logika

**Korak 1** poišče vse DN v datumskem razponu **danes-1..danes** (pokrije
primer, ko se program ne zažene točno vsak dan) s pogojema
`FAKTURIRANA = 7` in `SKLADISCE = 11`, ki še niso bili obdelani. Sledenje
obdelanim DN se opira na persistenten seznam natančnih ključev
`stevilka.leto` (`data/obdelani_dn.json`), ne na "zadnjo številko" (x1) -
ker datumski razpon pokriva 2 dni, številke DN znotraj njega niso nujno
strogo naraščajoče/brez vrzeli glede na filter. Za vsak nov DN se prebere
dodatno polje `PRE_STEVILKA` in z njo pridobi povezano prevzemnico (glava +
postavke).

Za vsako najdeno prevzemnico se **že v tem koraku** izračuna celotno telo
nove prevzemnice za Korak 2 (glej spodaj) in shrani v sejo
(`data/sessions/seja_<timestamp>.json`, status `cakajoce_korak2`) - Korak 2
tako samo še izvede pisalne klice nad že pripravljenimi podatki, brez
ponovnega branja šifrantov. Seznam obdelanih DN se posodobi takoj po
shranjevanju seje, ne šele po Koraku 2. GUI izpiše seznam številk
prevzemnic, ki jih mora uporabnik ročno izbrisati v eVasco sistemu.

**Korak 2** (ročno sproženo šele, ko so prevzemnice iz zadnje seje
dejansko izbrisane v eVasco) za vsako pripravljeno prevzemnico iz seje
izvede dva pisalna klica: vpis LOT/DATUM_UPORABNOSTI v centralni šifrant
lotov (`POST`/`PUT /api/v1/FASifranti/lotPodatki`) in kreiranje nove
prevzemnice (`POST /api/v1/FA/prevzemnica`) s podatki iz izbrisane
prevzemnice + dodatnimi vrednostmi (glava: `INS_DRZAVA=705`,
`INS_DRZAVA_POREKLA=705`, `SKLADISCE_ZA_ODPIS=11`,
`INS_VRSTA_TRANSPORTA=3`, `DOKUMENT_DOBAVNICA=f"{pre_stevilka} DN"` za
sledljivost do izvirnika; postavke: `sifra`, `kolicina`, `nabavnaCena`,
`stopnjaDdv`, `LOT`, `CENA_VNESENA`). eVasco avtomatsko dodeli novo
številko prevzemnice, ki se shrani nazaj v sejo.

Izračuni na postavko (vsi izvedeni v Koraku 1, preko `_prepare_new_prevzemnica`
v `app/process.py`):

- **`stopnjaDdv`** ni prisoten v odgovoru GET prevzemnice, zato se pridobi
  iz šifranta artiklov (`GET /api/v1/FASifranti/artikel`, polje
  `stopnjaDDV`) po šifri vsake postavke. (Prvotna ideja — vzeti
  `stopnjaDdv` iz postavk povezanega DN — se je pri testiranju na
  produkciji izkazala za napačno: DN postavke in prevzemnica postavke
  nimajo skupnih šifer artiklov, ker predstavljajo različne stvari —
  naročene artikle na DN proti dejansko prejetim artiklom na prevzemnici.)
- **`LOT`** (format `ddmmyy`) se izračuna iz datuma **originalne (izbrisane)
  prevzemnice**, enak za vse postavke iste prevzemnice.
- **`DATUM_UPORABNOSTI`** = datum originalne prevzemnice + `PROSTO_R5` dni
  za šifro artikla te postavke (`FA_ARTIKEL.PROSTO_R5`, dodatno polje).
  Ni stolpec prevzemnice-postavke, ampak gre v ločen centralni šifrant
  lotov (`FASifranti/lotPodatki`, ključ šifra+lot) - glej Korak 2 zgoraj.
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
    `warnings` seje (GUI ga prikaže) - `cena_mat` je v tem primeru
    podcenjena, ne pa napačno prekinjena (živi podatki 2026-07-23: 26/418
    sestavin v šifrantu nima `NABAVNA_CENA`).
  - `cena_op` - `Σ redneUre * urna_postavka` po vseh operacijah izdelka, iz
    `GET /api/v1/FASifranti/artikel/normativOperacije?Normativ=<sifra>`
    (glej opombo o `Normativ=` vs. `Artikel=` zgoraj), privzeto `0`, če
    izdelek nima operacij.

    **Popravek 2026-07-24**: analiza operacij v prejšnji verziji tega
    dokumenta (2026-07-23) je poizvedovala z napačnim parametrom
    (`Artikel=` namesto `Normativ=` - glej bug opisan zgoraj pri `cena_mat`),
    zato je bila napačna - artikel 04949 v resnici IMA eno operacijo
    (`P50000`, `redneUre=1.8`), analiza pa je (napačno) pokazala prazen
    seznam. Vsak izdelek je treba znova preveriti z `Normativ=` poizvedbo.

    **Popravek 2026-07-24 (2)**: `pripravneUre` se **ne** upošteva - samo
    `redneUre` (potrjeno s strani uporabnika). Če je `redneUre = 0` za neko
    operacijo, se doda informativno opozorilo v `warnings` (GUI ga prikaže) -
    ne pomeni napake, `cena_op` za to operacijo je pač `0`.

    **Urna postavka** (potrjeno s strani uporabnika 2026-07-24): pridobi se
    iz `FA_ARTIKEL.PRODAJNA_CENA` **šifre same operacije** (native polje
    `prodajnaCena` na `GET /api/v1/FASifranti/artikel?Sifra=<sifra_operacije>`),
    ne iz fiksne konfiguracijske vrednosti - npr. operacija `P50000` ("P
    strošek za francosko solato") ima `prodajnaCena=0.2`. Če šifra operacije
    nima `prodajnaCena` (`None`/`0`), se doda opozorilo v `warnings`
    (`cena_op` je v tem primeru podcenjen za to operacijo).

  Pred pošiljanjem preko API klica se `cena_izdelka` (`cena_mat + cena_op`)
  zaokroži na **največ 5 številskih mest (significant digits, ne decimalnih
  mest)** - npr. `2.043656` → `2.0437`, `123.456789` → `123.46` (potrjeno s
  strani uporabnika 2026-07-24, `_round_cena` v `app/process.py`).

  **Kontrola prodajne cene** (potrjeno s strani uporabnika 2026-07-24, smer
  primerjave popravljena isti dan): po izračunu se `CENA_VNESENA` primerja z
  najnižjo od dveh dodatnih polj `PRODAJNA_CENA3`/`PRODAJNA_CENA4` na izdelku
  samem (`GET
  /api/v1/FASifranti/artikel?Sifra=<sifra_izdelka>&DodatnaPolja=PRODAJNA_CENA3,PRODAJNA_CENA4`).
  Če je `CENA_VNESENA` **višja** od te najnižje prodajne cene (izdelek bi se
  prodajal z izgubo), se doda opozorilo (`warnings`, prefiks
  `CENA_OPOZORILO:`) - GUI ga izpiše z **rdečim tekstom**
  (`gui/main.py::append_log`, prek `QTextCharFormat`). `0`/manjkajoča
  vrednost na `PRODAJNA_CENA3`/`4` pomeni "cena ni nastavljena" (isti
  dogovor kot pri `NABAVNA_CENA`) in se izloči iz primerjave; če nobena od
  obeh ni nastavljena, se primerjava izpusti (ni s čim primerjati).
  (Prvotna implementacija je pomotoma opozarjala v obratnem, napačnem
  primeru.)

### Testno obdobje — pisalni klici so zakomentirani v kodi

Med testnim obdobjem sta oba dejanska pisalna klica proti eVasco v
`app/process.py::run_step2` **zakomentirana v izvorni kodi** (ne preko
konfiguracijske zastavice) — `client.create_lot_podatki`/`update_lot_podatki`
(vpis LOT/DATUM_UPORABNOSTI) in `client.create_prevzemnica(body)` (kreiranje
nove prevzemnice). Obravnavata se kot eno samo dejanje na prevzemnico in se
odkomentirata skupaj, tako da do vnosa v produkcijski eVasco sistem ne more
priti po nesreči. GUI in backend sicer delujeta popolnoma enako kot bosta
delovala v produkciji — vsa obvestila (seznam prevzemnic za izbris, povzetek
Koraka 2, status seje) so enaka, ni posebnega "dry-run" opozorila.

Za vsako obdelano prevzemnico se ne glede na to telo zahtevka zapiše v
izhodno JSON datoteko
(`data/izhodni_zahtevki/prevzemnica_zahtevek_<stevilka>_<timestamp>.json`) —
to služi kot arhiv in sledljivost delovanja aplikacije, tudi po prehodu v
produkcijo. `lot_zapisi` (LOT/DATUM_UPORABNOSTI) se ne shranjujejo v ločeno
datoteko - živijo samo znotraj seje (`data/sessions/seja_*.json`), od koder
jih Korak 2 uporabi neposredno, ko bo pisalni klic `create_lot_podatki`/
`update_lot_podatki` odkomentiran.

Ko je Korak 2 potrjen za produkcijo: v `app/process.py::run_step2`
odkomentiraj oba bloka (lotPodatki zanko in vrstico
`# response = client.create_prevzemnica(body)`, ter zbriši `response = None`
pod njo). Rezultat (`nova_stevilka`/`leto`) se takrat samodejno doda v
odgovor in v GUI obvestilo.

### Delna obdelava / ponovni zagon Koraka 2

Če Korak 2 delno spodleti (napaka pri eni od prevzemnic), sistem to zapiše
po posameznih postavkah (uspešno/napaka) in ob ponovnem zagonu Koraka 2
preskoči tiste, ki so že bile uspešno obdelane — obdela samo manjkajoče.
Status seje se v `obdelano` spremeni, ko so **vse** prevzemnice uspešno
obdelane.

## Persistenca

Vsi podatki se hranijo v `data/` (JSON datoteke), ki je v Dockerju mountan
kot volume (`./data:/app/data` v `docker-compose.yml`), da se ohranijo med
restarti:

- `data/obdelani_dn.json` — persistenten seznam že obdelanih DN (ključi
  `stevilka.leto`), ne "zadnja številka" (x1) — glej Korak 1 zgoraj.
- `data/sessions/seja_<timestamp>.json` — ena seja na zagon Koraka 1 (DN-ji,
  pripadajoče prevzemnice, že pripravljena telesa za Korak 2, status,
  rezultati Koraka 2).
- `data/izhodni_zahtevki/prevzemnica_zahtevek_<stevilka>_<timestamp>.json`
  — arhiv pripravljenega telesa zahtevka za Korak 2, za sledljivost
  delovanja aplikacije (`lot_zapisi` se ne shranjuje ločeno v datoteko,
  ostane samo v seji).

## Avtentikacija GUI <-> backend

Backend v LAN posluša na vseh vmesnikih gostitelja (`0.0.0.0:8090`) brez
TLS, zato je zaščiten s skupnim skrivnim ključem (`API_KEY`), ne le z
zanašanjem na to, da je omrežje "zaupanja vredno":

- Ključ je ena naključna vrednost, nastavljena **enako** v backend `.env`
  (`API_KEY=...`) in v GUI `gui/.env` (`API_KEY=...`).
- GUI ga ob vsakem klicu pošlje v headerju `X-API-Key: <ključ>`
  (`gui/main.py::ApiWorker.run`).
- Backend ga preveri v `app/main.py::require_api_key` (FastAPI
  `Depends`, uporabljeno na `POST /api/step1` in `POST /api/step2`) prek
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

   Backend posluša na `http://<IP-strežnika>:8090` (`POST /api/step1`,
   `POST /api/step2`) — ker docker-compose objavi vrata na vseh vmesnikih
   gostitelja, je dosegljiv tudi iz drugih računalnikov v lokalnem omrežju
   (glej spodaj).

## Zagon GUI (Qt6, na ločenem računalniku)

GUI ni del Docker containerja (Qt6 GUI v containerju bi zahteval X11
forwarding) in **teče na drugem računalniku v lokalnem omrežju** kot
backend — kliče ga preko HTTP na naslov, ki ga nastaviš v `gui/.env`.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-gui.txt

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

- Gumb **"Korak 1"** pošlje `POST /api/step1` in prikaže seznam prevzemnic
  za ročni izbris v eVasco (ali sporočilo, da ni novih DN).
- Gumb **"Korak 2"** pošlje `POST /api/step2` in prikaže povzetek
  obdelanih prevzemnic ter morebitne napake.

Oba gumba sta med izvajanjem onemogočena (spinner), da se prepreči
podvojeno sprožanje.

## Status implementacije

- [x] Struktura projekta, Docker konfiguracija backenda + persistenten
      `data/` volume
- [x] Qt6 GUI klient — gumba "Korak 1" / "Korak 2", spinner, log okno,
      konfiguracija za ločen računalnik preko `gui/.env`
- [x] `EVascoClient` — avtentikacija, `get_dn_nalogi`,
      `get_prevzemnica_by_stevilka`, `get_artikli` (+`DodatnaPolja`),
      `get_artikel_normativ`, `get_artikel_normativ_operacije`,
      `get_lot_podatki`/`create_lot_podatki`/`update_lot_podatki`,
      `create_prevzemnica`
- [x] Korak 1 — iskanje DN (razpon danes-1..danes, obe možni leti),
      sledenje persistentnemu seznamu obdelanih DN (`stevilka.leto`),
      izračun LOT/DATUM_UPORABNOSTI/CENA_VNESENA/stopnjaDdv za vsako
      postavko, shranjevanje seje
- [x] Korak 2 — izvedba pripravljenih pisalnih klicev (lotPodatki +
      create_prevzemnica), arhiviranje zahtevkov, delna obdelava /
      idempotentni ponovni zagon; oba dejanska pisalna klica zakomentirana
      (testno obdobje)
- [ ] Produkcijska potrditev in odkomentiranje pisalnih klicev v
      `app/process.py::run_step2`

## Struktura

```
app/
  main.py             FastAPI app (backend, v Dockerju) - POST /api/step1, /api/step2
  config.py           branje env spremenljivk (DATA_DIR, STEVILKA_PADDING, ...)
  evasco_client.py     eVasco API klient (avtentikacija, DN, prevzemnica, artikel, normativ, lotPodatki)
  storage.py           persistenca: obdelani_dn.json, seje, arhiv zahtevkov
  process.py           run_step1() / run_step2() - poslovna logika (vklj. izračune za Korak 2)
gui/
  main.py             Qt6 GUI klient - gumba Korak 1 / Korak 2
  .env.example         predloga za API_BASE_URL (naslov backenda v LAN)
  gui.spec              PyInstaller spec (gui/main.py -> onefile .exe)
  build_exe.bat         Windows: zgradi dist\DN-PREVZEMNICA-Tomato-GUI.exe
  setup.iss             Inno Setup skripta (namestitveni jezik: EN)
  build_installer.bat   Windows: build_exe.bat + Inno Setup -> Setup.exe
  INSTALLER.md           navodila za gradnjo namestitvenega paketa
requirements.txt        odvisnosti backenda (Docker)
requirements-gui.txt     odvisnosti GUI klienta (host)
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

Namestitveni program (`dist_installer\DN-PREVZEMNICA-Tomato-GUI-Setup.exe`)
teče v angleščini, namesti vse potrebne Python odvisnosti GUI-ja (vgrajene v
`.exe` preko PyInstaller - ni potreben ločen Python na ciljnem računalniku)
ter po potrebi (samo če manjka) samodejno namesti Microsoft Visual C++
Redistributable, ki ga PySide6/Qt6 potrebuje na Windows.
