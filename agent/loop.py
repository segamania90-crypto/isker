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
# COMPRESSION_THRESHOLD — сжимаем историю, только когда реально приблизились к лимиту,
# а не на каждом шаге (экономим отдельный вызов LLM на сжатие).
CONTEXT_TOKEN_LIMIT = int(os.getenv("CONTEXT_TOKEN_LIMIT", "6000"))
COMPRESSION_THRESHOLD = 0.7

# Пункт 3 аудита: технические сообщения об ошибках заменяются на понятные
# пользователю. Общий словарь для всей run_task — используется и при
# обычном "final" ответе модели, и в except-блоке сбоя всего цикла.
# Временное решение только для русского языка.
# TODO: в будущем учитывать self.lang из UI для перевода на en/es.
_ERROR_PATTERNS = {
    "api_key не задан": "Не настроен доступ к одному из провайдеров ИИ. Проверьте файл .env — там должны быть заданы ключи API.",
    "rate limit": "Сейчас все провайдеры ИИ перегружены или недоступны. Попробуйте повторить запрос через несколько минут.",
    "провайдеры отказали": "Сейчас все провайдеры ИИ перегружены или недоступны. Попробуйте повторить запрос через несколько минут.",
    "не удалось получить страницу": "Не получилось загрузить указанную веб-страницу. Проверьте, что ссылка верна и страница доступна.",
    "файл не найден": "Указанный файл не найден в проекте. Проверьте путь и повторите запрос.",
    "путь выходит за пределы": "Запрошенный файл находится за пределами разрешённой папки проекта.",
    "запись в файлы проекта не разрешена": "Запись в файлы не разрешена в этой сессии. Включите разрешение на запись при старте сессии.",
}


def _friendlify_error(text: str) -> str:
    """Заменяет известный технический текст ошибки на понятный пользователю.
    Если совпадений нет — возвращает text без изменений."""
    lowered = text.lower()
    for pattern, friendly in _ERROR_PATTERNS.items():
        if pattern in lowered:
            logger.warning("Техническая ошибка скрыта от пользователя: %s", text)
            return friendly
    return text


SYSTEM_PROMPT_TEMPLATE = """Ты — AI-агент, ускоряющий рутинную работу разработчика в проекте.
Проект языко-агностичен: код может быть на любом языке (GDScript, Python, JS и т.д.) —
ты уже умеешь их понимать, отдельных инструкций по языку не требуется.

Всегда отвечай пользователю (в поле "content" финального ответа) на том языке,
на котором он задал вопрос — если он пишет по-русски, отвечай по-русски; если
по-английски — по-английски, и т.д.

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
просит сделать что-то, для чего инструмента НЕТ (например, удалить файл,
переименовать файл, выполнить код, установить пакет, сделать git commit) —
НИКОГДА не притворяйся, что выполнил это, и не выдумывай результат (ни
"успешно удалено", ни "файл не найден", если ты это не проверял). Вместо
этого через action="final" честно скажи пользователю, что у тебя нет
инструмента для этого конкретного действия, и что реально доступно
(например: "У меня нет инструмента для удаления файлов — я могу только
читать, искать и записывать файлы. Удали файл вручную."). Ложь о выполненном
действии недопустима ни при каких обстоятельствах.

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
        print(f">>> READ_FILE вызван: path={args['path']} offset={offset}")
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


def run_task(state: SessionState, user_task: str) -> tuple[str, list[dict]]:
    """
    Запускает цикл агента для одной задачи пользователя в рамках сессии.
    Возвращает (финальный текстовый ответ, список изменённых файлов).
    Каждый элемент списка изменённых файлов: {"path": str, "status": str}.
    Все шаги пишутся в state.history.
    """
    import re
    cache_key = re.sub(r'[.,!?;:]+$', '', user_task.strip().lower())
    cache_key = re.sub(r'\s+', ' ', cache_key)
    if cache_key in state.task_cache:
        logger.info("Кэш: точное совпадение задачи, LLM не вызывается")
        cached_text, cached_changed_files = state.task_cache[cache_key]
        state.history.append({"role": "user", "content": user_task})
        state.history.append({"role": "assistant", "content": cached_text})
        state.memory.log_turn(state.session_id, "user", user_task)
        state.memory.log_turn(state.session_id, "assistant", cached_text)
        return cached_text, cached_changed_files

    changed_files: list[dict] = []

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
            "- write_file(path, content) -> записать файл (доступно только если запись разрешена в этой сессии)\n"
            "- get_current_datetime(timezone=null) -> реальные текущие дата и время. "
            "Без timezone — время компьютера пользователя. С timezone (IANA-формат, например "
            "\"Europe/Madrid\", \"Asia/Almaty\", \"UTC\") — время в этом часовом поясе. "
            "Если пользователь спрашивает время/дату конкретного города или страны — определи "
            "подходящий IANA-часовой пояс сам и передай его. Если не уверен в поясе — используй "
            "action=\"final\" и уточни у пользователя, вместо угадывания. "



            "Никогда не выдумывай дату/время самостоятельно, у тебя нет своих встроенных часов.\n"
            "- read_web_page(url) -> скачать и прочитать содержимое ОДНОЙ веб-страницы по прямой "
            "ссылке. Используй ТОЛЬКО если пользователь сам явно указал URL (например, ссылку на "
            "документацию). Никогда не вызывай этот инструмент по собственной инициативе без "
            "явно данной пользователем ссылки, и никогда не пытайся обойти сайт по внутренним "
            "ссылкам — только одна страница за раз."
        )
    else:
        tools_section = (
            "Проект сейчас НЕ открыт. Инструменты работы с файлами (list_tree, search_content, "
            "read_file, write_file) недоступны — не пытайся их вызывать, для вопросов о коде "
            "проекта сразу отвечай action=\"final\" текстом.\n\n"
            "Однако тебе доступен инструмент get_current_datetime(timezone=null) -> реальные "
            "текущие дата и время. Без timezone — время компьютера пользователя. С timezone "
            "(IANA-формат, например \"Europe/Madrid\", \"Asia/Almaty\", \"UTC\") — время в этом "
            "часовом поясе. Если пользователь спрашивает время/дату конкретного города или "
            "страны — определи подходящий IANA-часовой пояс сам и передай его. Если не уверен "
            "в поясе — используй action=\"final\" и уточни у пользователя, вместо угадывания. "
            "Никогда не выдумывай дату/время самостоятельно, у тебя нет своих встроенных часов.\n"
            "- read_web_page(url) -> скачать и прочитать содержимое ОДНОЙ веб-страницы по прямой "
            "ссылке. Используй ТОЛЬКО если пользователь сам явно указал URL (например, ссылку на "
            "документацию). Никогда не вызывай этот инструмент по собственной инициативе без "
            "явно данной пользователем ссылки, и никогда не пытайся обойти сайт по внутренним "
            "ссылкам — только одна страница за раз."
        )

    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        tools_section=tools_section,
        project_memory=project_memory_text,
    )
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(state.history)
    messages.append({"role": "user", "content": user_task})

    failed_parse_count = 0
    try:
        for step in range(MAX_STEPS):
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
                    return error_text, changed_files
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
                final_text = _friendlify_error(final_text)
                state.history.append({"role": "user", "content": user_task})
                state.history.append({"role": "assistant", "content": final_text})
                state.memory.log_turn(state.session_id, "user", user_task)
                state.memory.log_turn(state.session_id, "assistant", final_text)
                state.task_cache[cache_key] = (final_text, changed_files)
                return final_text, changed_files

            if decision.get("action") == "tool":
                tool_name = decision.get("tool")
                args = decision.get("args", {})
                try:
                    tool_result = _execute_tool(state, tool_name, args)
                except Exception as e:  # инструмент упал — отдаём ошибку модели, пусть решает дальше
                    tool_result = {"error": str(e)}
                    logger.warning("Инструмент %s упал: %s", tool_name, e)

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
        error_str = _friendlify_error(str(e))
        if changed_files:
            files_list = "\n".join(f"- {f['path']} ({f['status']})" for f in changed_files)
            error_text = (
                f"Задача прервана из-за ошибки: {error_str}\n\n"
                f"Успешно применены следующие файлы (откат не выполнялся):\n{files_list}\n\n"
                "Проверь их вручную перед продолжением."
            )
        else:
            error_text = f"Задача прервана из-за ошибки, ни один файл не был изменён: {error_str}"
        return error_text, changed_files

    return "Достигнут лимит шагов (защита от зацикливания). Задача не завершена — попробуй сузить запрос.", changed_files

def update_project_summary(state: SessionState) -> str:
    """
    Просит модель суммировать всё, что сделано в этой сессии, и сохраняет
    результат в долгую память (переживает перезапуск агента).
    Вызывать вручную в конце сессии работы над проектом.
    """
    session_turns = state.memory.get_session_history(state.session_id)
    if not session_turns:
        return "Сессия пуста, нечего суммировать."

    transcript = "\n".join(f"{t['role']}: {t['content']}" for t in session_turns)
    prev_summary = state.memory.get_fact(_project_id(state.project_root), "last_session_summary") or "(нет предыдущего саммари)"

    prompt = (
        f"Предыдущее саммари проекта:\n{prev_summary}\n\n"
        f"Действия за текущую сессию:\n{transcript}\n\n"
        "Обнови саммари проекта: кратко опиши архитектуру и главные изменения "
        "за эту сессию. 5-8 предложений, обычный текст, без JSON."
    )
    response = call_llm([{"role": "user", "content": prompt}], tier="fast")
    state.memory.set_fact(_project_id(state.project_root), "last_session_summary", response.text)
    return response.text
