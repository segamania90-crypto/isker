"""
Единая точка входа для вызова LLM.

Цепочка провайдеров: Groq -> OpenRouter -> Gemini.
Каждый новый вызов call_llm() снова начинает с Groq (не "залипает" на
последнем рабочем провайдере) — так более свежие free-лимиты Groq
используются в первую очередь.

Иерархия моделей заложена через параметр tier:
    tier="fast"   -> маленькая быстрая модель (поиск файла, классификация)
    tier="strong" -> сильная модель (рефакторинг, генерация большого кода)

Формат ответа всегда нормализован до простого dict:
    {"text": str, "provider": str, "model": str}

Мы намеренно НЕ используем нативный function-calling API конкретных
провайдеров — у бесплатных моделей поддержка нестабильна и форматы
отличаются. Вместо этого агент (agent/loop.py) просит модель отвечать
структурированным JSON прямо в тексте, а разбор JSON делаем сами.
Это и есть "нормализация ответов", о которой речь в roadmap.
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
        "fast": os.getenv("GROQ_MODEL_FAST", "llama-3.1-8b-instant"),
        "strong": os.getenv("GROQ_MODEL_STRONG", "llama-3.3-70b-versatile"),
    },
    "openrouter": {
        "fast": os.getenv("OPENROUTER_MODEL_FAST", "meta-llama/llama-3.1-8b-instruct:free"),
        "strong": os.getenv("OPENROUTER_MODEL_STRONG", "meta-llama/llama-3.3-70b-instruct:free"),
    },
    "gemini": {
        "fast": os.getenv("GEMINI_MODEL_FAST", "gemini-1.5-flash"),
        "strong": os.getenv("GEMINI_MODEL_STRONG", "gemini-1.5-pro"),
    },
}

REQUEST_TIMEOUT = 30  # секунд
MAX_RETRIES_PER_PROVIDER = 1  # без задержанных ретраев здесь — это Этап 6


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
        raise ProviderError("GEMINI_API_KEY не задан")

    # Gemini использует свой формат: contents с role user/model, без "system"
    # как отдельного сообщения в chat-массиве -> сворачиваем system в первый user.
    system_parts = [m["content"] for m in messages if m["role"] == "system"]
    contents = []
    prefix = ("\n\n".join(system_parts) + "\n\n") if system_parts else ""
    for m in messages:
        if m["role"] == "system":
            continue
        role = "model" if m["role"] == "assistant" else "user"
        text = m["content"]
        if prefix and role == "user":
            text = prefix + text
            prefix = ""
        contents.append({"role": role, "parts": [{"text": text}]})

    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": api_key},
        json={"contents": contents},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code == 429:
        raise ProviderError("429 rate limit (gemini)")
    if resp.status_code >= 400:
        raise ProviderError(f"HTTP {resp.status_code} from gemini: {resp.text[:300]}")
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


# Порядок цепочки провайдеров разный для fast/strong:
# fast   -> Groq первым (скорость важнее, вызовов много за одну задачу)
# strong -> Gemini первым (качество важнее, вызовов мало, лимит Gemini большой)
# OpenRouter в обоих случаях последний резерв — его дневной лимит самый маленький.
_PROVIDER_CHAINS = {
        "fast": [
        ("groq", _call_groq),
        ("gemini", _call_gemini),
        ("openrouter", _call_openrouter),
    ],
    
    "strong": [
        ("gemini", _call_gemini),
        ("groq", _call_groq),
        ("openrouter", _call_openrouter),
    ],
}


def call_llm(messages: list[dict], tier: str = "fast") -> LLMResponse:
    """
    Отправляет запрос по цепочке провайдеров, начиная с Groq.
    Переключается на следующего при 429 / таймауте / любой ошибке провайдера.

    messages: список {"role": "system"|"user"|"assistant", "content": str}
    tier: "fast" или "strong" — выбирает модель под сложность шага.
    """
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
                # Модель проигнорировала запрет на нативный tool-calling — это её
                # стабильное поведение, не разовый сбой. Повтор на этом же
                # провайдере/модели почти гарантированно провалится тем же
                # образом, поэтому сразу переходим к следующему провайдеру,
                # не тратя вторую попытку впустую.
                if "Tool choice is none, but model called a tool" in str(e):
                    break
                time.sleep(0.5)
                continue

    raise ProviderError(f"Все провайдеры отказали. Последняя ошибка: {last_error}")
