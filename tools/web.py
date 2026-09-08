"""
Чтение ОДНОЙ веб-страницы по прямой ссылке, заданной пользователем явно.

Сознательно НЕ массовый парсинг/обход сайтов (см. roadmap, раздел
"НЕ делать: массовый парсинг/скрейпинг сайтов") — только одна страница
по одному URL за вызов, без перехода по ссылкам внутри неё.
"""

from __future__ import annotations

import re

import requests

from tools.filesystem import estimate_tokens

MAX_PAGE_CHARS = 20000
REQUEST_TIMEOUT = 10  # секунд


def _strip_html(html: str) -> str:
    """Грубая, но достаточная очистка HTML до читаемого текста, без внешних зависимостей."""
    html = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def read_web_page(url: str, max_chars: int = MAX_PAGE_CHARS) -> dict:
    """
    Скачивает и возвращает читаемый текст одной страницы по явно заданному URL.
    Обрезает слишком большие страницы, явно сообщая об этом (как read_file).
    """
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return {"error": f"Некорректный URL (должен начинаться с http:// или https://): {url}"}

    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "ISKER-agent/1.0"},
        )
        response.raise_for_status()
    except requests.RequestException as e:
        return {"error": f"Не удалось получить страницу {url}: {e}"}

    content_type = response.headers.get("Content-Type", "")
    if "html" in content_type.lower():
        text = _strip_html(response.text)
    else:
        text = response.text

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]

    return {
        "url": url,
        "content": text,
        "truncated": truncated,
        "estimated_tokens": estimate_tokens(text),
    }
