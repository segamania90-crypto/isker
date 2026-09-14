"""
Тесты на атомарность (Этап 6, последний открытый пункт).

1. test_write_file_atomic_on_failure — если запись во временный файл прошла,
   но переименование (os.replace) сорвалось, исходный файл должен остаться
   НЕТРОНУТЫМ (не битым, не наполовину записанным).

2. test_multi_file_task_no_rollback_on_crash — если агент в рамках одной
   задачи успешно записал файл A, а затем упал (например, LLM недоступна
   на всех провайдерах), файл A должен остаться изменённым (откат не
   делается), файл B — не создан, а в тексте ошибки должно быть честно
   написано, что откат не выполнялся, и перечислены применённые файлы.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools import filesystem as fs
from agent.loop import SessionState, run_task


# ---------- Тест 1: атомарность одного файла ----------

def test_write_file_atomic_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "config.txt"
    target.write_text("старое содержимое", encoding="utf-8")

    def broken_replace(src, dst):
        raise OSError("симулированный сбой ОС при переименовании")

    monkeypatch.setattr(os, "replace", broken_replace)

    with pytest.raises(OSError):
        fs.write_file(str(tmp_path), "config.txt", "новое содержимое", write_enabled=True)

    # Главное: исходный файл не должен быть повреждён или заменён.
    assert target.read_text(encoding="utf-8") == "старое содержимое"


# ---------- Тест 2: атомарность между файлами в многошаговой задаче ----------

class FakeMemory:
    """Лёгкая заглушка вместо реальной SQLite-памяти — тест не должен трогать диск."""
    def get_all_facts(self, project_id):
        return {}

    def log_turn(self, session_id, role, content):
        pass

    def get_session_history(self, session_id):
        return []

    def get_fact(self, project_id, key):
        return None

    def set_fact(self, project_id, key, value):
        pass


class FakeResponse:
    def __init__(self, text, provider="fake", model="fake-model"):
        self.text = text
        self.provider = provider
        self.model = model


def test_multi_file_task_no_rollback_on_crash(tmp_path, monkeypatch):
    # Шаг 1: модель просит записать file_a.txt (должно успешно применится).
    # Шаг 2: перед записью file_b.txt LLM "падает" (все провайдеры отказали).
    responses = [
        FakeResponse(
            '{"action": "tool", "tool": "write_file", '
            '"args": {"path": "file_a.txt", "content": "содержимое A"}}'
        ),
    ]

    call_count = {"n": 0}

    def fake_call_llm(messages, tier="fast"):
        call_count["n"] += 1
        if call_count["n"] <= len(responses):
            return responses[call_count["n"] - 1]
        raise RuntimeError("все провайдеры (Groq/OpenRouter/Gemini) отказали")

    monkeypatch.setattr("agent.loop.call_llm", fake_call_llm)

    state = SessionState(
        project_root=str(tmp_path),
        session_id="test-session",
        write_enabled=True,
        memory=FakeMemory(),
    )

    final_text, changed_files, task_summary = run_task(state, "создай file_a.txt и file_b.txt")

    # file_a должен реально существовать на диске с нужным содержимым.
    file_a = tmp_path / "file_a.txt"
    assert file_a.exists()
    assert file_a.read_text(encoding="utf-8") == "содержимое A"

    # file_b НЕ должен быть создан — до него дело не дошло.
    assert not (tmp_path / "file_b.txt").exists()

    # changed_files должен отражать реально применённый файл.
    assert any(f["path"] == "file_a.txt" for f in changed_files)
    assert not any(f["path"] == "file_b.txt" for f in changed_files)

    # Отчёт должен быть ЧЕСТНЫМ: упомянуть file_a, сказать, что откат не делался.
    assert "file_a.txt" in final_text
    assert "откат не выполнялся" in final_text
    assert "file_b.txt" not in final_text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
