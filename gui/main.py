import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
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
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("DN-PREVZEMNICA-Tomato")
        self.resize(700, 500)

        self.step1_button = QPushButton("Korak 1 - Poišči DN in prevzemnice")
        self.step1_button.clicked.connect(self.on_step1_clicked)

        self.step2_button = QPushButton("Korak 2 - Kreiraj nove prevzemnice")
        self.step2_button.clicked.connect(self.on_step2_clicked)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)

        self.status_label = QLabel("")

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)

        self.clear_log_button = QPushButton("Počisti log")
        self.clear_log_button.clicked.connect(self.on_clear_log_clicked)

        button_row = QHBoxLayout()
        button_row.addWidget(self.step1_button)
        button_row.addWidget(self.step2_button)

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
        self.step1_button.setEnabled(not busy)
        self.step2_button.setEnabled(not busy)
        self.progress.setVisible(busy)

    def on_step1_clicked(self) -> None:
        self._set_busy(True)
        self.status_label.setText("Korak 1 v teku...")
        self.append_log(f"POST {API_BASE_URL}/api/step1")

        self.worker = ApiWorker("/api/step1")
        self.worker.finished_with_result.connect(self.on_finished)
        self.worker.start()

    def on_step2_clicked(self) -> None:
        self._set_busy(True)
        self.status_label.setText("Korak 2 v teku...")
        self.append_log(f"POST {API_BASE_URL}/api/step2")

        self.worker = ApiWorker("/api/step2")
        self.worker.finished_with_result.connect(self.on_finished)
        self.worker.start()

    def on_finished(self, endpoint: str, result: dict) -> None:
        self._set_busy(False)

        if result.get("status") not in ("ok",):
            self.status_label.setText("Napaka.")
            message = result.get("message", "Neznana napaka")
            self.append_log("NAPAKA: " + message)
            QMessageBox.critical(self, "Napaka", message)
            return

        if endpoint == "/api/step1":
            self._handle_step1_result(result)
        else:
            self._handle_step2_result(result)

    def _handle_step1_result(self, result: dict) -> None:
        self.status_label.setText("Korak 1 zaključen.")
        self.append_log("OK: " + result.get("message", ""))

        for warning in result.get("warnings", []):
            if warning.startswith("CENA_OPOZORILO:"):
                self.append_log("OPOZORILO: " + warning, color="red")
            else:
                self.append_log("OPOZORILO: " + warning)

        prevzemnice = result.get("prevzemnice_za_izbris", [])
        if not prevzemnice:
            QMessageBox.information(
                self, "Korak 1", result.get("message", "Ni novih delovnih nalogov za obdelavo.")
            )
            return

        seznam = "\n".join(prevzemnice)
        QMessageBox.information(
            self,
            "Korak 1 - izbrišite prevzemnice",
            f"Izbrišite naslednje prevzemnice v eVasco sistemu:\n\n{seznam}\n\n"
            "Ko jih izbrišete, sprožite Korak 2.",
        )

    def _handle_step2_result(self, result: dict) -> None:
        self.status_label.setText("Korak 2 zaključen.")

        lines = []
        for r in result.get("results", []):
            if r.get("status", "").startswith("uspesno"):
                if r.get("nova_stevilka"):
                    lines.append(f"{r['pre_stevilka']} -> nova prevzemnica {r['nova_stevilka']}/{r.get('leto')}")
                else:
                    lines.append(f"{r['pre_stevilka']} -> obdelano (izhodna datoteka: {r.get('izhodna_datoteka', '')})")
            else:
                lines.append(f"{r['pre_stevilka']}: NAPAKA - {r.get('error', '')}")

        for line in lines:
            self.append_log(line)

        if not lines:
            QMessageBox.information(self, "Korak 2", "Ni podatkov za obdelavo.")
            return

        prefix = "" if result.get("vse_uspesno") else "Nekatere prevzemnice niso bile uspešno obdelane:\n\n"
        QMessageBox.information(self, "Korak 2 - rezultat", prefix + "\n".join(lines))


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
