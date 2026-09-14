"""
Desktop-интерфейс агента ISKER на PySide6.
Поддерживает переключение языка интерфейса (RU/EN/ES) через выпадающий список.
Ответы самого агента подстраиваются под язык вопроса — настроено отдельно
в системном промпте (agent/loop.py), не зависит от языка UI.

Визуально: киберпанк-тема с анимированным "нейросетевым" фоном (узлы,
соединённые линиями, медленно движутся и пульсируют) для эффекта глубины.
"""

import sys
import logging 
import math
import random
import html
import ctypes
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit,
    QPushButton, QTextEdit, QVBoxLayout, QHBoxLayout, QComboBox,
    QStackedLayout, QCheckBox
)

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QPalette, QFont, QFontDatabase, QIcon

from agent.loop import SessionState, run_task, update_project_summary


def _resource_path(*parts) -> Path:
    """
    Путь к встроенному ресурсу (шрифт, иконка) — работает одинаково что при
    запуске из исходников (python -m ui.app), что из собранного .exe
    (PyInstaller распаковывает ресурсы во временную папку sys._MEIPASS).
    """
    if getattr(sys, "frozen", False):
        base_path = Path(sys._MEIPASS)
    else:
        base_path = Path(__file__).parent.parent
    return base_path.joinpath(*parts)


def _app_dir() -> Path:
    """
    Папка, где реально лежит запущенный .exe (или папка проекта при запуске
    из исходников) — сюда пишем лог-файл, рядом с приложением, а не во
    временную папку распаковки, которая удаляется после закрытия программы.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent.parent


from logging.handlers import RotatingFileHandler

LOG_PATH = _app_dir() / "isker.log"

_formatter = logging.Formatter(
    fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
_file_handler = RotatingFileHandler(
    LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(_formatter)

logging.basicConfig(level=logging.INFO, handlers=[_file_handler])
logger = logging.getLogger(__name__)

FONT_PATH = _resource_path("assets", "fonts", "Jura-SemiBold.ttf")


def load_custom_font() -> str:
    """
    Подключает шрифт из assets/fonts напрямую в приложение (без установки
    в систему пользователя) — так .exe будет выглядеть одинаково у всех,
    даже если у пользователя сам шрифт не установлен.
    Возвращает имя семейства шрифта для использования в CSS, либо запасной
    вариант 'Cascadia Mono', если файл не найден или не загрузился.
    """
    font_id = QFontDatabase.addApplicationFont(str(FONT_PATH))
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        return families[0]
    return "Cascadia Mono"


def build_cyberpunk_style(FONT_FAMILY: str) -> str:
    return f"""
QWidget#overlay {{
    background: transparent;
}}  
QCheckBox {{
    color: #00fff9;
    font-family: '{FONT_FAMILY}';       
    font-size: 14px;
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid #00fff9;
    background-color: rgba(10, 14, 20, 220);
}}
QCheckBox::indicator:checked {{
    background-color: #00fff9;
}}

QLabel {{
    color: #00fff9;
    font-family: '{FONT_FAMILY}';
    font-size: 14px;
    background: transparent;
}}
QLineEdit {{
    background-color: rgba(10, 14, 20, 220);
    color: #e0e0e0;
    border: 1px solid #00fff9;
    padding: 6px;
    font-family: '{FONT_FAMILY}';
    font-size: 14px;
}}
QLineEdit:focus {{
    border: 1px solid #ff00ff;
}}
QLineEdit:disabled {{
    color: #555a66;
    border: 1px solid #2a2f3a;
}}
QLineEdit#taskInput {{
    font-size: 17px;
    padding: 12px;
    border: 2px solid #00fff9;
    border-radius: 4px;
    min-height: 28px;
}}
QLineEdit#taskInput:focus {{
    border: 2px solid #ff00ff;
}}
QTextEdit {{
    background-color: rgba(10, 14, 20, 210);
    color: #e0e0e0;
    border: 1px solid #2a2f3a;
    font-family: '{FONT_FAMILY}';
    font-size: 14px;
}}
QPushButton {{
    background-color: rgba(16, 21, 31, 230);
    color: #00fff9;
    border: 1px solid #00fff9;
    border-radius: 6px;
    padding: 6px 16px;
    font-family: '{FONT_FAMILY}';
    font-weight: bold;
    min-height: 20px;
}}
QPushButton:hover {{
    background-color: #00fff9;
    color: #0a0e14;
}}
QPushButton:disabled {{
    color: #555a66;
    border: 1px solid #2a2f3a;
}}
QPushButton#startButton, QPushButton#newSessionButton {{
    padding: 10px 26px;
    font-size: 14px;
    border-radius: 8px;
}}
QPushButton#newSessionButton {{
    color: #ff00ff;
    border: 1px solid #ff00ff;
}}
QPushButton#newSessionButton:hover {{
    background-color: #ff00ff;
    color: #0a0e14;
}}
QPushButton#sendButton {{
    font-size: 15px;
    padding: 10px 24px;
    border-radius: 8px;
}}
QComboBox {{
    background-color: rgba(16, 21, 31, 230);
    color: #00fff9;
    border: 1px solid #00fff9;
    border-radius: 6px;
    padding: 6px 10px;
    font-family: '{FONT_FAMILY}';
    min-height: 26px;
    min-width: 64px;
}}
QComboBox QAbstractItemView {{
    background-color: #10151f;
    color: #00fff9;
    border: 1px solid #00fff9;
    selection-background-color: #00fff9;
    selection-color: #0a0e14;
}}
"""

# ---------------------------------------------------------------------------
# Анимированный фон: узлы ("нейроны"), соединённые линиями, если близко друг
# к другу, медленно плавают и пульсируют. Рисуется через QPainter на таймере.
# ---------------------------------------------------------------------------
class NeuralBackgroundWidget(QWidget):
    def __init__(self, parent=None, node_count=32):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.nodes = []
        for _ in range(node_count):
            self.nodes.append({
                "x": random.uniform(0, 1),
                "y": random.uniform(0, 1),
                "vx": random.uniform(-0.0004, 0.0004),
                "vy": random.uniform(-0.0004, 0.0004),
                "phase": random.uniform(0, 6.28),
            })
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(33)  # ~30 fps

    def _tick(self):
        for n in self.nodes:
            n["x"] += n["vx"]
            n["y"] += n["vy"]
            if n["x"] < 0 or n["x"] > 1:
                n["vx"] *= -1
            if n["y"] < 0 or n["y"] > 1:
                n["vy"] *= -1
            n["phase"] += 0.03
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(self.rect(), QColor("#0a0e14"))

        pts = [(n["x"] * w, n["y"] * h) for n in self.nodes]

        max_dist = min(w, h) * 0.22
        for i in range(len(pts)):
            for j in range(i + 1, len(pts)):
                x1, y1 = pts[i]
                x2, y2 = pts[j]
                dist = math.hypot(x1 - x2, y1 - y2)
                if dist < max_dist:
                    alpha = int(55 * (1 - dist / max_dist))
                    pen = QPen(QColor(0, 255, 249, alpha))
                    pen.setWidthF(1.0)
                    painter.setPen(pen)
                    painter.drawLine(int(x1), int(y1), int(x2), int(y2))

        for idx, (x, y) in enumerate(pts):
            pulse = 0.5 + 0.5 * math.sin(self.nodes[idx]["phase"])
            radius = 2.5 + pulse * 2
            base_color = QColor("#00fff9") if idx % 3 else QColor("#ff00ff")

            glow = QColor(base_color)
            glow.setAlpha(int(35 + pulse * 35))
            painter.setBrush(glow)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(
                int(x - radius * 2.2), int(y - radius * 2.2),
                int(radius * 4.4), int(radius * 4.4),
            )

            solid = QColor(base_color)
            solid.setAlpha(210)
            painter.setBrush(solid)
            painter.drawEllipse(int(x - radius), int(y - radius), int(radius * 2), int(radius * 2))

class MatrixRainWidget(QWidget):
    CHARS = "01{}[]<>()=+-*/;:.,_|\\!?#@$%^&"

    def __init__(self, parent=None, font_size=16):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.font_size = font_size
        self.columns = []
        self._init_columns()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(50)

    def _init_columns(self):
        col_width = int(self.font_size * 0.45)
        count = max(1, self.width() // col_width) if self.width() else 60
        self.columns = [self._new_column(i * col_width, randomize_y=True) for i in range(count)]

    def _new_column(self, x, randomize_y=False):
        length = random.randint(8, 22)
        return {
            "x": x,
            "y": random.uniform(-30, self.height() or 700) if randomize_y else random.uniform(-30, 0),
            "speed": random.uniform(2.0, 6.0),
            "length": length,
            "chars": [random.choice(self.CHARS) for _ in range(length)],
            "magenta": random.random() < 0.12,
        }

    def resizeEvent(self, event):
        self._init_columns()
        super().resizeEvent(event)

    def _tick(self):
        h = self.height() or 700
        for col in self.columns:
            col["y"] += col["speed"]
            if random.random() < 0.05:
                idx = random.randrange(len(col["chars"]))
                col["chars"][idx] = random.choice(self.CHARS)
            if col["y"] - col["length"] * self.font_size > h:
                col["y"] = random.uniform(-100, -20)
                col["speed"] = random.uniform(2.0, 6.0)
                col["length"] = random.randint(8, 22)
                col["chars"] = [random.choice(self.CHARS) for _ in range(col["length"])]
                col["magenta"] = random.random() < 0.12
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0a0e14"))
        font = QFont("Consolas", max(10, self.font_size - 4))
        font.setBold(True)
        painter.setFont(font)

        for col in self.columns:
            base_color = QColor("#ff00ff") if col["magenta"] else QColor("#00fff9")
            n = len(col["chars"])
            for i, ch in enumerate(col["chars"]):
                y = col["y"] - i * self.font_size
                if y < -self.font_size or y > self.height() + self.font_size:
                    continue
                fade = 1.0 - (i / n)
                alpha = int(max(0.0, fade) * 130)
                if i == 0:
                    alpha = min(255, alpha + 90)
                color = QColor(base_color)
                color.setAlpha(alpha)
                painter.setPen(color)
                painter.drawText(int(col["x"]), int(y), ch)


from PySide6.QtCore import QThread, Signal

class AgentTaskWorker(QThread):
    finished = Signal(str, list, str)  # answer, changed_files, task_summary
    error = Signal(str)           # error message
    step = Signal(str)            # live-комментарий по ходу выполнения

    def __init__(self, state, task):
        super().__init__()
        self.state = state
        self.task = task
        self._cancelled = False

    def request_cancel(self):
        self._cancelled = True

    def run(self):
        try:
            answer, changed_files, task_summary = run_task(
                self.state, self.task,
                on_step=self.step.emit,
                cancel_check=lambda: self._cancelled,
            )
            self.finished.emit(answer, changed_files, task_summary or "")
        except Exception as e:
            self.error.emit(str(e))


# ---------------------------------------------------------------------------
# Переводы всех текстов интерфейса. Ключ -> {язык: текст}.
# Добавление нового языка = добавление ключа "xx" в каждый словарь ниже.
# ---------------------------------------------------------------------------
TRANSLATIONS = {
    "project_label": {"ru": "Проект:", "en": "Project:", "es": "Proyecto:"},
            "project_placeholder": {
        "ru": "Например: C:\\Users\\Имя\\Мой_проект",
        "en": "Example: C:\\Users\\Name\\MyProject",
        "es": "Ejemplo: C:\\Users\\Nombre\\MiProyecto",
    },
    "allow_write_checkbox": {
        "ru": "Разрешить запись в файлы",
        "en": "Allow writing to files",
        "es": "Permitir escritura en archivos",
    },
    
    "start_button": {"ru": "Начать сессию", "en": "Start session", "es": "Iniciar sesión"},
    "new_session_button": {"ru": "Новая сессия", "en": "New session", "es": "Nueva sesión"},
    "session_status_not_started": {"ru": "Сессия: не начата", "en": "Session: not started", "es": "Sesión: no iniciada"},
    "session_status_active": {"ru": "Сессия: активна", "en": "Session: active", "es": "Sesión: activa"},
    "history_placeholder": {
        "ru": "История диалога появится здесь...",
        "en": "Conversation history will appear here...",
        "es": "El historial de conversación aparecerá aquí...",
    },
    "task_placeholder_disabled": {
        "ru": "Сначала начни сессию сверху...",
        "en": "Start a session above first...",
        "es": "Primero inicia una sesión arriba...",
    },
    "task_placeholder_enabled": {
        "ru": "Введи задачу для ISKER...",
        "en": "Enter a task for ISKER...",
        "es": "Introduce una tarea para ISKER...",
    },
    "send_button": {"ru": "Отправить", "en": "Send", "es": "Enviar"},
    "cancel_button": {"ru": "Отмена", "en": "Cancel", "es": "Cancelar"},
    "session_started_no_project": {
        "ru": "сессия начата без проекта — доступны только общие вопросы, без работы с файлами.",
        "en": "session started without a project — only general questions are available, no file access.",
        "es": "sesión iniciada sin proyecto — solo preguntas generales, sin acceso a archivos.",
    },
    "session_started_write": {
        "ru": "сессия начата. Разрешена запись/изменение файлов проекта.",
        "en": "session started. File write/edit access is allowed for this project.",
        "es": "sesión iniciada. Se permite escribir/editar archivos del proyecto.",
    },

    "backup_reminder": {
        "ru": "⚠️ Перед началом работы советую скопировать весь проект и сохранить копию отдельно — если агент что-то испортит, сможешь вернуться к этому моменту.",
        "en": "⚠️ Before starting, it's recommended to copy the whole project and save it elsewhere — if the agent breaks something, you can go back to this point.",
        "es": "⚠️ Antes de empezar, se recomienda copiar todo el proyecto y guardarlo aparte — si el agente rompe algo, podrás volver a este punto.",
    },

    "session_started_readonly": {
        "ru": "сессия начата в режиме только чтения.",
        "en": "session started in read-only mode.",
        "es": "sesión iniciada en modo de solo lectura.",
    },
    "session_reset": {
        "ru": "сессия завершена. Укажи новый проект и начни снова.",
        "en": "session ended. Provide a new project and start again.",
        "es": "sesión finalizada. Indica un nuevo proyecto y comienza de nuevo.",
    },
    "you_label": {"ru": "Ты", "en": "You", "es": "Tú"},
    "error_label": {"ru": "ошибка", "en": "error", "es": "error"},
        "thinking_label": {"ru": "думает...", "en": "thinking...", "es": "pensando..."},
    "task_summary_label": {"ru": "Резюме задачи:", "en": "Task summary:", "es": "Resumen de la tarea:"},
}

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ISKER")
        self.resize(1000, 700)

        self.lang = "ru"
        self.state: SessionState | None = None

        # --- Слоёный центр: анимированный фон + интерфейс поверх него ---
        central = QWidget()
        self.setCentralWidget(central)
        stacked = QStackedLayout(central)
        stacked.setStackingMode(QStackedLayout.StackAll)

        self.background = MatrixRainWidget(font_size=22)
        stacked.addWidget(self.background)

        overlay = QWidget()
        overlay.setObjectName("overlay")
        main_layout = QVBoxLayout(overlay)
        main_layout.setContentsMargins(40, 40, 40, 40)
        main_layout.setSpacing(10)
        stacked.addWidget(overlay)
        stacked.setCurrentWidget(overlay) 

        # --- Верх: настройка сессии, два ряда ---
        # Ряд 1: путь к проекту на всю ширину
        project_row = QHBoxLayout()
        self.project_label = QLabel()
        project_row.addWidget(self.project_label)

        self.project_path_input = QLineEdit()
        placeholder_palette = self.project_path_input.palette()
        placeholder_palette.setColor(QPalette.PlaceholderText, QColor("#a8b3c2"))
        self.project_path_input.setPalette(placeholder_palette)
        project_row.addWidget(self.project_path_input)

        main_layout.addLayout(project_row)

        write_access_row = QHBoxLayout()

        self.allow_write_checkbox = QCheckBox()
        write_access_row.addWidget(self.allow_write_checkbox)
        write_access_row.addStretch(1)

        main_layout.addLayout(write_access_row)

        # Ряд 2: слева статус сессии, справа кнопки и выбор языка
        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 16, 0)

        self.session_status_label = QLabel()
        self.session_status_label.setObjectName("sessionStatusLabel")
        controls_row.addWidget(self.session_status_label)

        controls_row.addStretch(1)

        self.start_button = QPushButton()
        self.start_button.setObjectName("startButton")
        self.start_button.clicked.connect(self.start_session)
        controls_row.addWidget(self.start_button)

        controls_row.addSpacing(10)

        self.new_session_button = QPushButton()
        self.new_session_button.setObjectName("newSessionButton")
        self.new_session_button.setEnabled(False)
        self.new_session_button.clicked.connect(self.reset_session)
        controls_row.addWidget(self.new_session_button)

        controls_row.addStretch(1)

        self.lang_selector = QComboBox()
        self.lang_selector.addItem("RU", "ru")
        self.lang_selector.addItem("EN", "en")
        self.lang_selector.addItem("ES", "es")
        self.lang_selector.currentIndexChanged.connect(self.change_language)
        controls_row.addWidget(self.lang_selector)

        main_layout.addLayout(controls_row)

        # --- Центр: история диалога ---
        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        main_layout.addWidget(self.chat_history, stretch=1)

        # --- Низ: ввод задачи (крупное поле с явной рамкой) ---
        input_layout = QHBoxLayout()
        self.task_input = QLineEdit()
        self.task_input.setObjectName("taskInput")
        self.task_input.setEnabled(False)
        self.task_input.returnPressed.connect(self.send_task)
        input_layout.addWidget(self.task_input, stretch=1)

        self.send_button = QPushButton()
        self.send_button.setObjectName("sendButton")
        self.send_button.setEnabled(False)
        self.send_button.clicked.connect(self.send_task)
        input_layout.addWidget(self.send_button)

        
        self.cancel_button = QPushButton()
        self.cancel_button.setObjectName("cancelButton")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_task)
        input_layout.addWidget(self.cancel_button)

        main_layout.addLayout(input_layout)

        self.apply_translations()

    def t(self, key: str) -> str:
        return TRANSLATIONS[key][self.lang]

    def apply_translations(self):
        self.project_label.setText(self.t("project_label"))
        self.project_path_input.setPlaceholderText(self.t("project_placeholder"))
        self.allow_write_checkbox.setText(self.t("allow_write_checkbox"))
        self.start_button.setText(self.t("start_button"))
        self.new_session_button.setText(self.t("new_session_button"))
        self.chat_history.setPlaceholderText(self.t("history_placeholder"))
        self.send_button.setText(self.t("send_button"))
        self.cancel_button.setText(self.t("cancel_button"))
        if self.state is not None:
            self.session_status_label.setText(self.t("session_status_active"))
            self.session_status_label.setStyleSheet("color: #39ff88;")
        else:
            self.session_status_label.setText(self.t("session_status_not_started"))
            self.session_status_label.setStyleSheet("color: #555a66;")
        if self.task_input.isEnabled():
            self.task_input.setPlaceholderText(self.t("task_placeholder_enabled"))
        else:
            self.task_input.setPlaceholderText(self.t("task_placeholder_disabled"))

    def change_language(self):
        self.lang = self.lang_selector.currentData()
        if self.state is not None:
            self.state.lang = self.lang
        self.apply_translations()


    def start_session(self):
        project_root = self.project_path_input.text().strip() or None

        write_enabled = self.allow_write_checkbox.isChecked()

        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.state = SessionState(
            project_root=project_root,
            session_id=session_id,
            write_enabled=write_enabled,
            lang=self.lang,
        )

        if not project_root:
            self.chat_history.append(f">> ISKER: {self.t('session_started_no_project')}")
        elif self.allow_write_checkbox.isChecked():
            self.chat_history.append(f">> ISKER: {self.t('session_started_write')}")
            self.chat_history.append(f">> ISKER: {self.t('backup_reminder')}")
        else:
            self.chat_history.append(f">> ISKER: {self.t('session_started_readonly')}")

        self.task_input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.task_input.setPlaceholderText(self.t("task_placeholder_enabled"))
        self.project_path_input.setEnabled(False)
        self.allow_write_checkbox.setEnabled(False)
        self.start_button.setEnabled(False)
        self.new_session_button.setEnabled(True)
        self.apply_translations()

    def reset_session(self):
        if self.state is not None:
            try:
                update_project_summary(self.state)
            except Exception as e:
                self.chat_history.append(
                    f">> ISKER [{self.t('error_label')}]: не удалось сохранить память проекта ({e})"
                )

        self.state = None
        self.chat_history.clear()
        self.chat_history.append(f">> ISKER: {self.t('session_reset')}")

        self.project_path_input.clear()
        self.project_path_input.setEnabled(True)
        self.allow_write_checkbox.setEnabled(True)
        self.allow_write_checkbox.setChecked(False)
        self.start_button.setEnabled(True)
        self.new_session_button.setEnabled(False)

        self.task_input.setEnabled(False)
        self.send_button.setEnabled(False)
        self.task_input.setPlaceholderText(self.t("task_placeholder_disabled"))
        self.apply_translations()

        self.project_path_input.setFocus()

    def _remove_last_line(self):
        """Удаляет последний абзац из истории диалога — используется, чтобы
        убрать временную надпись 'ISKER думает...' перед показом реального ответа."""
        cursor = self.chat_history.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.select(cursor.SelectionType.BlockUnderCursor)
        cursor.removeSelectedText()
        cursor.deletePreviousChar()  # убираем лишний перевод строки после удаления

    def send_task(self):
        task = self.task_input.text().strip()
        if not task or self.state is None:   
            return

        self._thinking_removed = False

        you_label = self.t("you_label")
        self.chat_history.append(f"\n> {you_label}: {task}")
        self.task_input.clear()
        self.task_input.setEnabled(False)
        self.send_button.setEnabled(False)

        thinking_html = (
            '<span style="color:#00fff9;">'
            f'&gt;&gt; ISKER: {self.t("thinking_label")}</span>'
        )
        self.chat_history.append(thinking_html)

# Запускаем агента в фоновом потоке, чтобы матричная анимация не зависала
        self.worker = AgentTaskWorker(self.state, task)
        self.worker.finished.connect(self._on_task_finished)
        self.worker.error.connect(self._on_task_error)
        self.worker.step.connect(self._on_task_step)
        self.cancel_button.setEnabled(True)
        self.worker.start()

    def cancel_task(self):
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.request_cancel()
            self.cancel_button.setEnabled(False)

    def _on_task_step(self, message):
        # Первый live-комментарий заменяет собой надпись "ISKER думает..."
        if not self._thinking_removed:
            self._remove_last_line()
            self._thinking_removed = True
        step_html = (
            '<span style="color:#888888;">'
            f'&gt;&gt; {html.escape(message)}</span>'
        )
        self.chat_history.append(step_html)


    def _on_task_finished(self, answer, changed_files, task_summary):
        self.cancel_button.setEnabled(False)
        if not self._thinking_removed:
            self._remove_last_line()
        self.chat_history.append(f">> ISKER: {answer}")
        if changed_files:
            lines_html = "<br>".join(
                f"&nbsp;&nbsp;• {html.escape(cf['path'])} — {html.escape(cf['status'])}"
                for cf in changed_files
            )
            self.chat_history.append(
                '<table cellpadding="10" cellspacing="0" style="'
                'border: 1px solid #aaff00; border-radius: 6px; '
                'background-color: rgba(14, 20, 8, 180); margin-top: 4px;">'
                '<tr><td>'
                f'<span style="color:#aaff00; font-family:\'{self.font_family}\'; font-size:14px;">'
                f'✏️ Изменённые файлы:<br>{lines_html}</span>'
                '</td></tr></table>'
            )
        if task_summary:
            self.chat_history.append(
                '<table cellpadding="10" cellspacing="0" style="'
                'border: 1px solid #00fff9; border-radius: 6px; '
                'background-color: rgba(8, 16, 20, 180); margin-top: 4px;">'
                '<tr><td>'
                f'<span style="color:#00fff9; font-family:\'{self.font_family}\'; font-size:14px;">'
                f'📋 {self.t("task_summary_label")} {html.escape(task_summary)}</span>'
                '</td></tr></table>'
            )        
        self.task_input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.task_input.setFocus()

    def _on_task_error(self, err_msg):
        if not self._thinking_removed:
            self._remove_last_line()
        error_label = self.t("error_label")
        self.cancel_button.setEnabled(False)
        self.chat_history.append(f">> ISKER [{error_label}]: {err_msg}")
        self.task_input.setEnabled(True)
        self.send_button.setEnabled(True)
        self.task_input.setFocus()

    def closeEvent(self, event):
        if self.state is not None:
            try:
                update_project_summary(self.state)
            except Exception as e:
                logger.error(f"Не удалось сохранить память проекта при закрытии: {e}")
        event.accept()

def _enable_dark_title_bar(window):
    """Включает тёмный заголовок окна на Windows 10/11 через DWM API.
    На других ОС просто ничего не делает (тихо пропускается)."""
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value)
        )
    except Exception:
        pass  # старая версия Windows без поддержки — не критично, просто останется светлый


def main():
    app = QApplication(sys.argv)
    font_family = load_custom_font()  # только после создания QApplication — иначе краш
    app.setStyleSheet(build_cyberpunk_style(font_family))
    icon_path = _resource_path("assets", "icon.png")
    app.setWindowIcon(QIcon(str(icon_path)))
    window = MainWindow()
    window.font_family = font_family
    _enable_dark_title_bar(window)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()