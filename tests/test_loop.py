"""
Юнит-тесты для agent/loop.py: диспетчер инструментов, сжатие контекста,
основной цикл run_task и update_project_summary.

call_llm, fs (tools.filesystem) и web_tools (tools.web) полностью мокаются —
тесты не зависят от реальных LLM-провайдеров и от конкретной реализации
tools/filesystem.py / agent/memory.py, только от контракта, который
использует agent/loop.py.
"""
import json

import pytest
from unittest.mock import MagicMock

import agent.loop as loop
from agent.llm import LLMResponse


# ---------------------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_fs(monkeypatch):
    fake = MagicMock()
    # По умолчанию токены "дешёвые", чтобы сжатие контекста не срабатывало
    # случайно в тестах, которые его не проверяют.
    fake.estimate_tokens.side_effect = lambda text: 1
    monkeypatch.setattr(loop, "fs", fake)
    return fake


@pytest.fixture
def fake_web(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(loop, "web_tools", fake)
    return fake


@pytest.fixture
def fake_call_llm(monkeypatch):
    mock = MagicMock()
    monkeypatch.setattr(loop, "call_llm", mock)
    return mock


@pytest.fixture
def fake_memory():
    mem = MagicMock()
    mem.get_all_facts.return_value = {}
    mem.get_session_history.return_value = []
    mem.get_fact.return_value = None
    return mem


@pytest.fixture
def state(fake_fs, fake_web, fake_memory):
    return loop.SessionState(
        project_root="/project", session_id="sess-1", memory=fake_memory
    )


def _final(content, provider="groq", model="m"):
    return LLMResponse(
        text=json.dumps({"action": "final", "content": content}, ensure_ascii=False),
        provider=provider, model=model,
    )


def _tool(tool_name, args, provider="groq", model="m"):
    return LLMResponse(
        text=json.dumps({"action": "tool", "tool": tool_name, "args": args}, ensure_ascii=False),
        provider=provider, model=model,
    )


# ---------------------------------------------------------------------------
# _parse_model_json
# ---------------------------------------------------------------------------

def test_parse_model_json_plain():
    result = loop._parse_model_json('{"action": "final", "content": "ok"}')
    assert result == {"action": "final", "content": "ok"}


def test_parse_model_json_strips_markdown_backticks():
    raw = '```json\n{"action": "final", "content": "ok"}\n```'
    result = loop._parse_model_json(raw)
    assert result == {"action": "final", "content": "ok"}


def test_parse_model_json_invalid_raises():
    with pytest.raises(json.JSONDecodeError):
        loop._parse_model_json("это не json")


# ---------------------------------------------------------------------------
# _execute_tool
# ---------------------------------------------------------------------------

def test_execute_tool_get_current_datetime_no_project_needed(fake_fs):
    state = loop.SessionState(project_root=None, session_id="s", memory=MagicMock())
    fake_fs.get_current_datetime.return_value = {"iso": "2026-09-09"}
    result = loop._execute_tool(state, "get_current_datetime", {"timezone": "UTC"})
    assert result == {"result": {"iso": "2026-09-09"}}
    fake_fs.get_current_datetime.assert_called_once_with(timezone="UTC")


def test_execute_tool_read_web_page_no_project_needed(fake_web):
    state = loop.SessionState(project_root=None, session_id="s", memory=MagicMock())
    fake_web.read_web_page.return_value = {"text": "содержимое страницы"}
    result = loop._execute_tool(state, "read_web_page", {"url": "http://example.com"})
    assert result == {"result": {"text": "содержимое страницы"}}
    fake_web.read_web_page.assert_called_once_with("http://example.com")


def test_execute_tool_requires_project_root_for_file_tools():
    state = loop.SessionState(project_root=None, session_id="s", memory=MagicMock())
    with pytest.raises(ValueError, match="Проект не открыт"):
        loop._execute_tool(state, "list_tree", {})


def test_execute_tool_list_tree(state, fake_fs):
    fake_fs.list_tree.return_value = {"content": ["a.py", "b.py"]}
    result = loop._execute_tool(state, "list_tree", {})
    assert result == {"result": {"content": ["a.py", "b.py"]}}
    fake_fs.list_tree.assert_called_once_with("/project")


def test_execute_tool_search_content_uses_defaults(state, fake_fs):
    fake_fs.search_content.return_value = {"matches": []}
    loop._execute_tool(state, "search_content", {"pattern": "foo"})
    fake_fs.search_content.assert_called_once_with(
        "/project", pattern="foo", glob="*", regex=False
    )


def test_execute_tool_search_content_passes_explicit_args(state, fake_fs):
    fake_fs.search_content.return_value = {"matches": []}
    loop._execute_tool(
        state, "search_content", {"pattern": "foo", "glob": "*.py", "regex": True}
    )
    fake_fs.search_content.assert_called_once_with(
        "/project", pattern="foo", glob="*.py", regex=True
    )


def test_execute_tool_read_file(state, fake_fs):
    fake_fs.read_file.return_value = {"content": "данные файла"}
    result = loop._execute_tool(state, "read_file", {"path": "a.py", "offset": 5})
    assert result == {"result": {"content": "данные файла"}}
    fake_fs.read_file.assert_called_once_with("/project", "a.py", offset=5)


def test_execute_tool_write_file_passes_write_enabled_flag(fake_fs, fake_web, fake_memory):
    state = loop.SessionState(
        project_root="/project", session_id="s", write_enabled=True, memory=fake_memory
    )
    fake_fs.write_file.return_value = {"path": "a.py", "created": True}
    loop._execute_tool(state, "write_file", {"path": "a.py", "content": "x"})
    fake_fs.write_file.assert_called_once_with("/project", "a.py", "x", write_enabled=True)


def test_execute_tool_unknown_tool_raises(state):
    with pytest.raises(ValueError, match="Неизвестный инструмент"):
        loop._execute_tool(state, "delete_file", {})


# ---------------------------------------------------------------------------
# _trim_context_if_needed
# ---------------------------------------------------------------------------

def test_trim_context_unchanged_when_below_threshold(fake_fs, fake_call_llm):
    fake_fs.estimate_tokens.side_effect = lambda t: 100
    messages = [{"role": "system", "content": "s"}] + [
        {"role": "user", "content": "u"} for _ in range(3)
    ]
    result = loop._trim_context_if_needed(messages)
    assert result == messages
    fake_call_llm.assert_not_called()


def test_trim_context_unchanged_when_too_few_messages(fake_fs, fake_call_llm):
    # Токенов много, но сообщений слишком мало, чтобы было что сжимать
    fake_fs.estimate_tokens.side_effect = lambda t: 10000
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u1"},
        {"role": "user", "content": "u2"},
    ]
    result = loop._trim_context_if_needed(messages)
    assert result == messages
    fake_call_llm.assert_not_called()


def test_trim_context_compresses_when_above_threshold(fake_fs, fake_call_llm):
    fake_fs.estimate_tokens.side_effect = lambda t: 10000
    messages = [{"role": "system", "content": "sys"}] + [
        {"role": "user", "content": f"m{i}"} for i in range(10)
    ]
    fake_call_llm.return_value = LLMResponse(text="краткое саммари", provider="p", model="m")

    result = loop._trim_context_if_needed(messages)

    assert len(result) == 1 + 1 + 4  # system + summary + tail_keep(4)
    assert result[0] == messages[0]
    assert "[Сжатая история предыдущих шагов]" in result[1]["content"]
    assert "краткое саммари" in result[1]["content"]
    assert result[-4:] == messages[-4:]
    fake_call_llm.assert_called_once()
    assert fake_call_llm.call_args.kwargs["tier"] == "fast"


# ---------------------------------------------------------------------------
# run_task — кэш
# ---------------------------------------------------------------------------

def test_run_task_cache_hit_skips_llm(state, fake_call_llm):
    state.task_cache["привет"] = ("Ответ из кэша", [{"path": "a.py", "status": "изменён"}])

    text, changed = loop.run_task(state, "  Привет!!!  ")

    assert text == "Ответ из кэша"
    assert changed == [{"path": "a.py", "status": "изменён"}]
    fake_call_llm.assert_not_called()
    state.memory.log_turn.assert_any_call("sess-1", "user", "  Привет!!!  ")
    state.memory.log_turn.assert_any_call("sess-1", "assistant", "Ответ из кэша")


# ---------------------------------------------------------------------------
# run_task — базовый final-ответ и friendlify
# ---------------------------------------------------------------------------

def test_run_task_simple_final_response(state, fake_call_llm):
    fake_call_llm.return_value = _final("Готово!")

    text, changed = loop.run_task(state, "Что делать?")

    assert text == "Готово!"
    assert changed == []
    fake_call_llm.assert_called_once()
    assert fake_call_llm.call_args.kwargs["tier"] == "fast"
    assert list(state.task_cache.values())[0][0] == "Готово!"


def test_run_task_friendlifies_error_in_final_content(state, fake_call_llm):
    fake_call_llm.return_value = _final("Ошибка: rate limit exceeded")

    text, _ = loop.run_task(state, "задача")

    assert text == (
        "Сейчас все провайдеры ИИ перегружены или недоступны. "
        "Попробуйте повторить запрос через несколько минут."
    )


# ---------------------------------------------------------------------------
# run_task — цепочка tool -> final
# ---------------------------------------------------------------------------

def test_run_task_tool_call_then_final(state, fake_call_llm, fake_fs):
    fake_fs.list_tree.return_value = {"content": ["a.py"]}
    fake_call_llm.side_effect = [
        _tool("list_tree", {}),
        _final("Дерево получено"),
    ]

    text, _ = loop.run_task(state, "покажи файлы")

    assert text == "Дерево получено"
    assert fake_call_llm.call_count == 2
    fake_fs.list_tree.assert_called_once_with("/project")
    second_messages = fake_call_llm.call_args_list[1][0][0]
    assert any(
        "Результат инструмента list_tree" in m["content"]
        for m in second_messages if m["role"] == "user"
    )


def test_run_task_write_file_records_created_status(state, fake_call_llm, fake_fs):
    fake_fs.write_file.return_value = {
        "path": "new.py", "created": True, "lines_before": 0, "lines_after": 5,
    }
    fake_call_llm.side_effect = [
        _tool("write_file", {"path": "new.py", "content": "print(1)"}),
        _final("Файл создан"),
    ]

    _, changed = loop.run_task(state, "создай файл")

    assert changed == [{"path": "new.py", "status": "создан, 5 строк"}]


def test_run_task_write_file_records_edited_status(state, fake_call_llm, fake_fs):
    fake_fs.write_file.return_value = {
        "path": "a.py", "created": False, "lines_before": 3, "lines_after": 10,
    }
    fake_call_llm.side_effect = [
        _tool("write_file", {"path": "a.py", "content": "x"}),
        _final("Файл изменён"),
    ]

    _, changed = loop.run_task(state, "измени файл")

    assert changed == [{"path": "a.py", "status": "изменён, было 3 строки, стало 10"}]


def test_run_task_tool_exception_is_reported_and_loop_continues(state, fake_call_llm, fake_fs):
    fake_fs.list_tree.side_effect = RuntimeError("boom")
    fake_call_llm.side_effect = [
        _tool("list_tree", {}),
        _final("Ошибка обработана"),
    ]

    text, _ = loop.run_task(state, "покажи файлы")

    assert text == "Ошибка обработана"
    second_messages = fake_call_llm.call_args_list[1][0][0]
    assert any(
        '"error": "boom"' in m["content"] for m in second_messages if m["role"] == "user"
    )


# ---------------------------------------------------------------------------
# run_task — автодочитывание read_file
# ---------------------------------------------------------------------------

def test_run_task_read_file_auto_continuation_combines_parts(state, fake_call_llm, fake_fs):
    fake_fs.estimate_tokens.side_effect = lambda t: len(t)
    fake_fs.read_file.side_effect = [
        {"path": "big.py", "content": "part1", "truncated": True, "next_offset": 100, "offset": 0},
        {"path": "big.py", "content": "part2", "truncated": False, "offset": 100},
    ]
    fake_call_llm.side_effect = [
        _tool("read_file", {"path": "big.py"}),
        _final("Прочитано"),
    ]

    text, _ = loop.run_task(state, "прочитай big.py")

    assert text == "Прочитано"
    assert fake_fs.read_file.call_count == 2
    fake_fs.read_file.assert_any_call("/project", "big.py", offset=0)
    fake_fs.read_file.assert_any_call("/project", "big.py", offset=100)
    second_messages = fake_call_llm.call_args_list[1][0][0]
    combined = next(
        m["content"] for m in second_messages
        if m["role"] == "user" and "Результат инструмента read_file" in m["content"]
    )
    assert "part1part2" in combined
    assert '"truncated": false' in combined


def test_run_task_read_file_auto_continuation_hits_repeat_limit(state, fake_call_llm, fake_fs):
    """Раньше здесь был баг с мёртвым кодом после break — проверяем, что при
    файле, который ВСЕГДА возвращает truncated=True, цикл останавливается
    ровно на 20 частях и добавляет предупреждение, а не зацикливается."""
    fake_fs.estimate_tokens.side_effect = lambda t: len(t)

    def always_truncated(project_root, path, offset=0):
        return {
            "path": path, "content": f"part@{offset}",
            "truncated": True, "next_offset": offset + 10, "offset": offset,
        }

    fake_fs.read_file.side_effect = always_truncated
    fake_call_llm.side_effect = [
        _tool("read_file", {"path": "huge.py"}),
        _final("Готово, частично"),
    ]

    text, _ = loop.run_task(state, "прочитай huge.py")

    assert text == "Готово, частично"
    assert fake_fs.read_file.call_count == 20  # 1 первичный + 19 в цикле дочитывания
    second_messages = fake_call_llm.call_args_list[1][0][0]
    combined = next(
        m["content"] for m in second_messages
        if m["role"] == "user" and "Результат инструмента read_file" in m["content"]
    )
    assert "лимит повторов чтения" in combined


# ---------------------------------------------------------------------------
# run_task — невалидный JSON и неизвестный action
# ---------------------------------------------------------------------------

def test_run_task_invalid_json_retries_then_recovers(state, fake_call_llm):
    fake_call_llm.side_effect = [
        LLMResponse(text="не json", provider="p", model="m"),
        LLMResponse(text="снова не json", provider="p", model="m"),
        _final("Наконец получилось"),
    ]

    text, _ = loop.run_task(state, "задача")

    assert text == "Наконец получилось"
    assert fake_call_llm.call_count == 3


def test_run_task_invalid_json_three_times_gives_up(state, fake_call_llm):
    fake_call_llm.side_effect = [
        LLMResponse(text="bad", provider="p", model="m") for _ in range(3)
    ]

    text, changed = loop.run_task(state, "задача")

    assert "некорректный ответ" in text.lower()
    assert changed == []
    assert fake_call_llm.call_count == 3


def test_run_task_unknown_action_prompts_model_to_fix(state, fake_call_llm):
    fake_call_llm.side_effect = [
        LLMResponse(text=json.dumps({"action": "weird"}), provider="p", model="m"),
        _final("ок теперь верно"),
    ]

    text, _ = loop.run_task(state, "задача")

    assert text == "ок теперь верно"
    second_messages = fake_call_llm.call_args_list[1][0][0]
    assert any(
        'Используй только "tool" или "final"' in m["content"]
        for m in second_messages if m["role"] == "user"
    )


# ---------------------------------------------------------------------------
# run_task — выбор tier
# ---------------------------------------------------------------------------

def test_run_task_uses_strong_tier_for_refactor_keywords(state, fake_call_llm):
    fake_call_llm.return_value = _final("готово")
    loop.run_task(state, "Перепиши этот модуль")
    assert fake_call_llm.call_args.kwargs["tier"] == "strong"


def test_run_task_uses_fast_tier_by_default(state, fake_call_llm):
    fake_call_llm.return_value = _final("готово")
    loop.run_task(state, "покажи список файлов")
    assert fake_call_llm.call_args.kwargs["tier"] == "fast"


# ---------------------------------------------------------------------------
# run_task — MAX_STEPS и общий except
# ---------------------------------------------------------------------------

def test_run_task_max_steps_reached(state, fake_call_llm, fake_fs):
    fake_fs.get_current_datetime.return_value = {"iso": "2026-09-09"}
    fake_call_llm.return_value = _tool("get_current_datetime", {})

    text, changed = loop.run_task(state, "который час")

    assert "лимит шагов" in text.lower()
    assert changed == []
    assert fake_call_llm.call_count == loop.MAX_STEPS


def test_run_task_unexpected_exception_with_changed_files(state, fake_call_llm, fake_fs):
    fake_fs.write_file.return_value = {
        "path": "a.py", "created": True, "lines_before": 0, "lines_after": 1,
    }
    calls = {"n": 0}

    def side_effect(messages, tier="fast"):
        calls["n"] += 1
        if calls["n"] == 1:
            return _tool("write_file", {"path": "a.py", "content": "x"})
        raise RuntimeError("провайдеры отказали")

    fake_call_llm.side_effect = side_effect

    text, changed = loop.run_task(state, "создай файл")

    assert "Задача прервана из-за ошибки" in text
    assert "a.py" in text
    assert "Сейчас все провайдеры ИИ перегружены" in text
    assert changed == [{"path": "a.py", "status": "создан, 1 строка"}]


def test_run_task_unexpected_exception_without_changed_files(state, fake_call_llm):
    fake_call_llm.side_effect = RuntimeError("что-то сломалось")

    text, changed = loop.run_task(state, "задача")

    assert "ни один файл не был изменён" in text
    assert changed == []


# ---------------------------------------------------------------------------
# update_project_summary
# ---------------------------------------------------------------------------

def test_update_project_summary_empty_history(state, fake_call_llm):
    state.memory.get_session_history.return_value = []

    result = loop.update_project_summary(state)

    assert result == "Сессия пуста, нечего суммировать."
    fake_call_llm.assert_not_called()


def test_update_project_summary_with_history(state, fake_call_llm):
    state.memory.get_session_history.return_value = [
        {"role": "user", "content": "Сделай X"},
        {"role": "assistant", "content": "Готово X"},
    ]
    state.memory.get_fact.return_value = "старое саммари"
    fake_call_llm.return_value = LLMResponse(text="новое саммари", provider="p", model="m")

    result = loop.update_project_summary(state)

    assert result == "новое саммари"
    assert fake_call_llm.call_args.kwargs["tier"] == "fast"
    state.memory.set_fact.assert_called_once_with(
        loop._project_id(state.project_root), "last_session_summary", "новое саммари"
    )