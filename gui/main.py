import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from PySide6.QtCore import QSize, QThread, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# GUI teče na ločenem računalniku od backenda (Docker, glej
# docker-compose.yml), zato se API_BASE_URL bere iz .env - nastavi ga na
# naslov računalnika, kjer teče backend, npr. http://192.168.x.x:8090
# (glej gui/.env.example in README.md, razdelek "Zagon GUI na ločenem
# računalniku"). API_KEY mora biti enak vrednosti API_KEY v backend .env -
# posreduje se v headerju `X-API-Key` in backend brez njega/z napačnim
# ključem zavrne klic (401).
#
# `Path(__file__)` po PyInstaller zamrznitvi kaže v začasno/vgrajeno mapo
# (sys._MEIPASS), ne v mapo, kjer je dejansko nameščen .exe - zato se .env
# tam iskal ne bi bil najden. `sys.frozen` je standardna PyInstaller zastavica
# za zaznavo zamrznjenega okolja; takrat se .env išče poleg .exe datoteke
# (sys.executable), sicer poleg tega izvorne datoteke (razvojni zagon).
if getattr(sys, "frozen", False):
    _env_dir = Path(sys.executable).resolve().parent
else:
    _env_dir = Path(__file__).resolve().parent
load_dotenv(_env_dir / ".env")
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8090")
API_KEY = os.environ.get("API_KEY", "")

# Ikona aplikacije (gui/pic-sloncek/) - v razvojnem zagonu leži poleg te
# datoteke, v PyInstaller onefile .exe pa se izvleče v začasno mapo
# sys._MEIPASS (glej gui/gui.spec, datas=[...]) - zato NI poleg .exe kot
# .env (glej opombo o _env_dir zgoraj).
if getattr(sys, "frozen", False):
    _resource_dir = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    _resource_dir = Path(__file__).resolve().parent
ICON_PATH = _resource_dir / "pic-sloncek" / "flying_elephant_frame_1.png"
ANIMATION_FRAME_PATHS = [_resource_dir / "pic-sloncek" / f"flying_elephant_frame_{i}.png" for i in range(1, 7)]


class ApiWorker(QThread):
    finished_with_result = Signal(str, dict)

    def __init__(self, endpoint: str) -> None:
        super().__init__()
        self._endpoint = endpoint

    def run(self) -> None:
        try:
            response = httpx.post(
                f"{API_BASE_URL}{self._endpoint}",
                headers={"X-API-Key": API_KEY},
                timeout=120.0,
            )
            response.raise_for_status()
            result = response.json()
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail")
            except ValueError:
                detail = None
            result = {"status": "error", "message": detail or str(exc)}
        except Exception as exc:  # noqa: BLE001 - surface any error to the GUI log
            result = {"status": "error", "message": str(exc)}
        self.finished_with_result.emit(self._endpoint, result)


class MainWindow(QMainWindow):
    BUTTON_TEXT = "Izpolni\nprevzemnice"
    BUTTON_SIZE = QSize(300, 250)
    # Trajanje enega okvirja animacije slončka in premor med posameznimi
    # ponovitvami (3x, 1s premor med njimi - glej _start_elephant_animation).
    ANIM_FRAME_MS = 120
    ANIM_LOOPS = 3
    ANIM_LOOP_PAUSE_MS = 1000

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Slonček")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(700, 500)

        self.obdelaj_button = QPushButton(self.BUTTON_TEXT)
        self.obdelaj_button.setFixedSize(self.BUTTON_SIZE)
        # 1px viden rob (barva sledi trenutni Qt paleti - light/dark) + 8px
        # radij, enak zaokrožitvi robov okna v Windows 11. Samo
        # `border-radius` (brez `border`) na nekaterih stilih/temah ne izriše
        # nobenega vidnega roba, zato je `border` tu eksplicitno naveden.
        self.obdelaj_button.setStyleSheet(
            "QPushButton { text-align: center; border: 1px solid palette(mid); border-radius: 8px; }"
        )
        self.obdelaj_button.setIconSize(self.BUTTON_SIZE - QSize(40, 40))
        self.obdelaj_button.clicked.connect(self.on_obdelaj_clicked)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)

        self.status_label = QLabel("")

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)

        self.clear_log_button = QPushButton("Počisti log")
        self.clear_log_button.clicked.connect(self.on_clear_log_clicked)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.obdelaj_button)
        button_row.addStretch(1)

        # Slikovni okvirji za animacijo slončka, prikazano V GUMBU (namesto
        # besedila) po koncu obdelave - naložijo se enkrat vnaprej (ne ob
        # vsakem predvajanju), skalirani na velikost ikone gumba. Manjkajoča/
        # pokvarjena datoteka (QPixmap.isNull()) se izloči, da animacija ne
        # pade, če kakšen okvir manjka.
        icon_size = self.obdelaj_button.iconSize()
        self._anim_frames = [
            pixmap
            for path in ANIMATION_FRAME_PATHS
            for pixmap in [
                QPixmap(str(path)).scaled(icon_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            ]
            if not pixmap.isNull()
        ]
        self._anim_frame_index = 0
        self._anim_loop_count = 0
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._on_animation_tick)

        layout = QVBoxLayout()
        layout.addLayout(button_row)
        layout.addWidget(self.progress)
        layout.addWidget(self.status_label)
        layout.addWidget(self.log_view)
        layout.addWidget(self.clear_log_button)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.worker: ApiWorker | None = None

    def on_clear_log_clicked(self) -> None:
        self.log_view.clear()

    def append_log(self, text: str, color: str | None = None) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {text}"
        if color is None:
            self.log_view.appendPlainText(line)
            return

        # Za obarvan tekst (npr. rdeča opozorila cena_vnesena < prodajna
        # cena) gremo mimo appendPlainText (ki ne podpira formatiranja) in
        # vstavimo besedilo z QTextCharFormat preko kurzorja - QPlainTextEdit
        # to podpira, ker interno vseeno uporablja QTextDocument.
        cursor = self.log_view.textCursor()
        cursor.movePosition(QTextCursor.End)
        if not self.log_view.document().isEmpty():
            cursor.insertBlock()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(line, fmt)
        self.log_view.setTextCursor(cursor)
        self.log_view.ensureCursorVisible()

    def _set_busy(self, busy: bool) -> None:
        self.obdelaj_button.setEnabled(not busy)
        self.progress.setVisible(busy)

    def _start_elephant_animation(self) -> None:
        """Po končani obdelavi 3x predvaja animacijo slončka V GUMBU (namesto besedila), s 1s premorom med ponovitvami."""
        if not self._anim_frames:
            return
        self._anim_loop_count = 0
        self._anim_frame_index = 0
        self.obdelaj_button.setEnabled(False)
        self.obdelaj_button.setText("")
        self.obdelaj_button.setIcon(QIcon(self._anim_frames[0]))
        self._anim_timer.start(self.ANIM_FRAME_MS)

    def _on_animation_tick(self) -> None:
        self._anim_frame_index += 1
        if self._anim_frame_index >= len(self._anim_frames):
            self._anim_timer.stop()
            self._anim_loop_count += 1
            if self._anim_loop_count >= self.ANIM_LOOPS:
                self._finish_elephant_animation()
                return
            QTimer.singleShot(self.ANIM_LOOP_PAUSE_MS, self._start_next_animation_loop)
            return
        self.obdelaj_button.setIcon(QIcon(self._anim_frames[self._anim_frame_index]))

    def _start_next_animation_loop(self) -> None:
        self._anim_frame_index = 0
        self.obdelaj_button.setIcon(QIcon(self._anim_frames[0]))
        self._anim_timer.start(self.ANIM_FRAME_MS)

    def _finish_elephant_animation(self) -> None:
        self.obdelaj_button.setIcon(QIcon())
        self.obdelaj_button.setText(self.BUTTON_TEXT)
        self.obdelaj_button.setEnabled(True)

    def on_obdelaj_clicked(self) -> None:
        self._set_busy(True)
        self.status_label.setText("Obdelava v teku...")
        self.append_log(f"POST {API_BASE_URL}/api/obdelaj")

        self.worker = ApiWorker("/api/obdelaj")
        self.worker.finished_with_result.connect(self.on_finished)
        self.worker.start()

    def on_finished(self, endpoint: str, result: dict) -> None:
        self._set_busy(False)

        if result.get("status") not in ("ok",):
            self.status_label.setText("Napaka.")
            message = result.get("message", "Neznana napaka")
            self.append_log("NAPAKA: " + message)
            QMessageBox.critical(self, "Napaka", message)
            self._start_elephant_animation()
            return

        self._handle_obdelaj_result(result)
        self._start_elephant_animation()

    def _handle_obdelaj_result(self, result: dict) -> None:
        self.status_label.setText("Obdelava zaključena.")
        self.append_log("OK: " + result.get("message", ""))

        for warning in result.get("warnings", []):
            if warning.startswith("CENA_OPOZORILO:"):
                self.append_log("OPOZORILO: " + warning, color="red")
            else:
                self.append_log("OPOZORILO: " + warning)

        lines = []
        for r in result.get("results", []):
            if r.get("status") == "uspesno":
                lines.append(f"{r['pre_stevilka']} -> posodobljeno (izhodna datoteka: {r.get('izhodna_datoteka', '')})")
            elif r.get("status") == "pripravljeno":
                lines.append(
                    f"{r['pre_stevilka']} -> pripravljeno, PUT testno preskočen "
                    f"(izhodna datoteka: {r.get('izhodna_datoteka', '')})"
                )
            else:
                lines.append(f"{r.get('pre_stevilka') or r.get('dn')}: NAPAKA/PRESKOČENO - {r.get('error', r.get('status', ''))}")

        for line in lines:
            self.append_log(line)

        if not lines:
            QMessageBox.information(self, "Obdelaj", result.get("message", "Ni podatkov za obdelavo."))
            return

        prefix = "" if result.get("vse_uspesno") else "Nekatere prevzemnice niso bile uspešno posodobljene:\n\n"
        QMessageBox.information(self, "Obdelaj - rezultat", prefix + "\n".join(lines))


def main() -> None:
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
