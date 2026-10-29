# Gradnja Windows namestitvenega paketa (.exe) za GUI

Mapa `gui/` (poleg izvorne kode GUI klienta) vsebuje tudi skripte za
pretvorbo `gui/main.py` v samostojen Windows `.exe` in nato v namestitveni
program (`Setup.exe`, namestitveni jezik EN): `gui.spec`, `build_exe.bat`,
`build_installer.bat`, `setup.iss`.

**Verzija aplikacije: 1.1.** Določena je na dveh mestih, ki ju je ob novi
verziji treba uskladiti: `APP_VERSION` v `gui/main.py` (prikazana v naslovu
okna) in `MyAppVersion` v `gui/setup.iss` (verzija namestitvenega programa,
"Apps & features" v Windows).

**Pomembno:** vse spodnje skripte je treba pognati na **Windows** računalniku
- PyInstaller zgradi binarko za operacijski sistem, na katerem teče (ne zna
prečno graditi Windows `.exe` iz Linuxa), enako velja za Inno Setup Compiler.

## Predpogoji (na Windows gradbenem računalniku)

- Python 3.11+ (v PATH)
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) - samo za `build_installer.bat`
  (če želite le `.exe` brez namestitvenega programa, zadošča `build_exe.bat`)

Vse Python odvisnosti GUI-ja (`PySide6`, `httpx`, `python-dotenv`) in
`pyinstaller` sam se namestijo **samodejno** ob zagonu `build_exe.bat`, če
še niso nameščene - `pip install` privzeto ne prenaša/ponovno ne namešča
paketov, ki so že na pravi različici, zato je ponovni zagon hiter in ne
naredi ničesar odveč.

## Uporaba

```bat
cd gui
build_exe.bat
```
Zgradi `dist\Sloncek-Tomato-GUI.exe` (samostojna, onefile binarka -
ne potrebuje ločeno nameščenega Pythona ali pip paketov na ciljnem
računalniku, vse je vgrajeno).

```bat
cd gui
build_installer.bat
```
Najprej pokliče `build_exe.bat`, nato z Inno Setup zgradi
`dist_installer\Sloncek-Tomato-GUI-Setup.exe` - namestitveni program:
- **namestitveni jezik: samo angleščina** (`[Languages]` v `setup.iss` ima en
  sam vnos, zato se dialog za izbiro jezika sploh ne prikaže)
- namesti `.exe` + predlogo `.env.example` (in `.env`, samo če na cilju še ne
  obstaja - ne prepiše obstoječe konfiguracije ob nadgradnji)
- ustvari bližnjico v meniju Start (+ neobvezno na namizju) ter bližnjico za
  urejanje `.env` (poveže se na `API_BASE_URL`/`API_KEY` backenda, glej
  glavni `README.md`, razdelek "Zagon GUI")
- ob koncu namestitve preveri, ali je na sistemu nameščen **Microsoft Visual
  C++ Redistributable (x64)** (runtime odvisnost za PySide6/Qt6 na Windows)
  in ga **samodejno prenese in namesti samo, če manjka** - če je že
  nameščen, se ta korak preskoči.

## Po namestitvi

Uporabnik mora urediti `.env` (bližnjica "Configure connection (.env)" v
meniju Start, ali ročno v namestitveni mapi) in nastaviti:
```
API_BASE_URL=http://<IP-backenda>:8090
API_KEY=<enak API_KEY kot v backend .env>
```
Glej glavni `README.md`, razdelek "Zagon GUI (Qt6, na ločenem računalniku)".
