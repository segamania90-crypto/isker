"""
Единая точка входа для вызова LLM.

Цепочка провайдеров: для fast — Groq -> Gemini -> OpenRouter; для strong — OpenRouter -> Groq -> Gemini.
Каждый новый вызов call_llm() снова начинает с первого доступного провайдера.

Иерархия моделей заложена через параметр tier:
    tier="fast"   -> маленькая быстрая модель (поиск файла, классификация)
    tier="strong" -> сильная модель (рефакторинг, генерация большого кода)

Формат ответа всегда нормализован до простого dict:
    {"text": str, "provider": str, "model": str}
"""

from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass

import requests

logger = logging.getLogger("agent.llm")


class ProviderError(Exception):
    """Провайдер отказал (429, таймаут, недоступен и т.п.)."""


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str


# ---------------------------------------------------------------------------
# Конфигурация моделей по уровням (tier). Меняется через .env при желании.
# ---------------------------------------------------------------------------

MODELS = {
    "groq": {
        "fast": os.getenv("GROQ_MODEL_FAST", "openai/gpt-oss-20b"),
        "strong": os.getenv("GROQ_MODEL_STRONG", "openai/gpt-oss-120b"),
    },
    "gemini": {
        "fast": os.getenv("GEMINI_MODEL_FAST", "gemini-2.5-flash"),
        "strong": os.getenv("GEMINI_MODEL_STRONG", "gemini-2.5-pro"),
    },
    "openrouter": {
        "fast": os.getenv("OPENROUTER_MODEL_FAST", "meta-llama/llama-3.1-8b-instruct:free"),
        "strong": os.getenv("OPENROUTER_MODEL_STRONG", "meta-llama/llama-3.3-70b-instruct:free"),
    },
}

REQUEST_TIMEOUT = 60  # секунд
MAX_RETRIES_PER_PROVIDER = 1
CHAIN_RETRY_DELAYS = [5, 15]  # секунды ожидания, если отказали все провайдеры разом


def _call_openai_compatible(base_url: str, api_key: str, model: str, messages: list[dict]) -> str:
    resp = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 429:
        raise ProviderError(f"429 rate limit ({base_url})")
    if resp.status_code >= 400:
        raise ProviderError(f"HTTP {resp.status_code} from {base_url}: {resp.text[:300]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_groq(model: str, messages: list[dict]) -> str:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ProviderError("GROQ_API_KEY не задан")
    return _call_openai_compatible("https://api.groq.com/openai/v1", api_key, model, messages)


def _call_openrouter(model: str, messages: list[dict]) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ProviderError("OPENROUTER_API_KEY не задан")
    return _call_openai_compatible("https://openrouter.ai/api/v1", api_key, model, messages)


def _call_gemini(model: str, messages: list[dict]) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ProviderError("GEMINI_API_KEY is not set")
    return _call_openai_compatible("https://generativelanguage.googleapis.com/v1beta/openai", api_key, model, messages)


# Цепочки провайдеров для разных уровней (tier):
_PROVIDER_CHAINS = {
    "fast": [
        ("groq", _call_groq),
        ("gemini", _call_gemini),
        ("openrouter", _call_openrouter),
    ],
    "strong": [
        ("openrouter", _call_openrouter),
        ("groq", _call_groq),
        ("gemini", _call_gemini),
    ],
}


def _try_chain_once(messages: list[dict], tier: str) -> LLMResponse | None:
    """Один проход по всей цепочке провайдеров. None, если все отказали."""
    last_error: Exception | None = None
    chain = _PROVIDER_CHAINS.get(tier, _PROVIDER_CHAINS["fast"])

    for provider_name, call_fn in chain:
        model = MODELS[provider_name][tier]
        for attempt in range(MAX_RETRIES_PER_PROVIDER + 1):
            try:
                text = call_fn(model, messages)
                logger.info("LLM OK provider=%s model=%s tier=%s", provider_name, model, tier)
                return LLMResponse(text=text, provider=provider_name, model=model)
            except (ProviderError, requests.Timeout, requests.RequestException) as e:
                last_error = e
                logger.warning(
                    "LLM FAIL provider=%s model=%s tier=%s attempt=%s reason=%s",
                    provider_name, model, tier, attempt, e,
                )
                err_str = str(e)
                if err_str.startswith("429") or "429 rate limit" in err_str:
                    break
                if "Tool choice is none, but model called a tool" in err_str:
                    break
                time.sleep(0.5)
                continue

    _try_chain_once.last_error = last_error
    return None


def call_llm(messages: list[dict], tier: str = "fast") -> LLMResponse:
    """
    Отправляет запрос по цепочке провайдеров.
    Если вся цепочка отказала — ждёт и пробует заново.
    """
    result = _try_chain_once(messages, tier)
    if result is not None:
        return result

    for delay in CHAIN_RETRY_DELAYS:
        logger.warning("Вся цепочка провайдеров отказала, жду %s сек перед повтором", delay)
        time.sleep(delay)
        result = _try_chain_once(messages, tier)
        if result is not None:
            return result

    raise ProviderError(f"Все провайдеры отказали. Последняя ошибка: {_try_chain_once.last_error}")
