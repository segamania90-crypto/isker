"""
Базовый цикл агента (Этап 1 + Этап 2 roadmap).

Цикл: запрос -> модель решает, нужен ли инструмент -> вызов инструмента
-> результат возвращается модели -> ... -> финальный ответ.

Вызов инструментов реализован через "structured JSON in text", а не через
нативный function-calling провайдера — чтобы не зависеть от того, какие
именно бесплатные модели у какого провайдера умеют tool-calling. Модель
инструктируется всегда отвечать одним JSON-объектом одного из двух видов:

    {"action": "tool", "tool": "<имя>", "args": {...}}
    {"action": "final", "content": "<ответ пользователю>"}

Это и есть слой нормализации, который делает поведение агента одинаковым
независимо от провайдера/модели, ответившей в конкретный момент.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

from pathlib import Path

from agent.llm import call_llm
from tools import filesystem as fs
from tools import web as web_tools
from agent.memory import Memory


logger = logging.getLogger("agent.loop")

MAX_STEPS = 15  # защита от зацикливания

# Этап 4: управление контекстом под лимиты бесплатных провайдеров.
# CONTEXT_TOKEN_LIMIT — консервативный бюджет на один запрос (можно менять в .env,
# ориентируясь на самый тесный лимит в цепочке провайдеров).
# COMPRESSION_THRESHOLD — сжимаем историю, только когда реальном приблизились к лимиту,
# а не на каждом шаге (экономим отдельный вызов LLM на сжатие).
CONTEXT_TOKEN_LIMIT = int(os.getenv("CONTEXT_TOKEN_LIMIT", "6000"))
COMPRESSION_THRESHOLD = 0.7

# Пункт 3 аудита: технические сообщения об ошибках заменяются на понятные
# пользователю. Общий словарь для всей run_task — используется и при
# обычном "final" ответе модели, и в except-блоке сбоя всего цикла.
# Временное решение только для русского языка.
# TODO: в будущем учитывать self.lang из UI для перевода на en/es.
# Пункт 3 аудита: технические сообщения об ошибках заменяются на понятные
# пользователю. Ключи словарей — русские фразы, потому что сами исходные
# сообщения об ошибках в коде проекта написаны на русском (см. filesystem.py,
# loop.py) — это не меняется. Меняется только язык понятного текста (значения),
# в зависимости от state.lang.
_ERROR_PATTERNS_RU = {
    "api_key не задан": "Не настроен доступ к одному из провайдеров ИИ. Проверьте файл .env — там должны быть заданы ключи API.",
    "rate limit": "Сейчас все провайдеры ИИ перегружены или недоступны. Попробуйте повторить запрос через несколько минут.",
    "провайдеры отказали": "Сейчас все провайдеры ИИ перегружены или недоступны. Попробуйте повторить запрос через несколько минут.",
    "не удалось получить страницу": "Не получилось загрузить указанную веб-страницу. Проверьте, что ссылка верна и страница доступна.",
    "файл не найден": "Указанный файл не найден в проекте. Проверьте путь и повторите запрос.",
    "путь выходит за пределы": "Запрошенный файл находится за пределами разрешённой папки проекта.",
    "запись в файлы проекта не разрешена": "Запись в файлы не разрешена в этой сессии. Включите разрешение на запись при старте сессии.",
    "папка не найдена": "Указанная папка не найдена в проекте. Проверьте путь и повторите запрос.",
    "это файл, а не папка": "По этому пути находится файл, а не папка. Для удаления файла используйте обычное удаление файла.",
    "нельзя удалить корневую папку проекта": "Нельзя удалить корневую папку проекта целиком — это защита от случайного уничтожения всего проекта.",
    "уже существует файл (не папка)": "По этому пути уже есть файл с таким именем — нельзя создать папку с тем же именем.",
}

_ERROR_PATTERNS_EN = {
    "api_key не задан": "Access to one of the AI providers is not configured. Check the .env file — the API keys must be set there.",
    "rate limit": "All AI providers are currently overloaded or unavailable. Please try again in a few minutes.",
    "провайдеры отказали": "All AI providers are currently overloaded or unavailable. Please try again in a few minutes.",
    "не удалось получить страницу": "Could not load the specified web page. Check that the link is correct and the page is accessible.",
    "файл не найден": "The specified file was not found in the project. Check the path and try again.",
    "путь выходит за пределы": "The requested file is outside the allowed project folder.",
    "запись в файлы проекта не разрешена": "Writing to files is not allowed in this session. Enable write access at session start.",
    "папка не найдена": "The specified folder was not found in the project. Check the path and try again.",
    "это файл, а не папка": "That path points to a file, not a folder. Use regular file deletion for a single file.",
    "нельзя удалить корневую папку проекта": "You can't delete the project's root folder entirely — this protects against wiping out the whole project by accident.",
    "уже существует файл (не папка)": "A file with that name already exists at this path — can't create a folder with the same name.",
}

_ERROR_PATTERNS_ES = {
    "api_key не задан": "No se ha configurado el acceso a uno de los proveedores de IA. Revisa el archivo .env — allí deben estar las claves API.",
    "rate limit": "Todos los proveedores de IA están sobrecargados o no disponibles ahora mismo. Inténtalo de nuevo en unos minutos.",
    "провайдеры отказали": "Todos los proveedores de IA están sobrecargados o no disponibles ahora mismo. Inténtalo de nuevo en unos minutos.",
    "не удалось получить страницу": "No se pudo cargar la página web indicada. Comprueba que el enlace es correcto y la página está accesible.",
    "файл не найден": "No se encontró el archivo indicado en el proyecto. Comprueba la ruta e inténtalo de nuevo.",
    "путь выходит за пределы": "El archivo solicitado está fuera de la carpeta del proyecto permitida.",
    "запись в файлы проекта не разрешена": "No se permite escribir archivos en esta sesión. Habilita el permiso de escritura al iniciar la sesión.",
    "папка не найдена": "No se encontró la carpeta indicada en el proyecto. Comprueba la ruta e inténtalo de nuevo.",
    "это файл, а не папка": "Esa ruta apunta a un archivo, no a una carpeta. Usa la eliminación normal de archivos para un solo archivo.",
    "нельзя удалить корневую папку проекта": "No se puede eliminar la carpeta raíz del proyecto por completo — es una protección contra el borrado accidental de todo el proyecto.",
    "уже существует файл (не папка)": "Ya existe un archivo con ese nombre en esa ruta — no se puede crear una carpeta con el mismo nombre.",
}

_ERROR_PATTERNS_BY_LANG = {
    "ru": _ERROR_PATTERNS_RU,
    "en": _ERROR_PATTERNS_EN,
    "es": _ERROR_PATTERNS_ES,
}



_TOOL_DESCRIPTIONS_BY_LANG = {
    "ru": {
        "list_tree": "📂 Смотрю структуру проекта...",
        "search_content": "🔍 Ищу «{pattern}» по проекту...",
        "read_file": "📖 Читаю файл {path}...",
        "write_file": "✏️ Записываю файл {path}...",
        "create_folder": "📁 Создаю папку {relative_path}...",
        "move_file": "📦 Перемещаю {source_path} → {destination_path}...",
        "delete_file": "🗑️ Удаляю файл {relative_path}...",
        "delete_folder": "🗑️ Удаляю папку {relative_path} со всем содержимым...",
        "get_current_datetime": "🕒 Узнаю текущую дату и время...",
        "read_web_page": "🌐 Загружаю страницу {url}...",
    },
    "en": {
        "list_tree": "📂 Looking at the project structure...",
        "search_content": "🔍 Searching for \"{pattern}\" in the project...",
        "read_file": "📖 Reading file {path}...",
        "write_file": "✏️ Writing file {path}...",
        "create_folder": "📁 Creating folder {relative_path}...",
        "move_file": "📦 Moving {source_path} → {destination_path}...",
        "delete_file": "🗑️ Deleting file {relative_path}...",
        "delete_folder": "🗑️ Deleting folder {relative_path} and its contents...",
        "get_current_datetime": "🕒 Getting the current date and time...",
        "read_web_page": "🌐 Loading page {url}...",
    },
    "es": {
        "list_tree": "📂 Revisando la estructura del proyecto...",
        "search_content": "🔍 Buscando «{pattern}» en el proyecto...",
        "read_file": "📖 Leyendo el archivo {path}...",
        "write_file": "✏️ Escribiendo el archivo {path}...",
        "create_folder": "📁 Creando la carpeta {relative_path}...",
        "move_file": "📦 Moviendo {source_path} → {destination_path}...",
        "delete_file": "🗑️ Eliminando el archivo {relative_path}...",
        "delete_folder": "🗑️ Eliminando la carpeta {relative_path} y su contenido...",
        "get_current_datetime": "🕒 Consultando la fecha y hora actuales...",
        "read_web_page": "🌐 Cargando la página {url}...",
    },
}


def _describe_tool_call(tool: str, args: dict, lang: str = "ru") -> str:
    """Человеко-понятная фраза для live-комментария в UI перед вызовом инструмента.
    Если инструмент неизвестен или аргумент отсутствует — возвращает нейтральный текст."""
    templates = _TOOL_DESCRIPTIONS_BY_LANG.get(lang, _TOOL_DESCRIPTIONS_BY_LANG["ru"])
    template = templates.get(tool)
    if not template:
        return tool
    try:
        return template.format(**args)
    except KeyError:
        return template.split("{")[0].strip()


def _match_known_error(text: str, lang: str = "ru") -> str | None:
    """Проверяет, совпадает ли text с одним из известных паттернов ошибок.
    Возвращает готовый переведённый текст, либо None, если совпадений нет."""
    patterns = _ERROR_PATTERNS_BY_LANG.get(lang, _ERROR_PATTERNS_RU)
    lowered = text.lower()
    for pattern, friendly in patterns.items():
        if pattern in lowered:
            logger.warning("Техническая ошибка скрыта от пользователя: %s", text)
            return friendly
    return None


def _friendlify_error(text: str, lang: str = "ru") -> str:
    """Заменяет известный технический текст ошибки на понятный пользователю,
    на языке lang (ru/en/es). Если совпадений нет — возвращает text без изменений."""
    return _match_known_error(text, lang) or text


SYSTEM_PROMPT_TEMPLATE = """Ты — AI-агент, ускоряющий рутинную работу разработчика в проекте.
Проект языко-агностичен: код может быть на любом языке (GDScript, Python, JS и т.д.) —
ты уже умеешь их понимать, отдельных инструкций по языку не требуется.

{language_instruction}

Твоя основная специализация — работа с кодом и файлами ЭТОГО проекта (чтение,
поиск, редактирование, рефакторинг, баги, тесты, документация) — для этого
используй инструменты. Но если пользователь задаёт общий вопрос не по коду
проекта (например, "как сделать сайт", общий совет по программированию,
объяснение концепции) — отвечай на него сам, по существу, обычным текстом
через action="final", как обычный полезный ассистент. Не отказывай и не
отправляй к другим LLM без необходимости — делай это только если вопрос
явно требует доступа к файлам/данным, которых у тебя нет в рамках этой
сессии (например, "открой файл X", когда проект не подключён).

КРИТИЧЕСКИ ВАЖНО — ЧЕСТНОСТЬ О СВОИХ ВОЗМОЖНОСТЯХ: у тебя есть ТОЛЬКО те
инструменты, что перечислены ниже в разделе про инструменты. Если пользователь
просит сделать что-то, для чего инструмента НЕТ (например, выполнить код,
установить пакет, сделать git commit, работать с git вообще) —
НИКОГДА не притворяйся, что выполнил это, и не выдумывай результат (ни
"успешно удалено", ни "файл не найден", если ты это не проверял). Вместо
этого через action="final" честно скажи пользователю, что у тебя нет
инструмента для этого конкретного действия, и что действительно доступно
(например: "У меня нет инструмента для удаления файлов — я могу только
читать, искать и записывать файлы. Удали файл вручную."). Ложь о выполненном
действии недопустима ни при каких обстоятельствах.

КРИТИЧЕСКИ ВАЖНО — НЕ УГАДЫВАЙ РЕЗУЛЬТАТ ИНСТРУМЕНТА ПОСЛЕ СБОЯ: если где-то
раньше в этом диалоге была ошибка или сбой при попытке вызвать инструмент
(любое сообщение о технической проблеме, отказе, недоступности), НИКОГДА не
пытайся сам "правдоподобно" угадать, каким мог бы быть результат этого
вызова, опираясь только на контекст разговора. Ты ОБЯЗАН вызвать этот
инструмент заново и дождаться его реального результата, прежде чем давать
финальный ответ через action="final". Формулировать предположение вместо
проверенного факта недопустимо, даже если предположение кажется правдоподобным.

КРИТИЧЕСКИ ВАЖНО — ДАТА И ВРЕМЯ: при ЛЮБОМ вопросе, где нужна дата, день
недели или время (в том числе в конкретном городе/стране/часовом поясе),
ты ОБЯЗАН вызвать инструмент get_current_datetime — даже если тебе
кажется, что ты знаешь ответ. Отвечать на такие вопросы без вызова
инструмента запрещено. При указании часового пояса используй в ответе
только значения utc_offset/tz_abbreviation из результата инструмента,
никогда не придумывай их сам.


КРИТИЧЕСКИ ВАЖНО — УТОЧНЕНИЕ ПРИ НЕЯСНОЙ ЗАДАЧЕ: если формулировка
пользователя недостаточно конкретна, чтобы точно понять, что именно
он хочет (например, непонятно какой файл имеется в виду, что именно
написать, заменить содержимое или дописать, какое значение подставить
и т.п.) — НЕ пытайся угадать и НЕ выполняй действие "как получится".
Вместо этого через action="final" задай пользователю короткий уточняющий
вопрос по существу. Выполняй задачу сразу, без вопросов, только если
формулировка достаточно ясна для однозначного действия.

Память о проекте с прошлых сессий:
{project_memory}

ВАЖНО: НЕ используй встроенный (нативный) механизм вызова функций/инструментов модели,
даже если он у тебя есть. Отвечай ТОЛЬКО обычным текстом в формате, описанном ниже.
Любой ответ не в виде обычного текстового JSON будет считаться ошибкой.

{tools_section}

ВАЖНО: отвечай СТРОГО одним JSON-объектом как обычным текстом, без какого-либо
текста вокруг и без использования нативного tool-calling:
  Чтобы вызвать инструмент:
    {{"action": "tool", "tool": "<имя_инструмента>", "args": {{...}}}}
  Чтобы дать финальный ответ пользователю (когда задача решена):
    {{"action": "final", "content": "<текст отчёта о том, что сделано>"}}

Никогда не смешивай эти два формата. Никогда не оборачивай JSON в markdown ```.
"""


@dataclass
class SessionState:
    project_root: str | None
    session_id: str
    write_enabled: bool = False
    lang: str = "ru"
    allowed_files: set[str] = field(default_factory=set)  # список файлов, разрешённых к записи; пусто = ограничений нет
    history: list[dict] = field(default_factory=list)  # короткая память сессии
    memory: Memory = field(default_factory=Memory)
    task_cache: dict[str, tuple[str, list[dict]]] = field(default_factory=dict)
    # Кэш ответов в рамках текущей сессии: точный текст задачи -> (ответ, изменённые файлы).
    # Не сохраняется между сессиями (обнуляется при "Новая сессия") — сознательно просто,
    # чтобы не отдавать устаревший ответ, если файлы проекта успели измениться.

def _pluralize_lines(n: int) -> str:
    """Русское склонение слова 'строка' под число n."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} строка"
    if 2 <= n % 10 <= 4 and not (12 <= n % 100 <= 14):
        return f"{n} строки"
    return f"{n} строк"


def _project_id(project_root: str) -> str:
    """Уникальный идентификатор проекта для разделения памяти между проектами."""
    return str(Path(project_root).resolve())


def _execute_tool(state: SessionState, tool: str, args: dict) -> dict:
    if tool == "get_current_datetime":
        return {"result": fs.get_current_datetime(timezone=args.get("timezone"))}
    if tool == "read_web_page":
        return {"result": web_tools.read_web_page(args["url"])}
    if not state.project_root:
        raise ValueError("Проект не открыт — инструменты работы с файлами недоступны в этой сессии.")
    if tool == "list_tree":
        return {"result": fs.list_tree(state.project_root)}
    if tool == "search_content":
        return {"result": fs.search_content(
            state.project_root,
            pattern=args["pattern"],
            glob=args.get("glob", "*"),
            regex=args.get("regex", False),
        )}
    if tool == "read_file":
        offset = args.get("offset", 0)
        return {"result": fs.read_file(state.project_root, args["path"], offset=offset)}
    if tool == "write_file":
        target_path = args["path"]
        if state.allowed_files and target_path not in state.allowed_files:
            raise PermissionError(
                f"Запись в файл '{target_path}' не разрешена в этой сессии. "
                f"Разрешены к записи только: {sorted(state.allowed_files)}."
            )
        return {"result": fs.write_file(
            state.project_root, target_path, args["content"], write_enabled=state.write_enabled
        )}
    if tool == "create_folder":
        return {"result": fs.create_folder(
            state.project_root, args["relative_path"], write_enabled=state.write_enabled
        )}
    if tool == "move_file":
        return {"result": fs.move_file(
            state.project_root, args["source_path"], args["destination_path"], write_enabled=state.write_enabled
        )}
    if tool == "delete_file":
        return {"result": fs.delete_file(
            state.project_root, args["relative_path"], write_enabled=state.write_enabled
        )}
    if tool == "delete_folder":
        return {"result": fs.delete_folder(
            state.project_root, args["relative_path"], write_enabled=state.write_enabled
        )}
    raise ValueError(f"Неизвестный инструмент: {tool}")


def _parse_model_json(raw_text: str) -> dict:
    """Модель иногда всё равно оборачивает JSON в ```; подчищаем перед парсингом."""
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

def _trim_context_if_needed(messages: list[dict]) -> list[dict]:
    """
    Если суммарный размер сообщений приближается к лимиту контекста —
    сжимает старую часть истории (кроме system prompt и нескольких последних
    сообщений) в короткое саммари через дешёвую "fast" модель.
    Иначе возвращает messages без изменений — не тратим лишний вызов LLM.
    """
    total_tokens = sum(fs.estimate_tokens(m["content"]) for m in messages)
    if total_tokens <= CONTEXT_TOKEN_LIMIT * COMPRESSION_THRESHOLD:
        return messages

    tail_keep = 4  # последние сообщения оставляем как есть — они самые актуальные
    if len(messages) <= tail_keep + 2:
        return messages  # сжимать особо нечего

    system_msg = messages[0]
    head = messages[1:-tail_keep]
    tail = messages[-tail_keep:]

    transcript = "\n".join(f"{m['role']}: {m['content'][:2000]}" for m in head)
    prompt = (
        "Сожми часть истории диалога AI-агента в короткое саммари (5-10 предложений).\n"
        "Сохрани ключевые факты: что искали, какие файлы читали или меняли, "
        "к каким выводам пришли. Обычный текст, без JSON.\n\n"
        f"{transcript}"
    )
    response = call_llm([{"role": "user", "content": prompt}], tier="fast")
    summary_msg = {"role": "user", "content": f"[Сжатая история предыдущих шагов]: {response.text}"}

    new_messages = [system_msg, summary_msg] + tail
    logger.info(
        "Контекст сжат: было %s сообщений (~%s ток.), стало %s сообщений",
        len(messages), total_tokens, len(new_messages),
    )
    return new_messages


_LANG_NAMES = {"ru": "русском", "en": "английском (English)", "es": "испанском (español)"}


def run_task(state: SessionState, user_task: str, on_step=None, cancel_check=None) -> tuple[str, list[dict]]:

    """
    Запускает цикл агента для одной задачи пользователя в рамках сессии.
    Возвращает (финальный текстовый ответ, список изменённых файлов).
    Каждый элемент списка изменённых файлов: {"path": str, "status": str}.
    Все шаги пишутся в state.history.
    """
    import re

    lang_name = _LANG_NAMES.get(state.lang, _LANG_NAMES["ru"])
    language_instruction = (
        f"КРИТИЧЕСКИ ВАЖНО — ЯЗЫК ОТВЕТА: всегда отвечай пользователю (в поле "
        f'"content" финального ответа) СТРОГО на {lang_name} языке — независимо '
        f"от того, на каком языке написан сам вопрос пользователя. Язык ответа "
        f"определяется настройкой интерфейса, а не текстом вопроса."
    )
    cache_key = re.sub(r'[.,!?;:]+$', '', user_task.strip().lower())
    cache_key = re.sub(r'\s+', ' ', cache_key)
    if cache_key in state.task_cache:
        logger.info("Кэш: точное совпадение задачи, LLM не вызывается")
        cached_text, cached_changed_files = state.task_cache[cache_key]
        state.history.append({"role": "user", "content": user_task})
        state.history.append({"role": "assistant", "content": cached_text})
        state.memory.log_turn(state.session_id, "user", user_task)
        state.memory.log_turn(state.session_id, "assistant", cached_text)
        return cached_text, cached_changed_files, None

    changed_files: list[dict] = []
    # Пункт 5.2: короткий журнал шагов ЭТОЙ задачи (не всей сессии) — нужен
    # только для последующего резюме через summarize_task(). Хранит краткую,
    # обрезанную версию результата каждого инструмента, а не полный текст —
    # чтобы доп. вызов LLM на резюме был дешёвым.
    task_log: list[dict] = []

    facts = state.memory.get_all_facts(_project_id(state.project_root)) if state.project_root else {}
    project_memory_text = "\n".join(f"- {k}: {v}" for k, v in facts.items()) or "(пока пусто, это первая сессия)"

    if state.project_root:
                tools_section = (
            f"Запись/изменение файлов в этой сессии: {'разрешена' if state.write_enabled else 'запрещена (только чтение и анализ)'}\n\n"
            "Тебе доступны следующие инструменты:\n"
            "- list_tree() -> дерево файлов проекта\n"
            "- search_content(pattern, glob=\"*\", regex=false) -> найти текст по проекту\n"
            "- read_file(path, offset=0) -> прочитать содержимое файла. Если в результате "
            "\"truncated\": true — файл не поместился целиком; вызови read_file снова с тем же "
            "path и offset, равным полученному \"next_offset\", чтобы прочитать следующую часть. "
            "Повторяй, пока \"truncated\" не станет false, чтобы увидеть файл целиком.\n"
            "- write_file(path, content) -> записать файл (доступно только если запись разрешена в этой сессии). "
            "Если промежуточных папок по пути path ещё не существует, они создаются автоматически — "
            "можно сразу записать файл по вложенному пути вроде \"new_folder/sub/file.txt\" одним "
            "вызовом, без отдельного создания папок.\n"
            "- create_folder(relative_path) -> создать ПУСТУЮ папку (и промежуточные папки по пути), "
            "ничего не перемещая и не создавая никаких файлов внутри. Используй именно этот инструмент, "
            "если пользователь просит просто “создай папку X” / “сделай новую папку”, "
            "без упоминания какого-либо файла, который нужно туда положить. "
            "(доступно только если запись разрешена в этой сессии)\n"
            "- move_file(source_path, destination_path) -> переместить или переименовать файл внутри проекта. "
            "Если папок по пути destination_path ещё не существует, они создаются автоматически — поэтому "
            "если пользователь просит “открой новую папку X и перемести туда файл Y” (явно называя "
            "и папку, и файл), это делается ОДНИМ вызовом move_file(source_path=“Y”, "
            "destination_path=“X/Y”). "
            "КРИТИЧЕСКИ ВАЖНО: никогда не вызывай move_file по собственной инициативе, чтобы “как-то” "
            "создать папку, если пользователь НЕ назвал явно, какой файл нужно переместить. Если он просит "
            "только создать папку — используй create_folder, а не move_file с каким-то произвольно "
            "выбранным файлом (например, последним упомянутым в разговоре) — это самовольное действие, "
            "которое пользователь не запрашивал, и оно недопустимо. "
            "source_path — относительный путь исходного файла, destination_path — относительный путь назначения. "
            "(доступно только если запись разрешена в этой сессии)\n"
            "- delete_file(relative_path) -> удалить ОДИН файл внутри проекта. Работает только с файлами. "
            "relative_path — относительный путь удаляемого файла. "
            "(доступно только если запись разрешена в этой сессии)\n"
            "- delete_folder(relative_path) -> удалить ПАПКУ целиком вместе со всем её содержимым "
            "(вложенные файлы и подпапки удаляются рекурсивно, без возможности восстановления). "
            "relative_path — относительный путь удаляемой папки. Нельзя удалить корень проекта. "
            "Используй именно этот инструмент, если пользователь просит удалить папку, а не delete_file. "
            "(доступно только если запись разрешена в этой сессии)\n"
            "- get_current_datetime(timezone=null) -> реальные текущие дата и время. "
            "Без timezone — время компьютера пользователя. С timezone (IANA-формат, например "
            "\"Europe/Madrid\", \"Asia/Almaty\", \"UTC\") — время в этом часовом поясе. "
            "Если пользователь спрашивает время/дату конкретного города или страны — определи "
            "подходящий IANA-часовой пояс сам и передай его. Если не уверен в поясе — используй "
            "action=\"final\" и уточни у пользователя, вместо угадывания. "



            "Никогда не выдумывай дату/время самостоятельно, у тебя нет своих встроенных часов.\n"
            "- read_web_page(url) ->/download and read the content of a SINGLE web page by direct URL. "
            "Use ONLY if the user explicitly provided a URL (e.g., a link to documentation). Never call this tool on your own initiative without an explicit user-provided URL, and never try to traverse the site via internal links — only one page at a time."
        )
    else:
        tools_section = (
            "Проект сейчас НЕ открыт. Инструменты работы с файлами (list_tree, search_content, "
            "read_file, write_file, create_folder, move_file, delete_file, delete_folder) недоступны — не пытайся их вызывать, для вопросов о коде "
            "проекта сразу отвечай action=\"final\" текстом.\n\n"
            "Однако тебе доступен инструмент get_current_datetime(timezone=null) -> реальные "
            "текущие дата и время. Без timezone — время компьютера пользователя. С timezone "
            "(IANA-формат, например \"Europe/Madrid\", \"Asia/Almaty\", \"UTC\") — время в этом "
            "часовом поясе. Если пользователь спрашивает время/дату конкретного города или "
            "страны — определи подходящий IANA-часовой пояс сам и передай его. Если не уверен "
            "в поясе — используй action=\"final\" и уточни у пользователя, вместо угадывания. "
            "Никогда не выдумывай дату/время самостоятельно, у тебя нет своих встроенных часов.\n"
            "- read_web_page(url) ->/download and read the content of a SINGLE web page by direct URL. "
            "Use ONLY if the user explicitly provided a URL (e.g., a link to documentation). Never call this tool on your own initiative without an explicit user-provided URL, and never try to traverse the site via internal links — only one page at a time."
        )

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        tools_section=tools_section,
        project_memory=project_memory_text,
        language_instruction=language_instruction,
    )
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(state.history)
    messages.append({"role": "user", "content": user_task})

    failed_parse_count = 0
    try:
        for step in range(MAX_STEPS):
            if cancel_check and cancel_check():
                cancel_text = "Задача отменена пользователем."
                if changed_files:
                    files_list = "\n".join(f"- {f['path']} ({f['status']})" for f in changed_files)
                    cancel_text += f"\n\nУспешно применены следующие файлы (откат не выполнялся):\n{files_list}"
                return cancel_text, changed_files, None
            # Простой шаг (решить, что делать дальше) идёт на "fast" модель;
            # если пользователь явно просит крупный рефакторинг/генерацию — "strong".
            tier = "strong" if any(k in user_task.lower() for k in ("рефактор", "перепиши", "архитектур")) else "fast"

            messages = _trim_context_if_needed(messages)

            response = call_llm(messages, tier=tier)
            logger.info("Шаг %s | provider=%s model=%s", step, response.provider, response.model)

            try:
                decision = _parse_model_json(response.text)
                failed_parse_count = 0
            except json.JSONDecodeError:
                failed_parse_count += 1
                if failed_parse_count >= 3:
                    error_text = "Модель вернула некорректный ответ несколько раз подряд, попробуйте переформулировать задачу или сменить модель"
                    return error_text, changed_files, None
                # Модель не выдержала формат — просим её исправиться, не падаем сразу.
                messages.append({"role": "assistant", "content": response.text})
                messages.append({
                    "role": "user",
                    "content": "Твой ответ не был валидным JSON. Ответь строго одним JSON-объектом, как описано в системном промпте.",
                })
                continue

            messages.append({"role": "assistant", "content": response.text})

            if decision.get("action") == "final":
                final_text = decision.get("content", "")
                final_text = _friendlify_error(final_text, state.lang)
                state.history.append({"role": "user", "content": user_task})
                state.history.append({"role": "assistant", "content": final_text})
                state.memory.log_turn(state.session_id, "user", user_task)
                state.memory.log_turn(state.session_id, "assistant", final_text)
                state.task_cache[cache_key] = (final_text, changed_files)
                task_summary = summarize_task(state, user_task, task_log, final_text) if task_log else None
                return final_text, changed_files, task_summary

            if decision.get("action") == "tool":
                tool_name = decision.get("tool")
                args = decision.get("args", {})
                if on_step:
                    on_step(f"[{step + 1}/{MAX_STEPS}] " + _describe_tool_call(tool_name, args, state.lang))
                    
                try:
                    tool_result = _execute_tool(state, tool_name, args)
                except Exception as e:
                    error_str = str(e)
                    logger.warning("Инструмент %s упал: %s", tool_name, e)
                    known_friendly = _match_known_error(error_str, state.lang)
                    if known_friendly:
                        # Известная ошибка — не отдаём модели на пересказ, чтобы
                        # текст и язык ответа не зависели от того, как модель
                        # решит сформулировать. Сразу завершаем задачу.
                        state.history.append({"role": "user", "content": user_task})
                        state.history.append({"role": "assistant", "content": known_friendly})
                        state.memory.log_turn(state.session_id, "user", user_task)
                        state.memory.log_turn(state.session_id, "assistant", known_friendly)
                        state.task_cache[cache_key] = (known_friendly, changed_files)
                        return known_friendly, changed_files, None
                    # Незнакомая ошибка — отдаём модели, пусть сформулирует сама.
                    tool_result = {"error": error_str}

                # Автоматическое дочитывание read_file при обрезке
                if tool_name == "read_file" and isinstance(tool_result.get("result"), dict):
                    res = tool_result["result"]
                    if res.get("truncated") or "next_offset" in res:
                        parts = [res.get("content", "")]
                        offset = res.get("next_offset")
                        path_arg = args.get("path")
                        truncated_final = True
                        # Максимум 20 частей всего (первая уже получена)
                        for _ in range(19):
                            if offset is None:
                                truncated_final = False
                                break
                            try:
                                part = _execute_tool(state, "read_file", {"path": path_arg, "offset": offset})
                            except Exception as e:
                                logger.warning("Ошибка дочитывания read_file: %s", e)
                                break
                            if not isinstance(part.get("result"), dict):
                                break
                            part_res = part["result"]
                            parts.append(part_res.get("content", ""))
                            if not part_res.get("truncated") and "next_offset" not in part_res:
                                offset = None
                                truncated_final = False
                                break
                            offset = part_res.get("next_offset")
                        full_text = "".join(parts)
                        if truncated_final:
                            full_text += "\n\n[ВНИМАНИЕ: файл мог быть прочитан не полностью — достигнут лимит повторов чтения (20 частей).]"
                        combined_res = {
                            "path": res.get("path", path_arg),
                            "content": full_text,
                            "truncated": truncated_final,
                            "offset": res.get("offset", 0),
                            "total_chars": len(full_text),
                            "next_offset": None,
                            "estimated_tokens": fs.estimate_tokens(full_text),
                        }
                        if truncated_final:
                            combined_res["note"] = "Достигнут лимит повторов чтения файла."
                        tool_result = {"result": combined_res}

                if tool_name == "write_file" and "result" in tool_result:
                    r = tool_result["result"]
                    status = (
                        f"создан, {_pluralize_lines(r['lines_after'])}" if r.get("created")
                        else f"изменён, было {_pluralize_lines(r['lines_before'])}, стало {r['lines_after']}"
                    )
                    changed_files.append({"path": r["path"], "status": status})

                task_log.append({
                    "tool": tool_name,
                    "args": args,
                    "result": json.dumps(tool_result, ensure_ascii=False)[:800],
                })

                messages.append({
                    "role": "user",
                    "content": f"Результат инструмента {tool_name}: {json.dumps(tool_result, ensure_ascii=False)[:30000]}",
                })
                continue

            # Неизвестный action — просим модель исправиться.
            messages.append({
                "role": "user",
                "content": 'Некорректное поле "action". Используй только "tool" или "final".',
            })
    except Exception as e:
        logger.error("Неожиданный сбой в run_task на шаге агента: %s", e)
        error_str = _friendlify_error(str(e), state.lang)
        if changed_files:
            files_list = "\n".join(f"- {f['path']} ({f['status']})" for f in changed_files)
            error_text = (
                f"Задача прервана из-за ошибки: {error_str}\n\n"
                f"Успешно применены следующие файлы (откат не выполнялся):\n{files_list}\n\n"
                "Проверь их вручную перед продолжением."
            )
        else:
            error_text = f"Задача прервана из-за ошибки, ни один файл не был изменён: {error_str}"
        return error_text, changed_files, None

    return "Достигнут лимит шагов (защита от зацикливания). Задача не завершена — попробуй сузить запрос.", changed_files, None

_STEP_INTRO_BY_LANG = {
    "ru": "Ход выполнения задачи ({n} шаг(ов)):",
    "en": "Task steps ({n} step(s)):",
    "es": "Pasos de la tarea ({n} paso(s)):",
}

_STEP_FAILED_BY_LANG = {
    "ru": "{desc} — не получилось ({error})",
    "en": "{desc} — failed ({error})",
    "es": "{desc} — falló ({error})",
}


def _step_result(step: dict) -> tuple[dict | None, str | None]:
    """Разбирает сохранённый в task_log JSON результата шага.
    Возвращает (result_dict_или_None, текст_ошибки_или_None)."""
    try:
        parsed = json.loads(step["result"])
    except (json.JSONDecodeError, TypeError, KeyError):
        return None, None
    if not isinstance(parsed, dict):
        return None, None
    return parsed.get("result"), parsed.get("error")


def _describe_step_ru(step: dict) -> str:
    tool, args = step["tool"], step.get("args", {})
    res, error = _step_result(step)
    if tool == "list_tree":
        base = "просмотрел структуру проекта"
    elif tool == "create_folder":
        base = f"создал папку {args.get('relative_path', '?')}"
    elif tool == "search_content":
        pattern = args.get("pattern", "?")
        count = len(res["matches"]) if isinstance(res, dict) and "matches" in res else None
        base = f"искал «{pattern}» по проекту" + (f", найдено совпадений: {count}" if count is not None else "")
    elif tool == "read_file":
        base = f"прочитал файл {args.get('path', '?')}"
    elif tool == "write_file":
        path = args.get("path", "?")
        created = isinstance(res, dict) and res.get("created")
        base = f"создал файл {path}" if created else f"изменил файл {path}"
    elif tool == "move_file":
        base = f"переместил {args.get('source_path', '?')} → {args.get('destination_path', '?')}"
    elif tool == "delete_file":
        base = f"удалил файл {args.get('relative_path', '?')}"
    elif tool == "delete_folder":
        base = f"удалил папку {args.get('relative_path', '?')} со всем содержимым"
    elif tool == "get_current_datetime":
        base = "узнал текущую дату/время"
    elif tool == "read_web_page":
        base = f"загрузил страницу {args.get('url', '?')}"
    else:
        base = f"вызвал инструмент {tool}"
    return _STEP_FAILED_BY_LANG["ru"].format(desc=base, error=error) if error else base


def _describe_step_en(step: dict) -> str:
    tool, args = step["tool"], step.get("args", {})
    res, error = _step_result(step)
    if tool == "list_tree":
        base = "looked at the project structure"
    elif tool == "create_folder":
        base = f"created folder {args.get('relative_path', '?')}"
    elif tool == "search_content":
        pattern = args.get("pattern", "?")
        count = len(res["matches"]) if isinstance(res, dict) and "matches" in res else None
        base = f"searched the project for \"{pattern}\"" + (f", found {count} match(es)" if count is not None else "")
    elif tool == "read_file":
        base = f"read file {args.get('path', '?')}"
    elif tool == "write_file":
        path = args.get("path", "?")
        created = isinstance(res, dict) and res.get("created")
        base = f"created file {path}" if created else f"modified file {path}"
    elif tool == "move_file":
        base = f"moved {args.get('source_path', '?')} → {args.get('destination_path', '?')}"
    elif tool == "delete_file":
        base = f"deleted file {args.get('relative_path', '?')}"
    elif tool == "delete_folder":
        base = f"deleted folder {args.get('relative_path', '?')} and its contents"
    elif tool == "get_current_datetime":
        base = "checked the current date/time"
    elif tool == "read_web_page":
        base = f"loaded page {args.get('url', '?')}"
    else:
        base = f"called tool {tool}"
    return _STEP_FAILED_BY_LANG["en"].format(desc=base, error=error) if error else base


def _describe_step_es(step: dict) -> str:
    tool, args = step["tool"], step.get("args", {})
    res, error = _step_result(step)
    if tool == "list_tree":
        base = "revisó la estructura del proyecto"
    elif tool == "create_folder":
        base = f"creó la carpeta {args.get('relative_path', '?')}"
    elif tool == "search_content":
        pattern = args.get("pattern", "?")
        count = len(res["matches"]) if isinstance(res, dict) and "matches" in res else None
        base = f"buscó «{pattern}» en el proyecto" + (f", {count} coincidencia(s)" if count is not None else "")
    elif tool == "read_file":
        base = f"leyó el archivo {args.get('path', '?')}"
    elif tool == "write_file":
        path = args.get("path", "?")
        created = isinstance(res, dict) and res.get("created")
        base = f"creó el archivo {path}" if created else f"modificó el archivo {path}"
    elif tool == "move_file":
        base = f"movió {args.get('source_path', '?')} → {args.get('destination_path', '?')}"
    elif tool == "delete_file":
        base = f"eliminó el archivo {args.get('relative_path', '?')}"
    elif tool == "delete_folder":
        base = f"eliminó la carpeta {args.get('relative_path', '?')} y su contenido"
    elif tool == "get_current_datetime":
        base = "consultó la fecha/hora actual"
    elif tool == "read_web_page":
        base = f"cargó la página {args.get('url', '?')}"
    else:
        base = f"llamó a la herramienta {tool}"
    return _STEP_FAILED_BY_LANG["es"].format(desc=base, error=error) if error else base


_DESCRIBE_STEP_BY_LANG = {"ru": _describe_step_ru, "en": _describe_step_en, "es": _describe_step_es}


def summarize_task(state: SessionState, user_task: str, task_log: list[dict], final_text: str) -> str:
    """
    Пункт 5.2 плана (переработано): резюме шагов ОДНОЙ конкретной задачи
    собирается кодом напрямую из task_log — без обращения к LLM. Это
    полностью убирает риск "фантазирования" (выдуманных шагов или
    несуществующей архитектуры), которым страдала предыдущая версия на
    базе LLM: теперь в резюме попадает буквально то и только то, что
    реально записано в task_log.

    Вызывать только когда task_log не пуст — вызывающий код (run_task)
    уже это проверяет перед вызовом.
    """
    if len(task_log) == 0:
        return final_text
    describe = _DESCRIBE_STEP_BY_LANG.get(state.lang, _describe_step_ru)
    intro = _STEP_INTRO_BY_LANG.get(state.lang, _STEP_INTRO_BY_LANG["ru"]).format(n=len(task_log))
    lines = [intro]
    for i, step in enumerate(task_log, start=1):
        lines.append(f"{i}. {describe(step)}")
    return "\n".join(lines)


def update_project_summary(state: SessionState) -> str:
    """
    Сохраняет в долгую память проекта список задач, которые пользователь
    реально ставил агенту. Резюме собирается кодом из журнала сессии, без
    обращения к LLM, поэтому выдумать в нём ничего невозможно.
    Хранятся последние 10 задач (по одной строке: дата и текст задачи).
    Вызывается при закрытии программы.
    """
    import re
    from datetime import datetime

    if not state.project_root:
        return "Сессия без проекта, сохранять нечего."

    session_turns = state.memory.get_session_history(state.session_id)
    tasks: list[str] = []
    for t in session_turns:
        if t["role"] != "user":
            continue
        text = " ".join(t["content"].split())[:150]
        if text and text not in tasks:
            tasks.append(text)
    if not tasks:
        return "Сессия пуста, нечего суммировать."

    project_id = _project_id(state.project_root)
    prev = state.memory.get_fact(project_id, "last_session_summary") or ""
    # Из старого значения берём только строки нового формата (дата: задача).
    # Всё остальное, например выдуманный LLM-текст прежней версии, отбрасывается.
    line_re = re.compile(r"^\d{4}-\d{2}-\d{2}: ")
    old_lines = [ln for ln in prev.splitlines() if line_re.match(ln)]

    today = datetime.now().strftime("%Y-%m-%d")
    new_lines = [f"{today}: {task}" for task in tasks]
    summary = "\n".join((old_lines + new_lines)[-10:])
    state.memory.set_fact(project_id, "last_session_summary", summary)
    return summary