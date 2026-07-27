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


def _fetch_ddv_lookup(client: EVascoClient, sifre: list[str]) -> dict[str, int]:
    """stopnjaDdv ni v GET prevzemnica odgovoru; pridobi ga iz šifranta artiklov.

    NOTE: prvotno je bilo predvideno, da se stopnjaDdv vzame iz postavk
    povezanega DN, vendar se je pri testiranju izkazalo, da DN.postavke in
    prevzemnica.postavke nimajo skupnih šifer (DN = naročeni artikli,
    prevzemnica = dejansko prejeti artikli) - zato se stopnjaDdv pridobi
    neposredno iz /api/v1/FASifranti/artikel (polje stopnjaDDV).
    """
    if not sifre:
        return {}
    artikli = client.get_artikli(sifre)
    return {a["sifra"]: a.get("stopnjaDDV") for a in artikli if a.get("sifra") is not None}


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
    _prepare_new_prevzemnica).
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


def _prepare_new_prevzemnica(
    client: EVascoClient, dn_stevilka: str, original: dict
) -> tuple[dict, list[dict], list[str]]:
    """Sestavi telo nove prevzemnice (Korak 2 POST body) + seznam lotPodatki zapisov + opozorila.

    Vsi GET klici (artikel/normativ/normativOperacije, stopnjaDdv) se
    izvedejo tukaj, v Koraku 1 - Korak 2 nato samo vzame že pripravljeno
    telo iz seje in izvede zapise (glej BRIEF.md: LOT/DATUM_UPORABNOSTI/
    CENA_VNESENA morajo biti znani vnaprej, POST/PUT proti eVasco pa je
    ločeno, čisto pisalno dejanje).

    `dn_stevilka` je številka DELOVNEGA NALOGA (ne prevzemnice/dobavnice) -
    uporabi se v DOKUMENT_DOBAVNICA (glej spodaj). Potrjeno na živih podatkih
    (prevzemnica 00604.2026, ki jo je ustvaril zunanji proces): tam ima
    DOKUMENT_DOBAVNICA vrednost "50277 dn", kjer je 50277 DN številka, ne
    00604 (številka same prevzemnice) - napačna zamenjava teh dveh je bila
    najdena in popravljena 2026-07-24.
    """
    warnings: list[str] = []
    original_postavke = original.get("postavke") or []
    sifre = sorted({p["sifra"] for p in original_postavke if p.get("sifra")})

    original_datum = datetime.fromisoformat(original["datum"])
    lot = original_datum.strftime("%d%m%y")

    ddv_lookup = _fetch_ddv_lookup(client, sifre)
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
        datum_uporabnosti = original_datum + timedelta(days=prosto_r5_dni)

        postavka_body: dict[str, Any] = {
            "sifra": sifra,
            "kolicina": p["kolicina"],
            "nabavnaCena": p["cena"],
            "dodatnaPolja": [{"naziv": "LOT", "vrednost": lot}],
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

        stopnja_ddv = ddv_lookup.get(sifra)
        if stopnja_ddv is not None:
            postavka_body["stopnjaDdv"] = stopnja_ddv
        postavke_body.append(postavka_body)

        lot_zapisi[(sifra, lot)] = {
            "sifra": sifra,
            "lot": lot,
            "datumUporabnosti": datum_uporabnosti.strftime("%Y-%m-%dT00:00:00"),
        }

    body = {
        "datum": original["datum"],
        "dobavitelj": original["dobavitelj"],
        "komercialist": "011",
        "skladisce": 11,
        "rabat1": 0.0,
        "dodatnaPolja": [
            {"naziv": "INS_DRZAVA", "vrednost": 705},
            {"naziv": "INS_DRZAVA_POREKLA", "vrednost": 705},
            {"naziv": "SKLADISCE_ZA_ODPIS", "vrednost": 11},
            {"naziv": "INS_VRSTA_TRANSPORTA", "vrednost": 3},
            {"naziv": "DOKUMENT_DOBAVNICA", "vrednost": f"{dn_stevilka} DN"},
        ],
        "postavke": postavke_body,
    }

    return body, list(lot_zapisi.values()), warnings


def run_step1() -> dict:
    """Korak 1: poišče nove DN (FAKTURIRANA=7, SKLADISCE=11) in njihove prevzemnice.

    Datumski razpon je vedno danes-1..danes (pokrije primer, ko se program
    ne zažene točno vsak dan). Ker znotraj tega razpona številke DN niso
    nujno strogo naraščajoče/brez vrzeli glede na filter, se sledenje že
    obdelanim DN ne opira na "zadnjo številko" (x1), ampak na persistenten
    seznam natančnih ključev (`stevilka.leto`) - glej storage.load_obdelani_dn.

    Za vsak DN se v tem koraku takoj izračuna tudi celotno telo nove
    prevzemnice (LOT, DATUM_UPORABNOSTI, CENA_VNESENA - vsi GET klici), da
    Korak 2 samo še izvede pisalne klice nad že pripravljenimi podatki.
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

        if not dn_list:
            return {
                "status": "ok",
                "message": "Ni novih delovnih nalogov za obdelavo.",
                "prevzemnice_za_izbris": [],
            }

        obdelani = storage.load_obdelani_dn()

        def dn_key(dn: dict) -> str:
            return f"{dn.get('stevilka')}.{dn.get('leto', letosnje_leto)}"

        novi_dn = [dn for dn in dn_list if dn_key(dn) not in obdelani]

        if not novi_dn:
            return {
                "status": "ok",
                "message": "Ni novih delovnih nalogov za obdelavo.",
                "prevzemnice_za_izbris": [],
            }

        items: list[dict] = []
        prevzemnice_za_izbris: list[str] = []
        warnings: list[str] = []
        processed_keys: set[str] = set()

        for dn in novi_dn:
            dn_stevilka_raw = dn.get("stevilka")
            processed_keys.add(dn_key(dn))

            dn_leto = dn.get("leto", letosnje_leto)
            pre_stevilka_raw = _get_dodatno_polje(dn.get("dodatnaPolja"), "PRE_STEVILKA")

            if not pre_stevilka_raw:
                warnings.append(f"DN {dn_stevilka_raw}: manjka PRE_STEVILKA, prevzemnice ni bilo mogoče poiskati.")
                items.append(
                    {
                        "dn": dn,
                        "pre_stevilka": None,
                        "prevzemnica": None,
                        "status": "brez_pre_stevilke",
                    }
                )
                continue

            pre_stevilka = str(pre_stevilka_raw).strip()
            stevilka_leto = pre_stevilka if "." in pre_stevilka else f"{pre_stevilka}.{dn_leto}"

            try:
                prevzemnica = client.get_prevzemnica_by_stevilka(stevilka_leto)
            except Exception as exc:  # noqa: BLE001 - surface per-item error, continue with others
                warnings.append(f"DN {dn_stevilka_raw}: napaka pri pridobivanju prevzemnice {pre_stevilka}: {exc}")
                items.append(
                    {
                        "dn": dn,
                        "pre_stevilka": pre_stevilka,
                        "prevzemnica": None,
                        "status": "napaka_prevzemnica",
                    }
                )
                continue

            if not prevzemnica:
                warnings.append(f"DN {dn_stevilka_raw}: prevzemnica {pre_stevilka} ni bila najdena.")
                items.append(
                    {
                        "dn": dn,
                        "pre_stevilka": pre_stevilka,
                        "prevzemnica": None,
                        "status": "prevzemnica_ni_najdena",
                    }
                )
                continue

            try:
                novo_telo, lot_zapisi, cena_warnings = _prepare_new_prevzemnica(
                    client, str(dn_stevilka_raw), prevzemnica[0]
                )
                warnings.extend(cena_warnings)
            except Exception as exc:  # noqa: BLE001 - surface per-item error, continue with others
                warnings.append(
                    f"DN {dn_stevilka_raw}: napaka pri izračunu podatkov za prevzemnico {pre_stevilka}: {exc}"
                )
                items.append(
                    {
                        "dn": dn,
                        "pre_stevilka": pre_stevilka,
                        "prevzemnica": prevzemnica,
                        "status": "napaka_izracuna",
                    }
                )
                continue

            items.append(
                {
                    "dn": dn,
                    "pre_stevilka": pre_stevilka,
                    "prevzemnica": prevzemnica,
                    "novo_telo": novo_telo,
                    "lot_zapisi": lot_zapisi,
                    "status": "pripravljeno",
                }
            )
            prevzemnice_za_izbris.append(pre_stevilka)

        session = {
            "session_id": storage.new_session_id(),
            "created_at": datetime.now().isoformat(),
            "status": storage.STATUS_WAITING,
            "leto": letosnje_leto,
            "items": items,
            "warnings": warnings,
        }
        storage.save_session(session)

        # Posodobi seznam obdelanih DN takoj po shranjevanju podatkov - ne
        # šele po Koraku 2 - da se prepreči ponovna obdelava istih DN ob
        # naslednjem zagonu Koraka 1, tudi če Korak 2 še ni bil izveden.
        storage.add_obdelani_dn(processed_keys)

        return {
            "status": "ok",
            "message": f"Najdenih {len(novi_dn)} DN, {len(prevzemnice_za_izbris)} prevzemnic za izbris.",
            "prevzemnice_za_izbris": prevzemnice_za_izbris,
            "warnings": warnings,
            "session_id": session["session_id"],
        }
    finally:
        client.close()


def run_step2() -> dict:
    """Korak 2: za vsako pripravljeno prevzemnico iz zadnje cakajoce_korak2 seje izvede zapise.

    Telo nove prevzemnice in lotPodatki zapisi so v celoti pripravljeni že v
    Koraku 1 (glej _prepare_new_prevzemnica) - Korak 2 samo izvede pisalne
    klice proti eVasco.

    PRODUKCIJA (od 2026-07-24): pisalna klica proti eVasco
    (client.create_lot_podatki/update_lot_podatki in
    client.create_prevzemnica) sta AKTIVNA - dejansko ustvarita/posodobijo
    zapise v produkcijskem eVasco. Pred vsakim lotPodatki zapisom se z
    client.get_lot_podatki preveri obstoječe stanje (glej lot_podatki_nacrt),
    da se izbere POST/PUT/brez sprememb. Pripravljeni podatki (vključno s to
    odločitvijo in dejanskim odgovorom create_prevzemnica) se še vedno
    zapišejo v izhodne JSON datoteke (data/izhodni_zahtevki/) - to služi kot
    arhiv in sledljivost delovanja aplikacije.

    Idempotentno: postavke že označene kot "uspesno" se ne ponovijo, tako da
    je mogoče Korak 2 varno ponovno sprožiti po delnem neuspehu.
    """
    session = storage.find_latest_session_with_status(storage.STATUS_WAITING)
    if session is None:
        return {
            "status": "error",
            "message": "Ni seje s statusom 'čakajoče korak 2'. Najprej zaženite Korak 1.",
        }

    client = EVascoClient()
    client.authenticate()

    results: list[dict] = []
    all_success = True

    try:
        for item in session["items"]:
            if item.get("status") != "pripravljeno":
                continue  # ni prevzemnice za ta DN (manjka PRE_STEVILKA ali napaka v Koraku 1)

            pre_stevilka = item["pre_stevilka"]

            if item.get("korak2_status") == "uspesno":
                results.append(
                    {
                        "pre_stevilka": pre_stevilka,
                        "status": "uspesno (predhodno)",
                        **item.get("korak2_result", {}),
                    }
                )
                continue

            body = item.get("novo_telo")
            lot_zapisi = item.get("lot_zapisi") or []
            if not body:
                all_success = False
                item["korak2_status"] = "napaka"
                item["korak2_error"] = "Manjka pripravljeno telo iz Koraka 1."
                results.append({"pre_stevilka": pre_stevilka, "status": "napaka", "error": item["korak2_error"]})
                continue

            try:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

                # --- LOT / DATUM_UPORABNOSTI: FASifranti/lotPodatki ---
                # Preverba (GET client.get_lot_podatki) je namerno ŽIVA, ne
                # zakomentirana - bralni klic je varen in pove, ali bi dejanski
                # zapis (spodaj še zakomentiran) sprožil POST (nov zapis), PUT
                # (obstoječ zapis, druga vrednost) ali nič (obstoječa vrednost
                # je že enaka) - arhivska datoteka mora vsebovati TO odločitev
                # in TOČNO telo, ki bi bilo poslano, da je mogoče preveriti
                # pravilnost pred odklepom dejanskih pisalnih klicev.
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

                # --- Nova prevzemnica: FA/prevzemnica ---
                response = client.create_prevzemnica(body)

                # DATUM_UPORABNOSTI ni veljavno dodatno polje na postavki prevzemnice
                # (potrjeno na živih podatkih 2026-07-24 - odsoten iz
                # /api/v1/FA/prevzemnica/postavka/polja) - zapiše se izključno v
                # FA_LOT_PODATKI preko ločenega lotPodatki klica, zato mora biti v
                # arhivski datoteki eksplicitno viden poleg telesa prevzemnice, sicer
                # v arhivu ni sledi o datumu uporabnosti.
                output_path = storage.output_request_path(pre_stevilka, timestamp)
                output_path.write_text(
                    json.dumps({"prevzemnica": body, "lotPodatki": lot_podatki_nacrt}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                korak2_result: dict[str, Any] = {
                    "izhodna_datoteka": str(output_path),
                }
                if response is not None:
                    korak2_result["nova_stevilka"] = response.get("stevilka")
                    korak2_result["leto"] = response.get("leto")

                item["korak2_status"] = "uspesno"
                item["korak2_result"] = korak2_result
                results.append({"pre_stevilka": pre_stevilka, "status": "uspesno", **korak2_result})
            except Exception as exc:  # noqa: BLE001 - collect per-item error, continue with others
                all_success = False
                item["korak2_status"] = "napaka"
                item["korak2_error"] = str(exc)
                results.append({"pre_stevilka": pre_stevilka, "status": "napaka", "error": str(exc)})

        if all_success:
            session["status"] = storage.STATUS_DONE
        storage.save_session(session)

        return {
            "status": "ok",
            "session_id": session["session_id"],
            "results": results,
            "vse_uspesno": all_success,
        }
    finally:
        client.close()
