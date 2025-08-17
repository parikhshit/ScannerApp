import sys
import subprocess
import platform
import asyncio
import aiohttp
import json
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QPushButton, QWidget, QProgressBar, QLabel, QLineEdit,
    QInputDialog, QMessageBox
)
from PyQt6.QtCore import Qt, QThreadPool, QRunnable, pyqtSignal, QObject
from PyQt6.QtGui import QFont

# ---------- CONFIG ----------
PERPLEXITY_CHAT_URL = "https://api.perplexity.ai/chat/completions"
MODEL_NAME = "sonar"
MAX_CONCURRENT_REQUESTS = 5
RETRY_DELAY = 5
MAX_RETRIES = 3

# ---------- FUNCTIONALITY ----------
def get_installed_software():
    """Get installed software cross-platform"""
    software_list = []
    system = platform.system()
    try:
        if system == "Windows":
            result = subprocess.run(
                ["wmic", "product", "get", "name,version"], capture_output=True, text=True
            )
            lines = result.stdout.splitlines()
            for line in lines[1:]:
                if line.strip():
                    parts = line.strip().split()
                    if len(parts) >= 2:
                        version = parts[-1]
                        name = " ".join(parts[:-1])
                        software_list.append((name, version))
        elif system == "Darwin":  # macOS
            result = subprocess.run(
                ["system_profiler", "SPApplicationsDataType", "-json"], capture_output=True, text=True
            )
            data = json.loads(result.stdout)
            for app in data.get("SPApplicationsDataType", []):
                name = app.get("_name") or app.get("name")
                version = app.get("version") or "Unknown"
                if name:
                    software_list.append((name, version))
        elif system == "Linux":
            try:
                result = subprocess.run(
                    ["dpkg-query", "-W", "-f=${Package} ${Version}\n"], capture_output=True, text=True
                )
                lines = result.stdout.splitlines()
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 2:
                        name = parts[0]
                        version = parts[1]
                        software_list.append((name, version))
            except Exception:
                result = subprocess.run(
                    ["rpm", "-qa", "--queryformat", "%{NAME} %{VERSION}\n"], capture_output=True, text=True
                )
                lines = result.stdout.splitlines()
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 2:
                        name = parts[0]
                        version = parts[1]
                        software_list.append((name, version))
    except Exception:
        pass
    return software_list

async def safe_post(session, json_payload, headers, retries=MAX_RETRIES, delay=RETRY_DELAY):
    """POST request with retry/backoff for rate limits"""
    for attempt in range(retries):
        try:
            async with session.post(PERPLEXITY_CHAT_URL, json=json_payload, headers=headers, timeout=20) as resp:
                if resp.status == 429:
                    await asyncio.sleep(delay * (attempt + 1))
                    continue
                text = await resp.text()
                return resp.status, text
        except Exception:
            await asyncio.sleep(delay * (attempt + 1))
    return None, "Max retries exceeded"

async def check_software_risk_async(session, software_name, api_key, semaphore):
    """Async Perplexity call, output only Safety + RCA"""
    prompt = (
        f"Check if the software '{software_name}' is harmful. "
        "If harmful, provide root cause analysis. "
        "Respond in valid JSON with keys: 'safety' (SAFE/HARMFUL), 'rca'."
    )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": MODEL_NAME, "messages": [{"role": "user", "content": prompt}], "temperature": 0}

    async with semaphore:
        status, text_response = await safe_post(session, payload, headers)
        if status != 200:
            return software_name, "UNKNOWN", "Error fetching data"

        try:
            data = json.loads(text_response)
            text = data['choices'][0]['message']['content']
            result = json.loads(text)
            safety = result.get("safety", "UNKNOWN")
            rca = result.get("rca", "")
        except Exception:
            safety = "UNKNOWN"
            rca = "Could not parse response"

        return software_name, safety, rca

# ---------- WORKER ----------
class WorkerSignals(QObject):
    progress = pyqtSignal(int)
    update_row = pyqtSignal(int, str, str)

class AsyncWorker(QRunnable):
    def __init__(self, software_list, api_key):
        super().__init__()
        self.software_list = software_list
        self.api_key = api_key
        self.signals = WorkerSignals()

    def run(self):
        asyncio.run(self.async_run())

    async def async_run(self):
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
        async with aiohttp.ClientSession() as session:
            tasks = [check_software_risk_async(session, s[0], self.api_key, semaphore) for s in self.software_list]
            for i, task in enumerate(asyncio.as_completed(tasks)):
                software_name, safety, rca = await task
                self.signals.update_row.emit(i, safety, rca)
                progress_percent = int((i+1)/len(self.software_list)*100)
                self.signals.progress.emit(progress_percent)

# ---------- GUI ----------
class SoftwareScannerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PARIKHSHIT - Agentic AI Software Scanner")
        self.setGeometry(200, 100, 950, 650)
        
        self.api_key = ""
        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout()
        self.central_widget.setLayout(self.layout)

        # Creative Header with PARIKHSHIT
        header = QLabel("🚀 Welcome to Parikhshit's AI Scanner")
        header.setFont(QFont("Arial", 18, QFont.Weight.Bold))
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.setStyleSheet("color: #FF8800;")
        self.layout.addWidget(header)

        # API Key input
        self.get_api_key()

        # Filter/search
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter by software name...")
        self.search_input.textChanged.connect(self.filter_table)
        self.layout.addWidget(QLabel("Search / Filter:"))
        self.layout.addWidget(self.search_input)

        # Table with 4 columns
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels([
            "Software", "Installed Version", "Status", "Root Cause Analysis"
        ])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.layout.addWidget(self.table)
        self.table.cellDoubleClicked.connect(self.show_rca_popup)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.layout.addWidget(self.progress_bar)

        # Scan button with custom style
        self.scan_button = QPushButton("🚀 Scan Software (PARIKHSHIT)")
        self.scan_button.clicked.connect(self.start_scan)
        self.layout.addWidget(self.scan_button)

        # Thread pool
        self.threadpool = QThreadPool()

        # Creative Styles
        self.setStyleSheet("""
            QTableWidget { font-size: 14px; gridline-color: #FF8800; }
            QHeaderView::section { background-color: #444; color: white; font-weight: bold; }
            QPushButton { background-color: #FF8800; color: white; font-size: 16px; padding: 10px; border-radius: 8px; }
            QPushButton:hover { background-color: #FFAA33; }
            QLineEdit { padding: 5px; font-size: 14px; border: 1px solid #FF8800; border-radius: 5px; }
        """)

    def get_api_key(self):
        key, ok = QInputDialog.getText(self, "API Key Required", "Enter Perplexity API Key:", QLineEdit.EchoMode.Password)
        if ok and key:
            self.api_key = key
        else:
            sys.exit("API Key required to run scanner")

    def start_scan(self):
        self.software_list = get_installed_software()
        if not self.software_list:
            return

        self.table.setRowCount(len(self.software_list))
        for i, (software, version) in enumerate(self.software_list):
            self.table.setItem(i, 0, QTableWidgetItem(software))
            self.table.setItem(i, 1, QTableWidgetItem(version))
            self.table.setItem(i, 2, QTableWidgetItem("Checking..."))
            self.table.setItem(i, 3, QTableWidgetItem(""))

        worker = AsyncWorker(self.software_list, self.api_key)
        worker.signals.progress.connect(self.progress_bar.setValue)
        worker.signals.update_row.connect(self.update_table_row)
        self.threadpool.start(worker)

    def update_table_row(self, row, safety, rca):
        row = min(row, self.table.rowCount() - 1)
        self.table.setItem(row, 2, QTableWidgetItem(safety))
        self.table.setItem(row, 3, QTableWidgetItem(rca))
        if safety.upper() == "HARMFUL":
            color = Qt.GlobalColor.red
        elif safety.upper() == "SAFE":
            color = Qt.GlobalColor.green
        else:
            color = Qt.GlobalColor.yellow
        self.table.item(row, 2).setBackground(color)
        self.table.item(row, 3).setBackground(color)

    def filter_table(self):
        text = self.search_input.text().lower()
        for row in range(self.table.rowCount()):
            software_name = self.table.item(row, 0).text().lower()
            self.table.setRowHidden(row, text not in software_name)

    def show_rca_popup(self, row, column):
        if column == 3:  # RCA column
            rca_text = self.table.item(row, column).text()
            QMessageBox.information(self, "Root Cause Analysis", rca_text or "No RCA available")

# ---------- RUN ----------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SoftwareScannerApp()
    window.show()
    sys.exit(app.exec())
