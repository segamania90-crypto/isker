"""
Универсальные (языко-агностичные) инструменты для работы с проектом.

Ничего не знает про GDScript, Godot, .tscn и т.п. — просто текст и файлы.
Специфика языка/задачи остаётся на уровне промпта (agent/loop.py), а не
здесь. Так один и тот же код работает и для Godot-проекта, и для сайта.
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
from datetime import datetime
from pathlib import Path     

# Директории, которые никогда не нужно показывать агенту.
# Базовый набор + опциональные дополнения из .env (EXTRA_IGNORE_DIRS,
# через запятую, например: EXTRA_IGNORE_DIRS=.pytest_cache,coverage_html)
# — так пользователь может добавить свои папки под конкретный проект,
# не трогая код.
_EXTRA_IGNORE_DIRS = {
    d.strip() for d in os.getenv("EXTRA_IGNORE_DIRS", "").split(",") if d.strip()
}

DEFAULT_IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", ".godot", "venv", ".venv",
    "dist", "build", ".idea", ".vscode",
} | _EXTRA_IGNORE_DIRS

# Грубая оценка: ~4 символа на токен (усреднённо для латиницы/кода).
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Грубая оценка числа токенов — для решения, влезет ли файл в контекст."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def get_current_datetime(timezone: str | None = None) -> dict:
    """
    Возвращает реальные текущие дату и время.
    Без timezone — время компьютера, на котором запущен агент (как раньше).
    С timezone (например "Europe/Madrid", "Asia/Almaty", "UTC") — время
    в указанном часовом поясе, через стандартный модуль zoneinfo.
    Модель сама по себе не знает текущего момента — без этого инструмента
    она вынуждена выдумывать правдоподобную, но неверную дату.
    """
    if timezone:
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo(timezone))
        except Exception as e:
            return {"error": f"Неизвестный или некорректный часовой пояс '{timezone}': {e}"}
    else:
        now = datetime.now()

    return {
        "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
        "timezone": timezone or "локальное время компьютера",
        "utc_offset": now.strftime("%z") or None,
        "tz_abbreviation": now.strftime("%Z") or None,
    }


def list_tree(root: str, max_depth: int = 6, ignore_dirs: set[str] | None = None, max_files: int = 2000, max_seconds: float = 10) -> dict:
    """
    Строит текстовое дерево файлов проекта, чтобы агент понимал структуру,
    не читая содержимое. Возвращает готовую строку для вставки в промпт.
    """
    ignore_dirs = ignore_dirs or DEFAULT_IGNORE_DIRS
    root_path = Path(root).resolve()
    lines: list[str] = [str(root_path)]

    start_time = time.time()
    file_count = 0
    truncated = False
    reason = None

    def _walk(path: Path, prefix: str, depth: int):
        nonlocal truncated, reason, file_count
        if truncated:
            return
        if depth > max_depth:
            return
        try:
            entries = sorted(
                [p for p in path.iterdir() if p.name not in ignore_dirs and not p.name.startswith(".")],
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except PermissionError:
            return
        for i, entry in enumerate(entries):
            if truncated:
                return
            if time.time() - start_time >= max_seconds:
                truncated = True
                reason = "time_limit"
                return
            connector = "└── " if i == len(entries) - 1 else "├── "
            lines.append(f"{prefix}{connector}{entry.name}")
            if entry.is_dir():
                extension = "    " if i == len(entries) - 1 else "│   "
                _walk(entry, prefix + extension, depth + 1)
            else:
                file_count += 1
                if file_count >= max_files:
                    truncated = True
                    reason = "file_limit"
                    return

    _walk(root_path, "", 1)
    return {
        "content": "\n".join(lines),
        "truncated": truncated,
        "reason": reason,
    }


def search_content(
    root: str,
    pattern: str,
    glob: str = "*",
    max_matches: int = 50,
    regex: bool = False,
    max_files: int = 2000,
    max_seconds: float = 10,
    ignore_dirs: set[str] | None = None,
) -> dict:
    """
    Ищет текст/паттерн по содержимому файлов проекта.
    По умолчанию — простой подстрочный поиск (без учёта регистра);
    если regex=True, pattern трактуется как регулярное выражение.

    Возвращает список {"file": путь, "line": номер, "text": строка}.
    """
    root_path = Path(root).resolve()
    matches: list[dict] = []
    compiled = re.compile(pattern) if regex else None
    ignore_dirs = ignore_dirs or DEFAULT_IGNORE_DIRS

    start_time = time.time()
    file_count = 0
    truncated = False
    reason = None

    for dirpath, dirnames, filenames in os.walk(root_path):
        if truncated:
            break
        if time.time() - start_time >= max_seconds:
            truncated = True
            reason = "time_limit"
            break
        dirnames[:] = [d for d in dirnames if d not in ignore_dirs and not d.startswith(".")]
        for filename in filenames:
            if truncated:
                break
            if time.time() - start_time >= max_seconds:
                truncated = True
                reason = "time_limit"
                break
            if not fnmatch.fnmatch(filename, glob):
                continue
            file_count += 1
            if file_count >= max_files:
                truncated = True
                reason = "file_limit"
                break
            filepath = Path(dirpath) / filename
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, start=1):
                        hit = compiled.search(line) if compiled else (pattern.lower() in line.lower())
                        if hit:
                            matches.append({
                                "file": str(filepath.relative_to(root_path)),
                                "line": lineno,
                                "text": line.strip(),
                            })
                            if len(matches) >= max_matches:
                                return {
                                    "matches": matches,
                                    "truncated": truncated,
                                    "reason": reason,
                                }
            except (UnicodeDecodeError, OSError):
                continue

    return {
        "matches": matches,
        "truncated": truncated,
        "reason": reason,
    }


def read_file(root: str, relative_path: str, max_chars: int = 20000, offset: int = 0) -> dict:
    """
    Читает файл по относительному пути внутри проекта, начиная с символа offset.
    Если файл не помещается целиком в max_chars начиная с offset — возвращает
    truncated=True и next_offset, чтобы агент мог запросить следующий кусок
    повторным вызовом read_file с offset=next_offset. Так большие файлы читаются
    по частям, а не обрезаются молча.
    """
    full_path = (Path(root) / relative_path).resolve()
    if not str(full_path).startswith(str(Path(root).resolve())):
        raise ValueError("Путь выходит за пределы разрешённого проекта")
    if not full_path.exists():
        raise FileNotFoundError(f"Файл не найден: {relative_path}")

    full_content = full_path.read_text(encoding="utf-8", errors="ignore")
    total_chars = len(full_content)

    content = full_content[offset:offset + max_chars]
    truncated = offset + max_chars < total_chars

    return {
        "path": relative_path,
        "content": content,
        "truncated": truncated,
        "offset": offset,
        "total_chars": total_chars,
        "next_offset": offset + max_chars if truncated else None,
        "estimated_tokens": estimate_tokens(content),
    }

def _resolve_existing_sibling(root: str, relative_path: str) -> str:
    """
    Если relative_path не имеет расширения и точно такого файла нет,
    но в той же папке проекта есть ровно один файл с таким же именем и
    любым расширением (напр. запросили "font_test", а есть "font_test.txt") —
    возвращает путь к найденному файлу вместо создания дубликата с другим
    "пустым" именем. Если совпадений нет или их несколько — возвращает
    relative_path без изменений (создастся/будет использован как есть).
    """
    target = Path(root) / relative_path
    if target.suffix or target.exists():
        return relative_path  # расширение уже указано, или файл точно существует — ничего не трогаем

    parent = target.parent
    if not parent.is_dir():
        return relative_path

    matches = [p for p in parent.iterdir() if p.is_file() and p.stem == target.name]
    if len(matches) == 1:
        return str(matches[0].relative_to(Path(root).resolve()))
    return relative_path


def write_file(root: str, relative_path: str, content: str, write_enabled: bool = False) -> dict:
    """
    Записывает содержимое в файл в рамках разрешённой сессии.
    write_enabled — общий тумблер на запись/изменение файлов проекта,
    выставляется один раз при старте сессии (start_session/reset_session).
    Если False — запись в любой файл запрещена, независимо от пути.

    Примечание: это сознательное архитектурное решение — отказ от
    ограничения по конкретным файлам в пользу общего разрешения на запись.
    Защита теперь не "какие файлы можно трогать", а "можно трогать
    файлы вообще, в этой сессии, или нет".
    """
    if not write_enabled:
        raise PermissionError(
            "Запись в файлы проекта не разрешена в этой сессии "
            "(write_enabled=False). Включите разрешение на запись при старте сессии."
        )

    relative_path = _resolve_existing_sibling(root, relative_path)

    full_path = (Path(root) / relative_path).resolve()
    if not str(full_path).startswith(str(Path(root).resolve())):
        raise ValueError("Путь выходит за пределы разрешённого проекта")

    full_path.parent.mkdir(parents=True, exist_ok=True)
    existed = full_path.exists()
    old_content = full_path.read_text(encoding="utf-8", errors="ignore") if existed else ""

    tmp_path = full_path.with_suffix(full_path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, full_path)

    return {
        "path": relative_path,
        "created": not existed,
        "bytes_written": len(content.encode("utf-8")),
        "lines_before": old_content.count("\n") + 1 if existed else 0,
        "lines_after": content.count("\n") + 1,
    }
