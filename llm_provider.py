"""LLM provider layer — the ONLY place in the project that knows which AI service is used.

WHY: the site should be able to switch AI vendors (free NVIDIA tier today, a paid provider when the
site earns money) WITHOUT touching any code. Every provider listed below speaks the same
"OpenAI-compatible chat completions" protocol, so switching = changing 3 settings:

    LLM_API_KEY    the provider's key (a GitHub Secret — never in code or in CLAUDE.md)
    LLM_BASE_URL   the provider's API address (default: NVIDIA, https://integrate.api.nvidia.com/v1)
    LLM_MODELS     model names, comma separated, tried in order (first = preferred, rest = fallbacks)

Examples of other providers that work with the same code (just different values):
    OpenRouter  https://openrouter.ai/api/v1
    Groq        https://api.groq.com/openai/v1
    Together    https://api.together.xyz/v1
    OpenAI      https://api.openai.com/v1
(Anthropic's own API uses a different format; adding it later means adding one small function here.)

Nothing else in the project imports requests/openai for AI — everything goes through chat().
"""
import os
import re
import time

import requests

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODELS = "meta/llama-3.3-70b-instruct,qwen/qwen3.5-122b-a10b"
TIMEOUT_SECONDS = 90


class LLMNotConfigured(Exception):
    """No API key set — callers treat this as 'AI is off', not as an error."""


class LLMAuthError(Exception):
    """The key was rejected (401/403) — no point trying other models."""


def _setting(name, default=""):
    # Empty string counts as "not set" (GitHub passes unset variables as empty strings).
    return (os.environ.get(name) or "").strip() or default


def is_configured():
    return bool(_setting("LLM_API_KEY"))


def _models():
    return [m.strip() for m in _setting("LLM_MODELS", DEFAULT_MODELS).split(",") if m.strip()]


def _clean(text):
    # Some "reasoning" models prepend their thinking in <think>...</think> — never show that.
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL)
    return text.strip()


def chat(messages, max_tokens=900, temperature=0.2):
    """Send a chat request. Returns (text, model_used).

    Tries each model in LLM_MODELS in order. A rate limit (429) waits and retries once; an
    unavailable/unknown model (400/404/5xx) moves on to the next model; a rejected key stops at once.
    Raises LLMNotConfigured / LLMAuthError / RuntimeError (all models failed).
    """
    key = _setting("LLM_API_KEY")
    if not key:
        raise LLMNotConfigured("LLM_API_KEY is not set")
    base = _setting("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    last = "no models configured"
    for model in _models():
        for attempt in (1, 2):
            try:
                r = requests.post(
                    f"{base}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json={"model": model, "messages": messages, "max_tokens": max_tokens,
                          "temperature": temperature, "stream": False},
                    timeout=TIMEOUT_SECONDS,
                )
            except Exception as e:  # noqa: BLE001
                last = f"{model}: {type(e).__name__}: {e}"
                print(f"[llm] {last}")
                break                                   # network trouble -> next model
            if r.status_code in (401, 403):
                raise LLMAuthError(f"HTTP {r.status_code}: key rejected ({r.text[:160]})")
            if r.status_code == 429 and attempt == 1:
                print(f"[llm] {model}: rate limited, waiting 30s and retrying once")
                time.sleep(30)
                continue
            if r.status_code != 200:
                last = f"{model}: HTTP {r.status_code} {r.text[:200]}"
                print(f"[llm] {last}")
                break                                   # model unavailable -> next model
            try:
                text = _clean(r.json()["choices"][0]["message"]["content"])
            except Exception as e:  # noqa: BLE001
                last = f"{model}: unreadable response ({type(e).__name__})"
                print(f"[llm] {last}")
                break
            if text:
                return text, model
            last = f"{model}: empty answer"
            print(f"[llm] {last}")
            break
    raise RuntimeError(f"all models failed — last: {last}")
