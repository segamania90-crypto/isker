"""
CLI-обвязка для тестирования ядра агента из терминала VS Code.
Desktop-интерфейс на PySide6 — отдельный, более поздний этап (Этап 5).

Использование:
    python main.py

Перед запуском:
    1. Заполни .env по образцу .env.example (хотя бы один провайдер).
    2. Сделай git commit в своём проекте — на случай отката правок агента.
"""

import logging
import sys
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from agent.loop import SessionState, run_task, update_project_summary

import os
from logging.handlers import RotatingFileHandler

_log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)

_formatter = logging.Formatter(
    fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

_file_handler = RotatingFileHandler(
    "isker.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(_formatter)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_formatter)

logging.basicConfig(level=_log_level, handlers=[_file_handler, _console_handler])


def main():
    print("=== AI-агент: рутинные задачи по проекту ===")
    
    first_line = input(
        "Путь к проекту (если нужна запись — добавь ' | ' и файлы через "
        "запятую; без ' | ' = режим только чтения): "
    ).strip()

    if "|" in first_line:
        project_root, allowed_raw = first_line.split("|", 1)
        project_root = project_root.strip()
        allowed_files = {p.strip() for p in allowed_raw.split(",") if p.strip()}
    else:
        project_root = first_line
        allowed_files = set()

    if allowed_files:
        print(f"Разрешены к записи: {sorted(allowed_files)}")
        print("Напоминание: убедись, что перед этой сессией сделан git commit.")
    else:
        print("Режим только чтения (запись файлов недоступна).")

    session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    state = SessionState(
        project_root=project_root,
        session_id=session_id,
        write_enabled=bool(allowed_files),
        allowed_files=allowed_files,
    )

    print("\nВведи задачу (или 'exit' для выхода).")
    while True:
        try:
            task = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nЗавершение.")
            sys.exit(0)

        if task.lower() in ("exit", "quit", "выход", "/exit", "/quit"):
            print("\nОбновляю долгую память проекта...")
            summary = update_project_summary(state)
            print(f"Саммари сохранено:\n{summary}")
            break        

        if not task:
            continue

        try:
                answer, changed_files, task_summary = run_task(state, task)
        except Exception as e:
            import traceback
            traceback.print_exc()
            continue
        print(f"\nАгент: {answer}")
        if changed_files:
            print(f"Изменённые файлы: {changed_files}")


if __name__ == "__main__":
    main()
