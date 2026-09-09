"""
Юнит-тесты для agent/llm.py: цепочка провайдеров и fallback-логика.

Все тесты мокают HTTP-слой (requests.post) — реальные API-ключи и лимиты
провайдеров не нужны и не расходуются.
"""
import pytest
from unittest.mock import patch, MagicMock

import agent.llm as llm


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    # В call_llm есть time.sleep(0.5) между попытками — отключаем, чтобы тесты
    # не тормозили и не зависели от реального времени.
    monkeypatch.setattr(llm.time, "sleep", lambda *a, **kw: None)


def _mock_response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text
    return resp


def _openai_ok(content="ok"):
    return _mock_response(200, {"choices": [{"message": {"content": content}}]})


def _gemini_ok(content="ok"):
    return _mock_response(200, {"candidates": [{"content": {"parts": [{"text": content}]}}]})


# ---------------------------------------------------------------------------
# _call_groq / _call_openrouter
# ---------------------------------------------------------------------------

def test_call_groq_missing_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(llm.ProviderError, match="GROQ_API_KEY"):
        llm._call_groq("model", [{"role": "user", "content": "hi"}])


def test_call_groq_success(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    with patch.object(llm.requests, "post", return_value=_openai_ok("Привет от Groq")) as mock_post:
        result = llm._call_groq("llama-x", [{"role": "user", "content": "hi"}])
    assert result == "Привет от Groq"
    assert mock_post.call_args[0][0] == "https://api.groq.com/openai/v1/chat/completions"


def test_call_groq_429_raises_provider_error(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    with patch.object(llm.requests, "post", return_value=_mock_response(429)):
        with pytest.raises(llm.ProviderError, match="429"):
            llm._call_groq("llama-x", [{"role": "user", "content": "hi"}])


def test_call_groq_server_error_raises_provider_error(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    with patch.object(llm.requests, "post", return_value=_mock_response(500, text="internal error")):
        with pytest.raises(llm.ProviderError, match="HTTP 500"):
            llm._call_groq("llama-x", [{"role": "user", "content": "hi"}])


def test_call_openrouter_missing_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(llm.ProviderError, match="OPENROUTER_API_KEY"):
        llm._call_openrouter("model", [{"role": "user", "content": "hi"}])


def test_call_openrouter_success(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    with patch.object(llm.requests, "post", return_value=_openai_ok("ok")) as mock_post:
        result = llm._call_openrouter("model", [{"role": "user", "content": "hi"}])
    assert result == "ok"
    assert mock_post.call_args[0][0] == "https://openrouter.ai/api/v1/chat/completions"


# ---------------------------------------------------------------------------
# _call_gemini — свой формат запроса/ответа
# ---------------------------------------------------------------------------

def test_call_gemini_missing_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(llm.ProviderError, match="GEMINI_API_KEY"):
        llm._call_gemini("model", [{"role": "user", "content": "hi"}])


def test_call_gemini_success(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    with patch.object(llm.requests, "post", return_value=_gemini_ok("ответ Gemini")) as mock_post:
        result = llm._call_gemini("gemini-x", [{"role": "user", "content": "hi"}])
    assert result == "ответ Gemini"
    assert mock_post.call_args[1]["params"] == {"key": "test-key"}


def test_call_gemini_429_raises_provider_error(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    with patch.object(llm.requests, "post", return_value=_mock_response(429)):
        with pytest.raises(llm.ProviderError, match="429"):
            llm._call_gemini("gemini-x", [{"role": "user", "content": "hi"}])


def test_call_gemini_merges_system_into_first_user_message(monkeypatch):
    """Gemini не поддерживает роль system отдельно — она должна стать
    префиксом первого user-сообщения, а assistant -> role 'model'."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    messages = [
        {"role": "system", "content": "Ты — полезный ассистент."},
        {"role": "user", "content": "Как дела?"},
        {"role": "assistant", "content": "Хорошо."},
        {"role": "user", "content": "Отлично."},
    ]
    with patch.object(llm.requests, "post", return_value=_gemini_ok()) as mock_post:
        llm._call_gemini("gemini-x", messages)

    sent = mock_post.call_args[1]["json"]["contents"]
    assert len(sent) == 3  # system не идёт отдельным элементом
    assert sent[0]["role"] == "user"
    assert sent[0]["parts"][0]["text"].startswith("Ты — полезный ассистент.")
    assert sent[0]["parts"][0]["text"].endswith("Как дела?")
    assert sent[1]["role"] == "model"
    assert sent[2]["parts"][0]["text"] == "Отлично."  # префикс использован только раз


# ---------------------------------------------------------------------------
# call_llm — оркестрация цепочки и fallback
# ---------------------------------------------------------------------------

def test_call_llm_first_provider_success(monkeypatch):
    calls = []

    def groq_ok(model, messages):
        calls.append("groq")
        return "ответ groq"

    def should_not_be_called(model, messages):
        raise AssertionError("не должен вызываться после успеха groq")

    monkeypatch.setitem(
        llm._PROVIDER_CHAINS, "fast",
        [("groq", groq_ok), ("gemini", should_not_be_called), ("openrouter", should_not_be_called)],
    )

    result = llm.call_llm([{"role": "user", "content": "hi"}], tier="fast")
    assert result.text == "ответ groq"
    assert result.provider == "groq"
    assert calls == ["groq"]


def test_call_llm_falls_back_to_next_provider_on_failure(monkeypatch):
    def groq_fails(model, messages):
        raise llm.ProviderError("429 rate limit")

    def gemini_ok(model, messages):
        return "ответ gemini"

    monkeypatch.setitem(
        llm._PROVIDER_CHAINS, "fast",
        [("groq", groq_fails), ("gemini", gemini_ok), ("openrouter", groq_fails)],
    )

    result = llm.call_llm([{"role": "user", "content": "hi"}], tier="fast")
    assert result.provider == "gemini"
    assert result.text == "ответ gemini"


def test_call_llm_all_providers_fail_raises(monkeypatch):
    def always_fails(model, messages):
        raise llm.ProviderError("недоступен")

    monkeypatch.setitem(
        llm._PROVIDER_CHAINS, "fast",
        [("groq", always_fails), ("gemini", always_fails), ("openrouter", always_fails)],
    )

    with pytest.raises(llm.ProviderError, match="Все провайдеры отказали"):
        llm.call_llm([{"role": "user", "content": "hi"}], tier="fast")


def test_call_llm_retries_same_provider_before_moving_on(monkeypatch):
    attempts = {"groq": 0}

    def groq_always_fails(model, messages):
        attempts["groq"] += 1
        raise llm.ProviderError("timeout")

    def gemini_ok(model, messages):
        return "ok"

    monkeypatch.setitem(
        llm._PROVIDER_CHAINS, "fast",
        [("groq", groq_always_fails), ("gemini", gemini_ok), ("openrouter", gemini_ok)],
    )

    llm.call_llm([{"role": "user", "content": "hi"}], tier="fast")
    # MAX_RETRIES_PER_PROVIDER=1 -> 2 попытки на groq перед переходом дальше
    assert attempts["groq"] == llm.MAX_RETRIES_PER_PROVIDER + 1


def test_call_llm_tool_choice_error_skips_retry_immediately(monkeypatch):
    """Спец-случай: модель проигнорировала запрет на tool-calling — повтор
    на этом же провайдере бессмыслен, должна быть ровно 1 попытка."""
    attempts = {"groq": 0}

    def groq_tool_choice_error(model, messages):
        attempts["groq"] += 1
        raise llm.ProviderError("Tool choice is none, but model called a tool")

    def gemini_ok(model, messages):
        return "ok"

    monkeypatch.setitem(
        llm._PROVIDER_CHAINS, "fast",
        [("groq", groq_tool_choice_error), ("gemini", gemini_ok), ("openrouter", gemini_ok)],
    )

    result = llm.call_llm([{"role": "user", "content": "hi"}], tier="fast")
    assert attempts["groq"] == 1
    assert result.provider == "gemini"


def test_call_llm_tier_fast_provider_order_is_groq_first():
    names = [name for name, _ in llm._PROVIDER_CHAINS["fast"]]
    assert names == ["groq", "gemini", "openrouter"]


def test_call_llm_tier_strong_provider_order_is_gemini_first():
    names = [name for name, _ in llm._PROVIDER_CHAINS["strong"]]
    assert names == ["gemini", "groq", "openrouter"]


def test_call_llm_returns_matching_model_for_tier(monkeypatch):
    def groq_ok(model, messages):
        assert model == llm.MODELS["groq"]["fast"]
        return "ok"

    monkeypatch.setitem(llm._PROVIDER_CHAINS, "fast", [("groq", groq_ok)])
    result = llm.call_llm([{"role": "user", "content": "hi"}], tier="fast")
    assert result.model == llm.MODELS["groq"]["fast"]