"""Тесты честности агента: ответ без вызова инструмента на задачу-действие."""
import json
from types import SimpleNamespace

import pytest

from agent import loop


class FakeMemory:
    def get_all_facts(self, pid): return {}
    def log_turn(self, *a): pass
    def get_fact(self, *a): return None
    def set_fact(self, *a): pass
    def get_session_history(self, sid): return []


def final(text):
    return json.dumps({"action": "final", "content": text}, ensure_ascii=False)


def tool(name, **args):
    return json.dumps({"action": "tool", "tool": name, "args": args}, ensure_ascii=False)


@pytest.fixture
def make_state(tmp_path):
    def _make(**kw):
        return loop.SessionState(
            project_root=str(tmp_path), session_id="t", write_enabled=True,
            memory=FakeMemory(), **kw,
        )
    return _make


def script_llm(monkeypatch, replies):
    """Подставляет call_llm, отдающий ответы по очереди; возвращает список вызовов."""
    calls = []
    it = iter(replies)

    def fake(messages, tier="fast"):
        calls.append(list(messages))
        return SimpleNamespace(text=next(it), provider="fake", model="fake")

    monkeypatch.setattr(loop, "call_llm", fake)
    return calls


def test_refusal_without_tool_is_retried_then_flagged(monkeypatch, make_state):
    state = make_state()
    calls = script_llm(monkeypatch, [final("Не могу создать файл."), final("Не могу, извините.")])
    text, changed, _ = loop.run_task(state, "создай файл x.txt")
    assert len(calls) == 2                                  # ровно один повтор
    assert text.startswith(loop._NO_ACTION_WARNINGS["ru"])  # предупреждение от кода
    assert changed == []
    # отказ модели не попал ни в историю, ни в кэш
    assert all("Не могу" not in m["content"] for m in state.history)
    assert state.task_cache == {}


def test_retry_makes_model_call_tool(monkeypatch, make_state):
    state = make_state()
    monkeypatch.setattr(loop, "_execute_tool", lambda s, t, a: {
        "result": {"created": True, "path": "x.txt", "lines_after": 1}})
    calls = script_llm(monkeypatch, [
        final("Готово!"),                       # враньё без инструмента
        tool("write_file", path="x.txt", content="hi"),
        final("Файл x.txt создан."),
    ])
    text, changed, summary = loop.run_task(state, "создай файл x.txt")
    assert len(calls) == 3
    assert changed and changed[0]["path"] == "x.txt"
    assert not text.startswith("⚠️")
    assert summary and "x.txt" in summary


def test_clarifying_question_passes_untouched(monkeypatch, make_state):
    state = make_state()
    calls = script_llm(monkeypatch, [final("Как назвать файл?")])
    text, _, _ = loop.run_task(state, "создай файл")
    assert len(calls) == 1
    assert text == "Как назвать файл?"


def test_plain_question_is_untouched_and_not_cached(monkeypatch, make_state):
    state = make_state()
    calls = script_llm(monkeypatch, [final("Это цикл агента.")])
    text, _, _ = loop.run_task(state, "что делает loop.py")
    assert len(calls) == 1
    assert text == "Это цикл агента."
    assert state.task_cache == {}   # проект открыт, инструментов не было


def test_failed_write_attempt_is_grounded(monkeypatch, make_state):
    """Если запись пробовали (даже с ошибкой), повтор не нужен: ответ подкреплён."""
    state = make_state()
    def boom(s, t, a): raise RuntimeError("что-то странное")
    monkeypatch.setattr(loop, "_execute_tool", boom)
    calls = script_llm(monkeypatch, [
        tool("write_file", path="x.txt", content="hi"),
        final("Не удалось записать файл: что-то странное."),
    ])
    text, _, _ = loop.run_task(state, "создай файл x.txt")
    assert len(calls) == 2
    assert not text.startswith("⚠️")


@pytest.mark.parametrize("task,expected", [
    ("создай файл x.txt", True),
    ("Пожалуйста, удали папку old", True),
    ("сделай новую папку docs", True),
    ("crea un archivo nuevo", True),
    ("Please delete the cache folder", True),
    ("как удалить файл в git?", False),
    ("что делает функция удаления?", False),
    ("how to add a column in pandas", False),
])
def test_task_needs_action(task, expected):
    assert loop._task_needs_action(task) is expected
