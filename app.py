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
    QPushButton,
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
    QCheckBox,
    QProgressBar,
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
    """Worker chargé du téléchargement séquentiel vidéo/audio."""

    progress: Signal = Signal(int)
    status: Signal = Signal(str)
    finished: Signal = Signal(bool, object, object)

    def __init__(
        self,
        url: str,
        itag: str,
        download_dir: Path,
        download_video: bool,
        download_audio: bool,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._url = url
        self._itag = itag
        self._download_dir = download_dir
        self._download_video = download_video
        self._download_audio = download_audio

    def _emit_progress(self, value: int) -> None:
        self.progress.emit(max(0, min(100, value)))

    def run(self) -> None:
        success = True
        error_message: Optional[str] = None
        try:
            out_dir = ensure_download_dir(self._download_dir)

            def hook(data: Dict[str, Any]) -> None:
                if data.get("status") == "downloading":
                    total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                    done = data.get("downloaded_bytes", 0)
                    pct = int(done * 100 / total) if total else 0
                    self.progress.emit(pct)
                elif data.get("status") == "finished":
                    self.progress.emit(100)

            if self._download_video:
                self._emit_progress(0)
                self.status.emit("Téléchargement vidéo…")
                ydl_opts_mp4 = {
                    "format": f"{self._itag}",
                    "merge_output_format": "mp4",
                    "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
                    "progress_hooks": [hook],
                    "quiet": True,
                    "no_warnings": True,
                }
                with yt_dlp.YoutubeDL(ydl_opts_mp4) as ydl:
                    ydl.extract_info(self._url, download=True)
                self.progress.emit(100)

            if success and self._download_audio:
                self._emit_progress(0)
                self.status.emit("Extraction audio…")
                ydl_opts_mp3 = {
                    "format": "bestaudio/best",
                    "outtmpl": str(out_dir / "%(title)s.%(ext)s"),
                    "postprocessors": [
                        {
                            "key": "FFmpegExtractAudio",
                            "preferredcodec": "mp3",
                            "preferredquality": "192",
                        }
                    ],
                    "progress_hooks": [hook],
                    "quiet": True,
                    "no_warnings": True,
                }
                with yt_dlp.YoutubeDL(ydl_opts_mp3) as ydl:
                    ydl.extract_info(self._url, download=True)
                self.progress.emit(100)

        except Exception as exc:  # noqa: BLE001
            success = False
            error_message = f"Erreur : {exc}"
            self.status.emit(error_message)

        if success:
            self.status.emit("Terminé")
        self.finished.emit(success, None, error_message)


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
        self.table.cellDoubleClicked.connect(self._on_row_double_clicked)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)

        self.status_label = QLabel("Prêt.")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setStyleSheet(
            "QProgressBar{ text-align:center; }"
            "QProgressBar::chunk{ background:#28a745; }"
        )

        options_layout = QHBoxLayout()
        self.download_video_checkbox = QCheckBox("MP4 (vidéo)")
        self.download_video_checkbox.setChecked(True)
        self.download_audio_checkbox = QCheckBox("MP3 (audio)")
        self.download_audio_checkbox.setChecked(True)
        self.download_video_checkbox.stateChanged.connect(self._update_download_button_state)
        self.download_audio_checkbox.stateChanged.connect(self._update_download_button_state)
        options_layout.addWidget(self.download_video_checkbox)
        options_layout.addWidget(self.download_audio_checkbox)
        options_layout.addStretch()

        self.download_button = QPushButton("Télécharger")
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self._on_download_clicked)

        layout.addLayout(input_layout)
        layout.addWidget(self.table)
        layout.addWidget(self.status_label)
        layout.addWidget(self.progress)
        layout.addLayout(options_layout)
        layout.addWidget(self.download_button)

    def append_log(self, message: str) -> None:
        """Met à jour le label de statut."""
        self.status_label.setText(message)

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
        self.progress.setValue(0)
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
        rows = [
            f
            for f in formats
            if f.get("ext") == "mp4"
            and not str(f.get("format_id", "")).startswith("sb")
            and f.get("ext") != "mhtml"
            and f.get("vcodec") not in (None, "none")
            and f.get("acodec") not in (None, "none")
        ]
        self.formats_data = rows
        self.table.setRowCount(len(rows))
        for row, data in enumerate(rows):
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
        if rows:
            self.append_log("Double-cliquez sur un format pour le sélectionner.")
        else:
            self.append_log("Aucun format MP4 progressif trouvé.")

    def _on_row_double_clicked(self, row: int, column: int) -> None:  # noqa: ARG002
        if row < 0 or row >= len(self.formats_data):
            return
        format_id = self.formats_data[row].get("format_id")
        if not format_id:
            self.append_log("Format invalide sélectionné.")
            return
        self.table.selectRow(row)
        self.selected_itag = str(format_id)
        self._update_checkmarks(row)
        self.append_log(f"Format sélectionné : itag {self.selected_itag}.")
        self._update_download_button_state()

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
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setData(Qt.ItemDataRole.DecorationRole, QIcon())
        self._update_download_button_state()

    def _update_download_button_state(self) -> None:
        any_download = self.download_video_checkbox.isChecked() or self.download_audio_checkbox.isChecked()
        self.download_button.setEnabled(bool(self.selected_itag) and any_download and not self._is_download_running())

    def _is_download_running(self) -> bool:
        return self.download_worker is not None and self.download_worker.isRunning()

    def _on_download_clicked(self) -> None:
        if not self.current_url or not self.selected_itag:
            self.append_log("Sélectionnez un format avant de télécharger.")
            return
        if not (self.download_video_checkbox.isChecked() or self.download_audio_checkbox.isChecked()):
            self.append_log("Sélectionnez au moins un type de téléchargement.")
            return
        if self._is_download_running():
            self.append_log("Téléchargement déjà en cours.")
            return
        download_dir = ensure_download_dir(Path("./downloads"))
        self.download_button.setEnabled(False)
        self.append_log("Préparation du téléchargement…")
        self.progress.setValue(0)
        self.download_worker = YTDLPDownloadWorker(
            self.current_url,
            self.selected_itag,
            download_dir,
            self.download_video_checkbox.isChecked(),
            self.download_audio_checkbox.isChecked(),
        )
        self.download_worker.progress.connect(self._on_download_progress)
        self.download_worker.status.connect(self.append_log)
        self.download_worker.finished.connect(self._on_download_finished)
        self.download_worker.start()

    def _on_download_progress(self, percent: int) -> None:
        self.progress.setValue(percent)

    def _on_download_finished(self, success: bool, path: Optional[str], error: Optional[str]) -> None:
        if not success:
            if error:
                self.append_log(error)
            else:
                self.append_log("Téléchargement échoué.")
        self.progress.setValue(100 if success else self.progress.value())
        self.download_button.setEnabled(False)
        self.download_worker = None
        self._update_download_button_state()


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
