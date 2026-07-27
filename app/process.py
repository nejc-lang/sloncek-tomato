import json
from datetime import datetime, timedelta
from typing import Any

from app import storage
from app.config import settings
from app.evasco_client import EVascoClient


def _get_dodatno_polje(dodatna_polja: list[dict] | None, naziv: str) -> Any:
    for polje in dodatna_polja or []:
        if polje.get("naziv") == naziv:
            return polje.get("vrednost")
    return None


def _round_cena(vrednost: float, mesta: int = 5) -> float:
    """Zaokroži na `mesta` skupnih številskih mest (significant digits, ne decimalnih mest) -

    eVasco API pri CENA_VNESENA sprejme ceno na največ 5 številskih mest,
    npr. 2.043656 -> 2.0437 (ne 2.04366, kar bi bilo 5 decimalnih mest).
    """
    if vrednost == 0:
        return 0.0
    return float(f"{vrednost:.{mesta}g}")


def _fetch_dodatno_polje_lookup(client: EVascoClient, sifre: list[str], polje_naziv: str) -> dict[str, Any]:
    """šifra artikla -> vrednost enega dodatnega polja (npr. PROSTO_R5, NABAVNA_CENA).

    Ti podatki niso native polja na ArtikelResponse, zato jih je treba
    zahtevati eksplicitno preko DodatnaPolja parametra.
    """
    if not sifre:
        return {}
    artikli = client.get_artikli(sifre, dodatna_polja=[polje_naziv])
    return {
        a["sifra"]: _get_dodatno_polje(a.get("dodatnaPolja"), polje_naziv)
        for a in artikli
        if a.get("sifra") is not None
    }


def _fetch_prodajna_cena_lookup(client: EVascoClient, sifre: list[str]) -> dict[str, float]:
    """šifra artikla operacije -> urna postavka (FA_ARTIKEL.PRODAJNA_CENA, native polje `prodajnaCena`).

    Uporablja se za cena_op: urna postavka dela se pridobi iz prodajne cene
    šifre same operacije (npr. P50000 "P strošek za francosko solato"), ne iz
    fiksne konfiguracijske vrednosti - potrjeno s strani uporabnika 2026-07-24.
    """
    if not sifre:
        return {}
    artikli = client.get_artikli(sifre)
    return {a["sifra"]: a.get("prodajnaCena") for a in artikli if a.get("sifra") is not None}


def _fetch_prodajne_cene_izdelka_lookup(client: EVascoClient, sifre: list[str]) -> dict[str, tuple[Any, Any]]:
    """šifra izdelka -> (PRODAJNA_CENA3, PRODAJNA_CENA4) dodatni polji.

    Uporablja se za primerjavo s CENA_VNESENA (opozorilo, če izdelek prodajamo
    pod nabavno/proizvodno ceno) - potrjeno s strani uporabnika 2026-07-24.
    Oba klica sta v enem GET-u (DodatnaPolja sprejme več naziv-ov naenkrat).
    """
    if not sifre:
        return {}
    artikli = client.get_artikli(sifre, dodatna_polja=["PRODAJNA_CENA3", "PRODAJNA_CENA4"])
    lookup: dict[str, tuple[Any, Any]] = {}
    for a in artikli:
        sifra = a.get("sifra")
        if sifra is None:
            continue
        dodatna_polja = a.get("dodatnaPolja")
        lookup[sifra] = (
            _get_dodatno_polje(dodatna_polja, "PRODAJNA_CENA3"),
            _get_dodatno_polje(dodatna_polja, "PRODAJNA_CENA4"),
        )
    return lookup


def _fetch_normativ_lookup(client: EVascoClient, sifre: list[str]) -> dict[str, list[dict]]:
    """šifra postavke -> seznam sestavin ({artikel: šifra_sestavine, kolicina, procentKala, ...}).

    `get_artikel_normativ` poizveduje z `Normativ=`, zato ima vsak vrnjen
    element `normativ` = šifra postavke (ta, ki jo iščemo) in `artikel` =
    šifra sestavine - obratno od poimenovanja parametra. Zato se tu grupira
    po `normativ` polju, komponenta pa se bere iz `artikel` polja (glej
    _prepare_prevzemnica_update).
    """
    if not sifre:
        return {}
    lookup: dict[str, list[dict]] = {}
    for sestavina in client.get_artikel_normativ(sifre):
        normativ = sestavina.get("normativ")
        if normativ is None:
            continue
        lookup.setdefault(normativ, []).append(sestavina)
    return lookup


def _fetch_operacije_lookup(client: EVascoClient, sifre: list[str]) -> dict[str, list[dict]]:
    """šifra postavke -> seznam operacij ({artikel: šifra_operacije, redneUre, pripravneUre, ...}).

    Glej opombo v `_fetch_normativ_lookup` - `normativ` polje = šifra
    postavke, `artikel` polje = šifra same operacije (npr. P50000). Šifra
    operacije se rabi naprej za pridobitev urne postavke (glej
    _fetch_prodajna_cena_lookup), zato se tu (za razliko od prejšnje verzije)
    ne sešteva takoj, ampak ohrani seznam surovih vrstic.
    """
    if not sifre:
        return {}
    lookup: dict[str, list[dict]] = {}
    for operacija in client.get_artikel_normativ_operacije(sifre):
        normativ = operacija.get("normativ")
        if normativ is None:
            continue
        lookup.setdefault(normativ, []).append(operacija)
    return lookup


def _prepare_prevzemnica_update(
    client: EVascoClient, original: dict, dn_stevilka: str
) -> tuple[dict, dict, list[dict], list[str]]:
    """Sestavi telesi PUT posodobitve OBSTOJEČE prevzemnice + lotPodatki zapise + opozorila.

    Od 2026-07-27 se prevzemnica ne briše in ponovno ustvarja (glej stari
    dvokoračni proces v git zgodovini) - namesto tega se z DVEMA API PUT
    klicema dopolnijo vrednosti na obstoječi prevzemnici (ločena endpointa,
    objavljena v swaggerju 2026-07-27):
    - `PUT /api/v1/FA/prevzemnica` (glava): datum, komercialist, skladisce,
      ins_drzava, skladisce_za_odpis, ins_vrsta_transporta. Shema
      (`PrevzemnicaPutRequest`) NIMA `postavke` polja (additionalProperties:
      false), zato gredo spremembe postavk v ločen klic spodaj.
    - `PUT /api/v1/FA/prevzemnica/spremembaPostavk` (postavke): lot,
      cena_vnesena IN ins_drzava_porekla - vse tri so postavka-nivojska
      polja (tabela FA_PREVZEMNICA_KNJIZBA, potrjeno preko
      `GET /api/v1/FA/prevzemnica/postavka/polja` 2026-07-27), NE
      glava-nivojska kot je predvideval prejšnji (nedokumentiran) TODO.
      Vsaka postavka potrebuje `zs` (zaporedna številka, native polje na GET
      odgovoru postavke) poleg `sifra`.

    `datum_uporabnosti` gre še vedno ločeno preko FASifranti/lotPodatki (ni
    veljavno polje na postavki prevzemnice, glej _fetch_dodatno_polje_lookup
    klic za PROSTO_R5 spodaj).

    `dn_stevilka` je številka DELOVNEGA NALOGA (ne prevzemnice/dobavnice) -
    uporabi se v glava-nivojskem DOKUMENT_DOBAVNICA dodatnem polju kot
    `"{dn_stevilka} DN"` (potrjeno s strani uporabnika 2026-07-27).

    Telesi NAMENOMA ne vsebujeta `dobavitelj`/`rabat1`/`sifra`-specifičnih
    kolicina/nabavnaCena/stopnjaDdv - te vrednosti se ne spreminjajo
    (prevzemnica ostane ista, samo dopolnjena), zato se ne pošiljajo v PUT
    telesih.

    Izračuni (LOT, CENA_VNESENA, DATUM_UPORABNOSTI) so nespremenjeni glede na
    prejšnjo verzijo - glej BRIEF.md.
    """
    warnings: list[str] = []
    original_postavke = original.get("postavke") or []
    sifre = sorted({p["sifra"] for p in original_postavke if p.get("sifra")})

    original_datum = datetime.fromisoformat(original["datum"])
    lot = original_datum.strftime("%d%m%y")

    prosto_r5_lookup = _fetch_dodatno_polje_lookup(client, sifre, "PROSTO_R5")
    normativ_lookup = _fetch_normativ_lookup(client, sifre)
    operacije_lookup = _fetch_operacije_lookup(client, sifre)
    prodajne_cene_lookup = _fetch_prodajne_cene_izdelka_lookup(client, sifre)

    sestavina_sifre = sorted(
        {s["artikel"] for sestavine in normativ_lookup.values() for s in sestavine if s.get("artikel")}
    )
    nabavna_cena_lookup = _fetch_dodatno_polje_lookup(client, sestavina_sifre, "NABAVNA_CENA")

    operacija_sifre = sorted(
        {o["artikel"] for operacije in operacije_lookup.values() for o in operacije if o.get("artikel")}
    )
    urna_postavka_lookup = _fetch_prodajna_cena_lookup(client, operacija_sifre)

    postavke_body: list[dict] = []
    lot_zapisi: dict[tuple[str, str], dict] = {}

    for p in original_postavke:
        sifra = p["sifra"]

        prosto_r5_raw = prosto_r5_lookup.get(sifra)
        prosto_r5_dni = int(float(prosto_r5_raw)) if prosto_r5_raw not in (None, "") else 0

        postavka_body: dict[str, Any] = {
            "sifra": sifra,
            "zs": p["zs"],
            "dodatnaPolja": [
                {"naziv": "LOT", "vrednost": lot},
                {"naziv": "INS_DRZAVA_POREKLA", "vrednost": 705},
            ],
        }

        # Artikel je "izdelek" natanko takrat, ko ima lasten normativ (recept) -
        # torej ima normativ_lookup zapis pod ključem = ta šifra (dobljen z
        # Normativ=<sifra> poizvedbo, glej _fetch_normativ_lookup), ne samo
        # kot sestavina (material) v receptu nekega drugega izdelka.
        # CENA_VNESENA se zapiše SAMO za izdelke; za material se cena samo
        # pridobi (nabavna_cena_lookup) za izračun cene nadrejenega izdelka,
        # na lastno postavko materiala pa se CENA_VNESENA ne zapisuje.
        sestavine = normativ_lookup.get(sifra)
        if sestavine:
            # procentKala je pravi odstotek (npr. 12.0 = 12%), zato se deli s 100 -
            # potrjeno na živih podatkih: P03350 ima procentKala=12.0 na edini
            # sestavini (60519), brez deljenja bi to pomenilo 13x nabavno ceno
            # sestavine namesto realnega ~12% kala.
            cena_mat = 0.0
            for sestavina in sestavine:
                sestavina_sifra = sestavina["artikel"]
                nabavna_cena_sestavine = nabavna_cena_lookup.get(sestavina_sifra)
                if not nabavna_cena_sestavine:
                    # živi podatki (2026-07-23): 26/418 sestavin nima NABAVNA_CENA
                    # (None ali 0) - brez opozorila bi se cena_mat za te izdelke
                    # tiho podcenila, ker ta sestavina prispeva 0 k cena_mat.
                    warnings.append(
                        f"Izdelek {sifra}: sestavina {sestavina_sifra} nima NABAVNA_CENA "
                        "(uporabljena vrednost 0 - cena_mat je podcenjena)."
                    )
                cena_mat += (
                    float(nabavna_cena_sestavine or 0.0)
                    * float(sestavina.get("kolicina") or 0.0)
                    * (1 + float(sestavina.get("procentKala") or 0.0) / 100)
                )
            # urna_postavka = FA_ARTIKEL.PRODAJNA_CENA šifre same operacije
            # (npr. P50000 "P strošek za francosko solato" -> prodajnaCena=0.2),
            # ne fiksna konfiguracijska vrednost - potrjeno s strani uporabnika
            # 2026-07-24.
            cena_op = 0.0
            for operacija in operacije_lookup.get(sifra, []):
                operacija_sifra = operacija["artikel"]
                urna_postavka = urna_postavka_lookup.get(operacija_sifra)
                if not urna_postavka:
                    warnings.append(
                        f"Izdelek {sifra}: operacija {operacija_sifra} nima PRODAJNA_CENA "
                        "(uporabljena vrednost 0 - cena_op je podcenjena)."
                    )
                # pripravneUre se namerno NE upošteva pri cena_op (potrjeno
                # s strani uporabnika 2026-07-24) - samo redneUre.
                redne_ure = float(operacija.get("redneUre") or 0.0)
                if redne_ure == 0:
                    warnings.append(
                        f"Izdelek {sifra}: operacija {operacija_sifra} ima redneUre = 0."
                    )
                cena_op += redne_ure * float(urna_postavka or 0.0)
            cena_izdelka = _round_cena(cena_mat + cena_op)
            postavka_body["dodatnaPolja"].append({"naziv": "CENA_VNESENA", "vrednost": cena_izdelka})

            # Primerjava s prodajno ceno - opozorilo (izpisano v GUI z rdečim
            # tekstom, glej prefiks "CENA_OPOZORILO:"), če je CENA_VNESENA
            # (nabavna/proizvodna cena) VIŠJA od najnižje od
            # PRODAJNA_CENA3/PRODAJNA_CENA4 (izdelek bi se prodajal z
            # izgubo) - popravljena smer primerjave, potrjeno s strani
            # uporabnika 2026-07-24 (prejšnja verzija je opozarjala v
            # obratnem, napačnem primeru). `0`/manjkajoča vrednost se šteje
            # za "cena ni nastavljena" (isti dogovor kot pri NABAVNA_CENA) in
            # se izloči iz primerjave - če nobena od obeh ni nastavljena, se
            # primerjava izpusti (ni s čim primerjati).
            cena3, cena4 = prodajne_cene_lookup.get(sifra, (None, None))
            veljavne_prodajne_cene = [float(c) for c in (cena3, cena4) if c not in (None, "") and float(c) != 0]
            if veljavne_prodajne_cene:
                min_prodajna_cena = min(veljavne_prodajne_cene)
                if cena_izdelka > min_prodajna_cena:
                    warnings.append(
                        f"CENA_OPOZORILO: Izdelek {sifra}: CENA_VNESENA ({cena_izdelka}) je VIŠJA "
                        f"od najnižje prodajne cene ({min_prodajna_cena}) - izdelek se prodaja z izgubo."
                    )

        postavke_body.append(postavka_body)

        # DATUM_UPORABNOSTI se zapiše SAMO, če ima artikel PROSTO_R5 >= 1
        # (dan/dni) - PROSTO_R5 == 0 (ali manjkajoč, glej privzeto 0 zgoraj)
        # pomeni "brez roka uporabnosti", zato se zanj lotPodatki zapis
        # (POST/PUT FASifranti/lotPodatki, glej run()) sploh ne pripravi -
        # potrjeno s strani uporabnika 2026-07-27.
        if prosto_r5_dni >= 1:
            datum_uporabnosti = original_datum + timedelta(days=prosto_r5_dni)
            lot_zapisi[(sifra, lot)] = {
                "sifra": sifra,
                "lot": lot,
                "datumUporabnosti": datum_uporabnosti.strftime("%Y-%m-%dT00:00:00"),
            }

    header_body = {
        "stevilka": original["stevilka"],
        "leto": original["leto"],
        "datum": original["datum"],
        "komercialist": "011",
        "skladisce": 11,
        "dodatnaPolja": [
            {"naziv": "INS_DRZAVA", "vrednost": 705},
            {"naziv": "SKLADISCE_ZA_ODPIS", "vrednost": 11},
            {"naziv": "INS_VRSTA_TRANSPORTA", "vrednost": 3},
            {"naziv": "DOKUMENT_DOBAVNICA", "vrednost": f"{dn_stevilka} DN"},
        ],
    }
    postavke_put_body = {
        "stevilka": original["stevilka"],
        "leto": original["leto"],
        "postavke": postavke_body,
    }

    return header_body, postavke_put_body, list(lot_zapisi.values()), warnings


def run() -> dict:
    """Poišče nove DN (FAKTURIRANA=7, SKLADISCE=11) in za vsakega z enim PUT klicem posodobi njegovo prevzemnico.

    Datumski razpon je vedno danes-1..danes (pokrije primer, ko se program
    ne zažene točno vsak dan). Ker znotraj tega razpona številke DN niso
    nujno strogo naraščajoče/brez vrzeli glede na filter, se sledenje že
    obdelanim DN ne opira na "zadnjo številko" (x1), ampak na persistenten
    seznam natančnih ključev (`stevilka.leto`) - glej storage.load_obdelani_dn.

    Za razliko od prejšnjega dvokoračnega procesa (Korak 1: pripravi +
    izpiši seznam za ročni izbris, Korak 2: ročno sproženo kreiranje novih
    prevzemnic) gre zdaj vse v EN korak: obstoječa prevzemnica se s PUT
    klicem dopolni na mestu, brisanje/ponovno ustvarjanje ni več potrebno.
    DN se v `obdelani_dn` doda ŠELE ob uspešni posodobitvi - DN z napako
    ostane zunaj seznama in se samodejno poskusi znova ob naslednjem zagonu
    (PUT je idempotenten, ponovni poskus ne podvoji zapisov).
    """
    client = EVascoClient()
    try:
        client.authenticate()

        today = datetime.now()
        yesterday = today - timedelta(days=1)
        datum_od = yesterday.strftime("%Y-%m-%dT00:00:00")
        datum_do = today.strftime("%Y-%m-%dT00:00:00")

        padding = settings.STEVILKA_PADDING
        lansko_leto = yesterday.year
        letosnje_leto = today.year
        stevilka_od = f"{1:0{padding}d}.{lansko_leto}"
        stevilka_do = f"{'9' * padding}.{letosnje_leto}"

        dn_list = client.get_dn_nalogi(
            datum_od=datum_od,
            datum_do=datum_do,
            stevilka_od=stevilka_od,
            stevilka_do=stevilka_do,
        )

        # TESTNO OBDOBJE (settings.DN_STEVILKA_OD, glej app/config.py): dokler
        # se sistem ročno testira, se DN pod to številko izločijo, da se ne
        # posega v prevzemnice, ki so bile obdelane/preverjene še pred
        # uvedbo tega procesa - primerjava je numerična (`stevilka` je v
        # odgovoru API-ja neblokiran string, npr. "52870", ne zero-padded),
        # ne string primerjava.
        if settings.DN_STEVILKA_OD > 1:
            dn_list = [dn for dn in dn_list if int(dn.get("stevilka") or 0) >= settings.DN_STEVILKA_OD]

        if not dn_list:
            return {
                "status": "ok",
                "message": "Ni novih delovnih nalogov za obdelavo.",
                "results": [],
            }

        obdelani = storage.load_obdelani_dn()

        def dn_key(dn: dict) -> str:
            return f"{dn.get('stevilka')}.{dn.get('leto', letosnje_leto)}"

        novi_dn = [dn for dn in dn_list if dn_key(dn) not in obdelani]

        if not novi_dn:
            return {
                "status": "ok",
                "message": "Ni novih delovnih nalogov za obdelavo.",
                "results": [],
            }

        results: list[dict] = []
        warnings: list[str] = []

        if settings.PUT_PREVZEMNICA_DRY_RUN:
            warnings.append(
                "TESTNO OBDOBJE: PUT /api/v1/FA/prevzemnica še ni objavljen v swaggerju, zato PUT klic ni bil "
                "izveden - prevzemnice spodaj so samo pripravljene/arhivirane, NISO dejansko posodobljene."
            )

        for dn in novi_dn:
            dn_stevilka_raw = dn.get("stevilka")
            dn_leto = dn.get("leto", letosnje_leto)
            pre_stevilka_raw = _get_dodatno_polje(dn.get("dodatnaPolja"), "PRE_STEVILKA")

            if not pre_stevilka_raw:
                warnings.append(f"DN {dn_stevilka_raw}: manjka PRE_STEVILKA, prevzemnice ni bilo mogoče poiskati.")
                results.append({"dn": dn_stevilka_raw, "pre_stevilka": None, "status": "brez_pre_stevilke"})
                continue

            pre_stevilka = str(pre_stevilka_raw).strip()
            stevilka_leto = pre_stevilka if "." in pre_stevilka else f"{pre_stevilka}.{dn_leto}"

            try:
                prevzemnica = client.get_prevzemnica_by_stevilka(stevilka_leto)
            except Exception as exc:  # noqa: BLE001 - surface per-item error, continue with others
                warnings.append(f"DN {dn_stevilka_raw}: napaka pri pridobivanju prevzemnice {pre_stevilka}: {exc}")
                results.append({"dn": dn_stevilka_raw, "pre_stevilka": pre_stevilka, "status": "napaka_prevzemnica", "error": str(exc)})
                continue

            if not prevzemnica:
                warnings.append(f"DN {dn_stevilka_raw}: prevzemnica {pre_stevilka} ni bila najdena.")
                results.append({"dn": dn_stevilka_raw, "pre_stevilka": pre_stevilka, "status": "prevzemnica_ni_najdena"})
                continue

            try:
                header_body, postavke_body, lot_zapisi, cena_warnings = _prepare_prevzemnica_update(
                    client, prevzemnica[0], str(dn_stevilka_raw)
                )
                warnings.extend(cena_warnings)
            except Exception as exc:  # noqa: BLE001 - surface per-item error, continue with others
                warnings.append(
                    f"DN {dn_stevilka_raw}: napaka pri izračunu podatkov za prevzemnico {pre_stevilka}: {exc}"
                )
                results.append({"dn": dn_stevilka_raw, "pre_stevilka": pre_stevilka, "status": "napaka_izracuna", "error": str(exc)})
                continue

            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                # --- LOT / DATUM_UPORABNOSTI: FASifranti/lotPodatki ---
                # Preverba (GET client.get_lot_podatki) pove, ali bi dejanski
                # zapis sprožil POST (nov zapis), PUT (obstoječ zapis, druga
                # vrednost) ali nič (obstoječa vrednost je že enaka) -
                # arhivska datoteka mora vsebovati TO odločitev in TOČNO
                # telo, ki je bilo poslano.
                lot_podatki_nacrt: list[dict[str, Any]] = []
                for zapis in lot_zapisi:
                    obstojeci = client.get_lot_podatki(zapis["sifra"], zapis["lot"])
                    if obstojeci and (obstojeci[0].get("datumUporabnosti") or "")[:10] == zapis["datumUporabnosti"][:10]:
                        akcija = "brez_spremembe"
                    elif obstojeci:
                        akcija = "posodobitev (PUT)"
                    else:
                        akcija = "nov_zapis (POST)"
                    lot_podatki_nacrt.append({"akcija": akcija, "telo": zapis, "obstojeci_zapis": obstojeci})

                    if akcija == "posodobitev (PUT)":
                        client.update_lot_podatki(zapis)
                    elif akcija == "nov_zapis (POST)":
                        client.create_lot_podatki(zapis)

                # --- Posodobitev obstoječe prevzemnice: 2x PUT FA/prevzemnica[/spremembaPostavk] ---
                # TESTNO OBDOBJE (settings.PUT_PREVZEMNICA_DRY_RUN, privzeto
                # True čeprav sta oba PUT-a od 2026-07-27 objavljena v
                # swaggerju - flag se ugasne šele po ročni potrditvi na živih
                # podatkih): dejanska PUT klica se PRESKOČITA - telesi se
                # samo izračunata in arhivirata, da je mogoče natančno
                # preveriti, kaj bi bilo poslano. DN se v tem primeru
                # NAMENOMA ne doda v obdelani_dn (glej spodaj), da se enkrat
                # ko je PUT_PREVZEMNICA_DRY_RUN=False samodejno dejansko
                # obdela, brez ročnega posega.
                if settings.PUT_PREVZEMNICA_DRY_RUN:
                    response_glava = None
                    response_postavke = None
                    status = "pripravljeno"
                else:
                    response_glava = client.update_prevzemnica(header_body)
                    response_postavke = client.update_prevzemnica_postavke(postavke_body)
                    status = "uspesno"

                output_path = storage.output_update_path(pre_stevilka, timestamp)
                output_path.write_text(
                    json.dumps(
                        {
                            "dry_run": settings.PUT_PREVZEMNICA_DRY_RUN,
                            "prevzemnicaGlava": header_body,
                            "prevzemnicaPostavke": postavke_body,
                            "lotPodatki": lot_podatki_nacrt,
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )

                result: dict[str, Any] = {
                    "dn": dn_stevilka_raw,
                    "pre_stevilka": pre_stevilka,
                    "status": status,
                    "izhodna_datoteka": str(output_path),
                }
                if response_glava is not None or response_postavke is not None:
                    result["odgovor"] = {"glava": response_glava, "postavke": response_postavke}
                results.append(result)

                if status == "uspesno":
                    storage.add_obdelani_dn({dn_key(dn)})
            except Exception as exc:  # noqa: BLE001 - collect per-item error, continue with others
                warnings.append(f"DN {dn_stevilka_raw}: napaka pri posodobitvi prevzemnice {pre_stevilka}: {exc}")
                results.append({"dn": dn_stevilka_raw, "pre_stevilka": pre_stevilka, "status": "napaka", "error": str(exc)})

        uspesni_statusi = ("uspesno", "pripravljeno")
        vse_uspesno = all(r["status"] in uspesni_statusi for r in results)
        obdelanih = sum(1 for r in results if r["status"] in uspesni_statusi)

        if settings.PUT_PREVZEMNICA_DRY_RUN:
            sporocilo = f"Najdenih {len(novi_dn)} DN, {obdelanih} prevzemnic pripravljenih (PUT testno preskočen)."
        else:
            sporocilo = f"Najdenih {len(novi_dn)} DN, {obdelanih} prevzemnic uspešno posodobljenih."

        return {
            "status": "ok",
            "message": sporocilo,
            "results": results,
            "warnings": warnings,
            "vse_uspesno": vse_uspesno,
        }
    finally:
        client.close()
