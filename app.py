"""Application PySide6 pour analyse et téléchargement YouTube."""
from __future__ import annotations

import sys
from os import stat
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
    QProgressBar,
)

import yt_dlp


BASE_DIR = Path(r"C:\Users\Lamine\Desktop\alpha\downloads")
VIDEOS_DIR = BASE_DIR / "videos"
AUDIOS_DIR = BASE_DIR / "audios"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
AUDIOS_DIR.mkdir(parents=True, exist_ok=True)


def _estimate_size_bytes(fmt: Dict[str, Any], duration: Optional[float]) -> Tuple[Optional[int], bool]:
    """Retourne (bytes, is_approx) en se basant sur les informations disponibles."""
    filesize = fmt.get("filesize")
    if isinstance(filesize, (int, float)) and filesize > 0:
        return int(filesize), False
    filesize_approx = fmt.get("filesize_approx")
    if isinstance(filesize_approx, (int, float)) and filesize_approx > 0:
        return int(filesize_approx), True
    tbr = fmt.get("tbr")
    if not tbr:
        vbr, abr = fmt.get("vbr"), fmt.get("abr")
        if vbr and abr:
            tbr = float(vbr) + float(abr)
    if duration and tbr:
        estimate = int((float(tbr) * 1000.0 / 8.0) * float(duration))
        return estimate, True
    return None, True


def _human_mb(nbytes: Optional[int], approx: bool) -> str:
    if not nbytes or nbytes <= 0:
        return "?"
    value = nbytes / (1024 * 1024)
    text = f"{value:.1f} Mo"
    return f"~{text}" if approx else text


def _fmt_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "Durée : ?"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"Durée : {hours:d}:{minutes:02d}:{sec:02d}"
    return f"Durée : {minutes:d}:{sec:02d}"


def _human_mb_exact(path: Path) -> Tuple[str, Optional[int]]:
    try:
        size = stat(path).st_size
    except OSError:
        return "?", None
    return f"{size / (1024 * 1024):.1f} Mo", size


class YTDLPProbeWorker(QThread):
    """Worker chargé d'analyser une URL YouTube."""

    info_ready: Signal = Signal(dict)
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
            if isinstance(info, dict):
                formats = info.get("formats", []) or []
                self.log.emit(f"Formats trouvés : {len(formats)}")
                self.info_ready.emit(info)
            else:
                self.error.emit("Réponse inattendue d'yt-dlp.")
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
    finished: Signal = Signal(bool, object, object, object)

    def __init__(
        self,
        url: str,
        itag: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._url = url
        self._itag = itag

    def _emit_progress(self, value: int) -> None:
        self.progress.emit(max(0, min(100, value)))

    def run(self) -> None:
        success = True
        error_message: Optional[str] = None
        video_path: Optional[str] = None
        audio_path: Optional[str] = None

        def hook(data: Dict[str, Any]) -> None:
            nonlocal video_path, audio_path
            status = data.get("status")
            if status == "downloading":
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                done = data.get("downloaded_bytes", 0)
                pct = int(done * 100 / total) if total else 0
                self.progress.emit(pct)
            elif status == "finished":
                filename = data.get("filename")
                if filename and filename.endswith(".mp3"):
                    audio_path = filename
                else:
                    video_path = filename or video_path
                self.progress.emit(100)

        try:
            # Étape 1 : vidéo MP4
            self._emit_progress(0)
            self.status.emit("Téléchargement vidéo (1/2)…")
            ydl_opts_mp4 = {
                "format": f"{self._itag}",
                "merge_output_format": "mp4",
                "outtmpl": str(VIDEOS_DIR / "%(title)s.%(ext)s"),
                "progress_hooks": [hook],
                "quiet": True,
                "no_warnings": True,
            }
            with yt_dlp.YoutubeDL(ydl_opts_mp4) as ydl:
                ydl.download([self._url])

            # Étape 2 : audio MP3
            self._emit_progress(0)
            self.status.emit("Extraction audio (2/2)…")
            ydl_opts_mp3 = {
                "format": "bestaudio/best",
                "outtmpl": str(AUDIOS_DIR / "%(title)s.%(ext)s"),
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
            try:
                with yt_dlp.YoutubeDL(ydl_opts_mp3) as ydl:
                    ydl.download([self._url])
            except Exception:  # noqa: BLE001
                self.status.emit("FFmpeg introuvable : MP3 non généré.")
                audio_path = None

        except Exception as exc:  # noqa: BLE001
            success = False
            error_message = f"Erreur : {exc}"
            self.status.emit(error_message)
        else:
            self.status.emit("Terminé")

        self.finished.emit(success, video_path, audio_path, error_message)


class YouTubeTab(QWidget):
    """Onglet dédié aux fonctionnalités YouTube."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.selected_itag: Optional[str] = None
        self.current_url: str = ""
        self.formats_data: List[Dict[str, Any]] = []
        self.current_info: Dict[str, Any] = {}
        self._selected_row: Optional[int] = None
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

        self.duration_label = QLabel("Durée : ?")

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["✓", "itag", "resolution", "taille"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.cellDoubleClicked.connect(self._on_row_double_clicked)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)

        self.status_lbl = QLabel("Prêt.")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setStyleSheet(
            "QProgressBar{ text-align:center; }"
            "QProgressBar::chunk{ background:#28a745; }"
        )

        self.download_button = QPushButton("Télécharger")
        self.download_button.setEnabled(False)
        self.download_button.clicked.connect(self._on_download_clicked)

        layout.addLayout(input_layout)
        layout.addWidget(self.duration_label)
        layout.addWidget(self.table)
        layout.addWidget(self.status_lbl)
        layout.addWidget(self.progress)
        layout.addWidget(self.download_button)

    def append_log(self, message: str) -> None:
        """Met à jour le label de statut."""
        self.status_lbl.setText(message)

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
        self.current_info = {}
        self.current_url = url
        self.progress.setValue(0)
        self.duration_label.setText("Durée : ?")
        self.probe_worker = YTDLPProbeWorker(url)
        self.probe_worker.log.connect(self.append_log)
        self.probe_worker.error.connect(self.append_log)
        self.probe_worker.info_ready.connect(self._populate_formats)
        self.probe_worker.finished.connect(self._on_probe_finished)
        self.probe_worker.start()

    def _on_probe_finished(self) -> None:
        self.analyze_button.setEnabled(True)
        self.probe_worker = None

    def _populate_formats(self, info: Dict[str, Any]) -> None:
        self.current_info = info
        duration = info.get("duration")
        self.duration_label.setText(_fmt_duration(duration if isinstance(duration, (int, float)) else None))
        formats = info.get("formats", []) or []
        rows = [
            fmt
            for fmt in formats
            if fmt.get("ext") == "mp4"
            and not str(fmt.get("format_id", "")).startswith("sb")
            and fmt.get("ext") != "mhtml"
            and fmt.get("vcodec") not in (None, "none")
            and fmt.get("acodec") not in (None, "none")
        ]

        self.formats_data = rows
        sorting_enabled = self.table.isSortingEnabled()
        if sorting_enabled:
            self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        for fmt in rows:
            itag = str(fmt.get("format_id", ""))
            width, height = fmt.get("width"), fmt.get("height")
            if width and height:
                resolution = f"{width}x{height}"
            elif height:
                resolution = f"{height}p"
            else:
                resolution = "?"
            nbytes, approx = _estimate_size_bytes(fmt, float(duration) if duration else None)
            human_size = _human_mb(nbytes, approx)

            row = self.table.rowCount()
            self.table.insertRow(row)

            check_item = QTableWidgetItem("")
            check_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, check_item)

            item_itag = QTableWidgetItem(itag)
            item_res = QTableWidgetItem(resolution)
            item_size = QTableWidgetItem(human_size)

            height_value = int(height) if height else 0
            item_res.setData(Qt.ItemDataRole.UserRole, height_value)
            item_size.setData(Qt.ItemDataRole.UserRole, nbytes or 0)

            for column, item in enumerate([item_itag, item_res, item_size], start=1):
                item.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(row, column, item)

        self.table.resizeColumnsToContents()
        if self.table.columnCount() > 0:
            self.table.setColumnWidth(0, 30)

        if rows:
            self.append_log("Double-cliquez sur un format pour le sélectionner.")
            self._auto_pick_row()
        else:
            self.append_log("Aucun format MP4 progressif trouvé.")
        if sorting_enabled:
            self.table.setSortingEnabled(True)

    def _on_row_double_clicked(self, row: int, column: int) -> None:  # noqa: ARG002
        self._select_row(row)

    def _select_row(self, row: int) -> None:
        if row < 0 or row >= len(self.formats_data):
            return
        fmt = self.formats_data[row]
        format_id = fmt.get("format_id")
        if not format_id:
            self.append_log("Format invalide sélectionné.")
            return
        self.table.selectRow(row)
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                item.setText("✓" if r == row else "")
        self.selected_itag = str(format_id)
        self._selected_row = row
        self.append_log(f"Format sélectionné : itag {self.selected_itag}.")
        self._update_download_button_state()

    def _auto_pick_row(self) -> None:
        best_row: Optional[int] = None
        best_height = 0
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 2)
            height = item.data(Qt.ItemDataRole.UserRole) if item else 0
            height = int(height) if height else 0
            if height == 720:
                best_row = row
                break
            if height > best_height:
                best_height = height
                best_row = row
        if best_row is not None:
            self._select_row(best_row)

    def _reset_selection(self) -> None:
        self.selected_itag = None
        self._selected_row = None
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setText("")
        self._update_download_button_state()

    def _update_download_button_state(self) -> None:
        self.download_button.setEnabled(bool(self.selected_itag) and not self._is_download_running())

    def _is_download_running(self) -> bool:
        return self.download_worker is not None and self.download_worker.isRunning()

    def _on_download_clicked(self) -> None:
        if not self.current_url or not self.selected_itag:
            self.append_log("Sélectionnez un format avant de télécharger.")
            return
        if self._is_download_running():
            self.append_log("Téléchargement déjà en cours.")
            return
        self.download_button.setEnabled(False)
        self.append_log("Préparation du téléchargement…")
        self.progress.setValue(0)
        self.download_worker = YTDLPDownloadWorker(
            self.current_url,
            self.selected_itag,
        )
        self.download_worker.progress.connect(self._on_download_progress)
        self.download_worker.status.connect(self.append_log)
        self.download_worker.finished.connect(self._on_download_finished)
        self.download_worker.start()

    def _on_download_progress(self, percent: int) -> None:
        self.progress.setValue(percent)

    def _on_download_finished(
        self,
        success: bool,
        video_path: Optional[str],
        audio_path: Optional[str],  # noqa: ARG002
        error: Optional[str],
    ) -> None:
        if not success:
            if error:
                self.append_log(error)
            else:
                self.append_log("Téléchargement échoué.")
        if success and video_path and self._selected_row is not None:
            text, size_bytes = _human_mb_exact(Path(video_path))
            item = self.table.item(self._selected_row, 3)
            if item:
                item.setText(text)
                if size_bytes is not None:
                    item.setData(Qt.ItemDataRole.UserRole, size_bytes)
        self.progress.setValue(100 if success else self.progress.value())
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
