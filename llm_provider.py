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
DEFAULT_MODELS = "nvidia/nemotron-3.5-lightning-30b-a3b,nvidia/nemotron-3-super-120b-a12b,deepseek-ai/deepseek-v4-flash"
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


_GOOD = ("nemotron", "llama", "deepseek", "gpt-oss", "qwen", "glm", "mistral", "gemma", "kimi", "minimax")
_BAD = ("guard", "safety", "embed", "rerank", "vision", "-vl", "ocr", "parse", "clip", "reward", "topic-control",
        "whisper", "tts", "asr", "omni", "retriev", "content", "translate", "image", "video", "audio")


class _Skip(Exception):
    """This model can't answer right now — try the next one."""


def _discover(base, key, already_tried):
    """Models change fast (NVIDIA retires some every few weeks). When every configured model fails,
    ask the provider what it currently offers and try a few sensible chat models from that list."""
    try:
        r = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        ids = [m.get("id", "") for m in r.json().get("data", [])]
    except Exception as e:  # noqa: BLE001
        print(f"[llm] could not list the provider's models ({type(e).__name__})")
        return []
    ok = [i for i in ids if i and i not in already_tried
          and any(g in i.lower() for g in _GOOD) and not any(b in i.lower() for b in _BAD)]
    ok.sort(key=lambda i: min((n for n, g in enumerate(_GOOD) if g in i.lower()), default=99))
    print(f"[llm] provider lists {len(ids)} models; will also try: {', '.join(ok[:6]) or 'none'}")
    return ok[:6]


def _try_model(base, key, model, messages, max_tokens, temperature):
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
            raise _Skip(f"{model}: {type(e).__name__}: {e}")
        if r.status_code in (401, 403):
            raise LLMAuthError(f"HTTP {r.status_code}: key rejected ({r.text[:160]})")
        if r.status_code == 429 and attempt == 1:
            print(f"[llm] {model}: rate limited, waiting 30s and retrying once")
            time.sleep(30)
            continue
        if r.status_code != 200:
            raise _Skip(f"{model}: HTTP {r.status_code} {r.text[:160]}")
        try:
            text = _clean(r.json()["choices"][0]["message"]["content"])
        except Exception as e:  # noqa: BLE001
            raise _Skip(f"{model}: unreadable response ({type(e).__name__})")
        if not text:
            raise _Skip(f"{model}: empty answer (a reasoning model may have run out of tokens)")
        return text
    raise _Skip(f"{model}: rate limited twice")


def chat(messages, max_tokens=3000, temperature=0.2):
    """Send a chat request. Returns (text, model_used).

    Tries each model in LLM_MODELS in order; a model that is retired/unavailable/slow moves on to the next.
    If none works and LLM_MODELS was not set explicitly, the provider's own model list is consulted and a few
    suitable chat models are tried. A rejected key (401/403) stops at once.
    Raises LLMNotConfigured / LLMAuthError / RuntimeError (all models failed).
    """
    key = _setting("LLM_API_KEY")
    if not key:
        raise LLMNotConfigured("LLM_API_KEY is not set")
    base = _setting("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    tried, last = [], "no models configured"

    def run(models):
        nonlocal last
        for model in models:
            tried.append(model)
            try:
                return _try_model(base, key, model, messages, max_tokens, temperature), model
            except _Skip as e:
                last = str(e)
                print(f"[llm] {last}")
        return None

    got = run(_models())
    if not got and not _setting("LLM_MODELS"):
        got = run(_discover(base, key, tried))
    if got:
        return got
    raise RuntimeError(f"all models failed — last: {last}")
