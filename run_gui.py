"""
Точка входа для сборки GUI-версии ISKER в .exe через PyInstaller.
Не редактировать логику здесь — вся реальная логика в ui/app.py,
этот файл только правильно её запускает.
"""

import runpy

if __name__ == "__main__":
    runpy.run_module("ui.app", run_name="__main__")