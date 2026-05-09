import sys
import os
import json
import subprocess
import io
import contextlib
import shutil
from pathlib import Path

import kitab_cli

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QCheckBox, QComboBox,
    QProgressBar, QTextEdit, QFileDialog, QGroupBox, QFormLayout,
    QSpinBox, QMessageBox
)
from PySide6.QtCore import QThread, Signal, Qt

# Assuming the GUI is in the same directory as the CLI scripts
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
KITAB_CLI = os.path.join(BASE_DIR, "kitab_cli.py")

try:
    from kitab_transliterate import add_latin_search_layer
except ImportError:
    add_latin_search_layer = None

class DownloaderWorker(QThread):
    """
    Background thread to run the download, OCR, and transliteration pipeline
    without freezing the UI.
    """
    log_signal = Signal(str, str)         # level, message
    progress_signal = Signal(int, int)    # current, total
    status_signal = Signal(str)           # status bar text
    finished_signal = Signal(bool, str)   # success, final pdf path

    def __init__(self, params):
        super().__init__()
        self.params = params
        self.is_cancelled = False
        self._process = None

    def emit_log(self, msg, level="info"):
        self.log_signal.emit(level, msg)

    def run(self):
        try:
            # 1. Download Phase
            self.emit_log("Starting download phase...", "info")
            
            pdf_path = [None] # Use list to store from inner scope
            
            def handle_line(line):
                if self.is_cancelled:
                    raise InterruptedError("Download cancelled by user.")
                    
                try:
                    event = json.loads(line)
                    t = event.get("type")
                    if t == "log":
                        self.emit_log(event.get("msg", ""), event.get("level", "info"))
                    elif t == "progress":
                        cur = event.get("page", 0)
                        tot = event.get("total", 0)
                        self.progress_signal.emit(cur, tot)
                        self.status_signal.emit(f"Downloading... ({cur}/{tot})")
                    elif t == "pdf":
                        pdf_path[0] = event.get("path")
                except json.JSONDecodeError:
                    self.emit_log(line, "info")

            class SignalStream:
                def __init__(self, callback):
                    self.callback = callback
                    self.buffer = ""
                def write(self, text):
                    self.buffer += text
                    if '\n' in self.buffer:
                        lines = self.buffer.split('\n')
                        self.buffer = lines.pop()
                        for line in lines:
                            if line.strip():
                                self.callback(line.strip())
                def flush(self):
                    pass

            try:
                kitab_cli._json_mode = True
                with contextlib.redirect_stdout(SignalStream(handle_line)):
                    success = kitab_cli.download_book(
                        bibid=self.params["bibid"],
                        output_dir=self.params["output_dir"],
                        delete_images=self.params["delete_images"],
                        start_page=self.params["start_page"] or 1,
                        end_page=self.params["end_page"],
                        workers=5
                    )
            except InterruptedError as e:
                self.emit_log(str(e), "warning")
                self.finished_signal.emit(False, "")
                return
            except Exception as e:
                self.emit_log(f"Download process failed: {e}", "error")
                self.finished_signal.emit(False, "")
                return

            if not pdf_path[0] or not os.path.exists(pdf_path[0]):
                self.emit_log("Download completed but PDF was not found.", "error")
                self.finished_signal.emit(False, "")
                return

            final_pdf_path = pdf_path[0]

            # 2. OCR Phase
            if self.params["run_ocr"]:
                self.emit_log("Starting OCR phase...", "info")
                self.status_signal.emit("Running OCR... (This may take a while)")
                
                ocr_pdf_path = final_pdf_path.replace(".pdf", "_ocr.pdf")
                ocr_cmd = [
                    "ocrmypdf",
                    "-l", self.params["ocr_lang"],
                    "--jobs", "4" # Be gentle on resources
                ]
                
                if self.params["ocr_deskew"]:
                    ocr_cmd.append("--deskew")
                if self.params["ocr_clean"]:
                    ocr_cmd.append("--clean")
                if self.params["ocr_rotate"]:
                    ocr_cmd.append("--rotate-pages")
                    
                ocr_cmd.extend([final_pdf_path, ocr_pdf_path])
                
                self.emit_log(f"Running: {' '.join(ocr_cmd)}", "info")
                
                # We can't easily parse ocrmypdf's rich progress output via stdout pipe perfectly, 
                # so we'll just capture it and put the progress bar in indeterminate mode.
                self.progress_signal.emit(0, 0) 
                
                self._process = subprocess.Popen(
                    ocr_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8"
                )
                
                for line in iter(self._process.stdout.readline, ''):
                    if self.is_cancelled:
                        self._process.terminate()
                        self.emit_log("OCR cancelled by user.", "warning")
                        self.finished_signal.emit(False, "")
                        return
                    # Optionally log OCR output, but it can be very spammy
                    # self.emit_log(line.strip(), "info")
                
                self._process.wait()
                
                if self._process.returncode == 0 and os.path.exists(ocr_pdf_path):
                    self.emit_log("OCR completed successfully.", "success")
                    final_pdf_path = ocr_pdf_path
                    
                    # 3. Transliteration Phase
                    if self.params["add_latin"] and add_latin_search_layer:
                        self.emit_log("Adding Latin search layer...", "info")
                        self.status_signal.emit("Adding Latin search layer...")
                        
                        def progress_cb(cur, tot):
                            self.progress_signal.emit(cur, tot)
                            
                        success = add_latin_search_layer(final_pdf_path, progress_callback=progress_cb)
                        if success:
                            self.emit_log("Latin search layer added.", "success")
                        else:
                            self.emit_log("Failed to add Latin search layer.", "warning")
                else:
                    self.emit_log(f"OCR failed with code {self._process.returncode}.", "error")
                    # We still have the original PDF, so we don't return False entirely

            self.emit_log("Pipeline finished successfully!", "success")
            self.status_signal.emit("Done.")
            self.finished_signal.emit(True, final_pdf_path)

        except Exception as e:
            self.emit_log(f"An error occurred: {str(e)}", "error")
            self.finished_signal.emit(False, "")

    def cancel(self):
        self.is_cancelled = True
        if self._process:
            self._process.terminate()

class KitabMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Kitab — ANL Downloader & OCR")
        self.resize(700, 600)
        
        self.worker = None

        # Main Widget and Layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)

        # --- Settings Group ---
        settings_group = QGroupBox("Download Settings")
        settings_layout = QFormLayout()
        
        self.input_bibid = QLineEdit()
        self.input_bibid.setPlaceholderText("e.g. vtls000233649")
        
        # Output Directory picker
        dir_layout = QHBoxLayout()
        self.input_outdir = QLineEdit(os.getcwd())
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self.browse_output_dir)
        dir_layout.addWidget(self.input_outdir)
        dir_layout.addWidget(self.btn_browse)

        # Pages
        pages_layout = QHBoxLayout()
        self.spin_start = QSpinBox()
        self.spin_start.setRange(1, 9999)
        self.spin_start.setValue(1)
        self.spin_end = QSpinBox()
        self.spin_end.setRange(0, 9999)
        self.spin_end.setValue(0)
        self.spin_end.setSpecialValueText("All")
        pages_layout.addWidget(QLabel("Start:"))
        pages_layout.addWidget(self.spin_start)
        pages_layout.addWidget(QLabel("End:"))
        pages_layout.addWidget(self.spin_end)
        pages_layout.addStretch()

        self.chk_delete_images = QCheckBox("Delete individual images after PDF creation")
        self.chk_delete_images.setChecked(True)

        settings_layout.addRow("Book ID:", self.input_bibid)
        settings_layout.addRow("Output Folder:", dir_layout)
        settings_layout.addRow("Pages:", pages_layout)
        settings_layout.addRow("", self.chk_delete_images)
        settings_group.setLayout(settings_layout)

        # --- OCR Group ---
        ocr_group = QGroupBox("OCR & Processing")
        ocr_layout = QFormLayout()

        self.chk_ocr = QCheckBox("Run OCR (ocrmypdf)")
        
        has_ocrmypdf = shutil.which("ocrmypdf") is not None
        if not has_ocrmypdf:
            self.chk_ocr.setEnabled(False)
            self.chk_ocr.setToolTip("ocrmypdf is not installed or not in PATH. Please install it to enable OCR.")
            
        self.chk_ocr.setChecked(False)
        self.chk_ocr.toggled.connect(self.toggle_ocr_options)

        self.combo_lang = QComboBox()
        self.combo_lang.addItems([
            "aze_cyrl+rus", "aze+rus", "aze_cyrl", "aze", "rus", "eng"
        ])
        
        self.chk_deskew = QCheckBox("Deskew pages")
        self.chk_clean = QCheckBox("Clean artifacts")
        self.chk_rotate = QCheckBox("Auto-rotate pages")
        self.chk_latin = QCheckBox("Add Latin search layer (Cyrillic to Latin)")
        self.chk_latin.setChecked(True)
        if not add_latin_search_layer:
            self.chk_latin.setEnabled(False)
            self.chk_latin.setToolTip("kitab_transliterate.py not found or fitz not installed.")

        ocr_layout.addRow("", self.chk_ocr)
        ocr_layout.addRow("Language:", self.combo_lang)
        
        opts_layout = QHBoxLayout()
        opts_layout.addWidget(self.chk_deskew)
        opts_layout.addWidget(self.chk_clean)
        opts_layout.addWidget(self.chk_rotate)
        ocr_layout.addRow("Options:", opts_layout)
        ocr_layout.addRow("", self.chk_latin)
        ocr_group.setLayout(ocr_layout)

        # --- Progress Area ---
        progress_group = QGroupBox("Status")
        progress_layout = QVBoxLayout()
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.lbl_status = QLabel("Ready.")
        
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("font-family: monospace;")

        progress_layout.addWidget(self.lbl_status)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.txt_log)
        progress_group.setLayout(progress_layout)

        # --- Action Buttons ---
        action_layout = QHBoxLayout()
        self.btn_start = QPushButton("Start Processing")
        self.btn_start.setMinimumHeight(40)
        self.btn_start.setStyleSheet("font-weight: bold; background-color: #2e7d32; color: white;")
        self.btn_start.clicked.connect(self.start_processing)
        
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setMinimumHeight(40)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_processing)

        action_layout.addWidget(self.btn_start, stretch=3)
        action_layout.addWidget(self.btn_cancel, stretch=1)

        # Assemble Main Layout
        main_layout.addWidget(settings_group)
        main_layout.addWidget(ocr_group)
        main_layout.addWidget(progress_group)
        main_layout.addLayout(action_layout)

        self.toggle_ocr_options(False)

    def browse_output_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Output Directory", self.input_outdir.text())
        if dir_path:
            self.input_outdir.setText(dir_path)

    def toggle_ocr_options(self, checked):
        self.combo_lang.setEnabled(checked)
        self.chk_deskew.setEnabled(checked)
        self.chk_clean.setEnabled(checked)
        self.chk_rotate.setEnabled(checked)
        self.chk_latin.setEnabled(checked and add_latin_search_layer is not None)

    def log_message(self, level, msg):
        colors = {
            "info": "#ffffff",
            "warning": "#ff9800",
            "error": "#f44336",
            "success": "#4caf50"
        }
        color = colors.get(level, "#ffffff")
        self.txt_log.append(f"<span style='color:{color}'>{msg}</span>")
        # Auto scroll to bottom
        scrollbar = self.txt_log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_progress(self, current, total):
        if total == 0:
            self.progress_bar.setMaximum(0) # Indeterminate mode
            self.progress_bar.setValue(0)
        else:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)

    def start_processing(self):
        bibid = self.input_bibid.text().strip()
        if not bibid:
            QMessageBox.warning(self, "Input Error", "Please enter a valid Book ID.")
            return

        outdir = self.input_outdir.text().strip()
        if not os.path.exists(outdir):
            QMessageBox.warning(self, "Input Error", "Output directory does not exist.")
            return

        params = {
            "bibid": bibid,
            "output_dir": outdir,
            "start_page": self.spin_start.value() if self.spin_start.value() > 1 else None,
            "end_page": self.spin_end.value() if self.spin_end.value() > 0 else None,
            "delete_images": self.chk_delete_images.isChecked(),
            "run_ocr": self.chk_ocr.isChecked(),
            "ocr_lang": self.combo_lang.currentText(),
            "ocr_deskew": self.chk_deskew.isChecked(),
            "ocr_clean": self.chk_clean.isChecked(),
            "ocr_rotate": self.chk_rotate.isChecked(),
            "add_latin": self.chk_latin.isChecked(),
        }

        # UI State update
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.txt_log.clear()
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximum(100)

        # Start Worker
        self.worker = DownloaderWorker(params)
        self.worker.log_signal.connect(self.log_message)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.status_signal.connect(self.lbl_status.setText)
        self.worker.finished_signal.connect(self.on_processing_finished)
        self.worker.start()

    def cancel_processing(self):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.btn_cancel.setEnabled(False)
            self.lbl_status.setText("Cancelling...")

    def on_processing_finished(self, success, pdf_path):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setMaximum(100)
            self.progress_bar.setValue(100)

        if success:
            self.lbl_status.setText("Finished successfully!")
            QMessageBox.information(self, "Success", f"Processing complete!\nSaved to: {pdf_path}")
        else:
            self.lbl_status.setText("Failed or Cancelled.")

if __name__ == "__main__":
    # Create the Qt Application
    app = QApplication(sys.argv)
    
    # Try to set a modern dark theme if available (fusion)
    app.setStyle("Fusion")

    # Create and show the main window
    window = KitabMainWindow()
    window.show()

    # Run the main Qt loop
    sys.exit(app.exec())
