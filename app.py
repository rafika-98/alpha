"""Application PySide6 pour analyse et téléchargement YouTube."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
)

import yt_dlp


def ensure_download_dir(path: Path) -> Path:
    """Ensure the download directory exists."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def format_filesize(value: Optional[int]) -> str:
    """Return a human-readable representation of the filesize."""
    if not value or value <= 0:
        return "?"
    units = ["o", "Ko", "Mo", "Go", "To"]
    size = float(value)
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    return f"{size:.1f} {units[unit_index]}"


class YTDLPProbeWorker(QThread):
    """Worker chargé d'analyser une URL YouTube."""

    formats_ready: Signal = Signal(list)
    log: Signal = Signal(str)
    error: Signal = Signal(str)
    finished: Signal = Signal()

    def __init__(self, url: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._url = url

    def run(self) -> None:
        try:
            self.log.emit("Analyse en cours…")
            options = {
                "quiet": True,
                "skip_download": True,
                "noplaylist": True,
                "nocheckcertificate": True,
                "ignoreerrors": False,
                "dump_single_json": True,
            }
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(self._url, download=False)
            formats: List[Dict[str, Any]] = info.get("formats", []) if isinstance(info, dict) else []
            self.formats_ready.emit(formats)
            self.log.emit(f"Formats trouvés : {len(formats)}")
        except Exception as exc:  # noqa: BLE001
            message = f"Erreur pendant l'analyse : {exc}"
            self.error.emit(message)
            self.log.emit(message)
        finally:
            self.finished.emit()


class YTDLPDownloadWorker(QThread):
    """Worker chargé du téléchargement d'un format spécifique."""

    log: Signal = Signal(str)
    progress: Signal = Signal(int)
    finished: Signal = Signal(bool, object, object)

    def __init__(self, url: str, itag: str, download_dir: Path, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._url = url
        self._itag = itag
        self._download_dir = download_dir
        self._final_path: Optional[Path] = None

    def _progress_hook(self, data: Dict[str, Any]) -> None:
        status = data.get("status")
        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes")
            if total and downloaded:
                percent = int(downloaded * 100 / total)
                self.progress.emit(percent)
            speed = data.get("_speed_str")
            eta = data.get("_eta_str")
            text = "Téléchargement en cours"
            if speed:
                text += f" - vitesse {speed}"
            if eta:
                text += f" - reste {eta}"
            self.log.emit(text)
        elif status == "finished":
            filename = data.get("filename")
            if filename:
                path = Path(filename)
                self._final_path = Path("./downloads") / path.name
            self.log.emit("Traitement final…")

    def run(self) -> None:
        try:
            self.log.emit("Préparation du téléchargement…")
            ensure_download_dir(self._download_dir)
            outtmpl = str(self._download_dir / "%(title)s.%(ext)s")
            options = {
                "quiet": True,
                "format": self._itag,
                "outtmpl": outtmpl,
                "noplaylist": True,
                "nocheckcertificate": True,
                "progress_hooks": [self._progress_hook],
            }
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([self._url])
            final_path = self._final_path
            if final_path is None:
                final_path = Path(outtmpl.replace("%(title)s", "video").replace("%(ext)s", ""))
            self.finished.emit(True, str(final_path), None)
            self.log.emit("Téléchargement terminé.")
        except Exception as exc:  # noqa: BLE001
            message = f"Erreur de téléchargement : {exc}"
            self.finished.emit(False, None, message)
            self.log.emit(message)


class YouTubeTab(QWidget):
    """Onglet dédié aux fonctionnalités YouTube."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.selected_itag: Optional[str] = None
        self.current_url: str = ""
        self.formats_data: List[Dict[str, Any]] = []
        self.probe_worker: Optional[YTDLPProbeWorker] = None
        self.download_worker: Optional[YTDLPDownloadWorker] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        input_layout = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("URL YouTube")
        self.analyze_button = QPushButton("Analyser")
        self.analyze_button.clicked.connect(self._on_analyze_clicked)
        input_layout.addWidget(self.url_input)
        input_layout.addWidget(self.analyze_button)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "✓",
            "itag",
            "ext",
            "résolution",
            "fps",
            "vcodec",
            "acodec",
            "taille",
            "note",
        ])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.itemDoubleClicked.connect(self._on_table_double_clicked)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)

        self.download_button = QPushButton("Télécharger")
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self._on_download_clicked)

        layout.addLayout(input_layout)
        layout.addWidget(self.table)
        layout.addWidget(self.console)
        layout.addWidget(self.download_button)

    def append_log(self, message: str) -> None:
        """Append a message to the console."""
        self.console.appendPlainText(message)
        self.console.verticalScrollBar().setValue(self.console.verticalScrollBar().maximum())

    def _on_analyze_clicked(self) -> None:
        url = self.url_input.text().strip()
        if not url:
            self.append_log("URL manquante.")
            return
        self._reset_selection()
        self.append_log("Lancement de l'analyse…")
        self.analyze_button.setEnabled(False)
        self.download_button.setEnabled(False)
        self.table.setRowCount(0)
        self.formats_data.clear()
        self.current_url = url
        self.probe_worker = YTDLPProbeWorker(url)
        self.probe_worker.log.connect(self.append_log)
        self.probe_worker.error.connect(self.append_log)
        self.probe_worker.formats_ready.connect(self._populate_formats)
        self.probe_worker.finished.connect(self._on_probe_finished)
        self.probe_worker.start()

    def _on_probe_finished(self) -> None:
        self.analyze_button.setEnabled(True)
        self.probe_worker = None

    def _populate_formats(self, formats: List[Dict[str, Any]]) -> None:
        self.formats_data = formats
        self.table.setRowCount(len(formats))
        for row, data in enumerate(formats):
            fields = {
                "itag": str(data.get("format_id", "")),
                "ext": data.get("ext", ""),
                "résolution": f"{data.get('width', '')}x{data.get('height', '')}" if data.get("width") and data.get("height") else data.get("resolution", ""),
                "fps": str(data.get("fps", "")),
                "vcodec": data.get("vcodec", ""),
                "acodec": data.get("acodec", ""),
                "taille": format_filesize(data.get("filesize") or data.get("filesize_approx")),
                "note": data.get("format_note", ""),
            }
            check_item = QTableWidgetItem()
            check_item.setData(Qt.ItemDataRole.DecorationRole, QIcon())
            check_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, check_item)
            for col, key in enumerate(["itag", "ext", "résolution", "fps", "vcodec", "acodec", "taille", "note"], start=1):
                item = QTableWidgetItem(fields[key])
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(row, col, item)
        self.table.resizeColumnsToContents()
        if self.table.columnCount() > 0:
            self.table.setColumnWidth(0, 30)
        self.append_log("Double-cliquez sur un format pour le sélectionner.")

    def _on_table_double_clicked(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if row < 0 or row >= len(self.formats_data):
            return
        format_id = self.formats_data[row].get("format_id")
        if not format_id:
            self.append_log("Format invalide sélectionné.")
            return
        self.selected_itag = str(format_id)
        self._update_checkmarks(row)
        self.append_log(f"Format sélectionné : itag {self.selected_itag}.")
        self.download_button.setEnabled(True)

    def _update_checkmarks(self, selected_row: int) -> None:
        check_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if not item:
                continue
            if row == selected_row:
                item.setData(Qt.ItemDataRole.DecorationRole, check_icon)
            else:
                item.setData(Qt.ItemDataRole.DecorationRole, QIcon())

    def _reset_selection(self) -> None:
        self.selected_itag = None
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setData(Qt.ItemDataRole.DecorationRole, QIcon())

    def _on_download_clicked(self) -> None:
        if not self.current_url or not self.selected_itag:
            self.append_log("Sélectionnez un format avant de télécharger.")
            return
        if self.download_worker is not None and self.download_worker.isRunning():
            self.append_log("Téléchargement déjà en cours.")
            return
        download_dir = ensure_download_dir(Path("./downloads"))
        self.download_button.setEnabled(False)
        self.append_log("Début du téléchargement…")
        self.download_worker = YTDLPDownloadWorker(self.current_url, self.selected_itag, download_dir)
        self.download_worker.log.connect(self.append_log)
        self.download_worker.progress.connect(self._on_download_progress)
        self.download_worker.finished.connect(self._on_download_finished)
        self.download_worker.start()

    def _on_download_progress(self, percent: int) -> None:
        self.append_log(f"Progression : {percent}%")

    def _on_download_finished(self, success: bool, path: Optional[str], error: Optional[str]) -> None:
        if success:
            if path:
                self.append_log(f"Téléchargement terminé : {path}")
            else:
                self.append_log("Téléchargement terminé.")
        else:
            if error:
                self.append_log(error)
            else:
                self.append_log("Téléchargement échoué.")
        self.download_button.setEnabled(bool(self.selected_itag))
        self.download_worker = None


class MainWindow(QMainWindow):
    """Fenêtre principale de l'application."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("app")
        self.resize(900, 600)
        self._setup_ui()

    def _setup_ui(self) -> None:
        tabs = QTabWidget()
        tabs.addTab(YouTubeTab(), "YouTube")
        tabs.addTab(self._placeholder_tab("TikTok"), "TikTok")
        tabs.addTab(self._placeholder_tab("Transcription"), "Transcription")
        tabs.addTab(self._placeholder_tab("Serveur"), "Serveur")
        tabs.addTab(self._placeholder_tab("Paramètres"), "Paramètres")
        self.setCentralWidget(tabs)

    @staticmethod
    def _placeholder_tab(name: str) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel(f"Onglet {name} en construction."))
        layout.addStretch()
        return widget


def main() -> None:
    """Point d'entrée de l'application."""
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
