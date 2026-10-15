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

LESSON LEARNED (CLAUDE.md): NVIDIA's free catalogue is unstable — models are retired every few weeks (HTTP 410),
many listed models answer 404 "not found for account", big ones time out or return 503. So:
  * run `python ai_digest.py --probe` (workflow "AI Digest (manual test)" -> mode "probe") to see which models
    ACTUALLY work for your key right now, then put the working ones in the LLM_MODELS variable;
  * "thinking" is switched off (reasoning models otherwise burn the whole token budget thinking and return nothing);
  * the whole attempt has a time budget, so a bad day at the provider never stalls the news pipeline.
"""
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODELS = "nvidia/nemotron-3.5-lightning-30b-a3b,nvidia/nemotron-3-super-120b-a12b"
TIMEOUT_SECONDS = 60
DEFAULT_BUDGET_SECONDS = 150          # total time chat() may spend across all models

_GOOD = ("nemotron", "llama", "deepseek", "gpt-oss", "qwen", "glm", "mistral", "gemma", "kimi", "minimax", "phi")
_BAD = ("guard", "safety", "embed", "rerank", "vision", "-vl", "ocr", "parse", "clip", "reward", "topic-control",
        "whisper", "tts", "asr", "omni", "retriev", "content", "translate", "image", "video", "audio", "nemoretriever")


class LLMNotConfigured(Exception):
    """No API key set — callers treat this as 'AI is off', not as an error."""


class LLMAuthError(Exception):
    """The key was rejected (401/403) — no point trying other models."""


class _Skip(Exception):
    """This model can't answer right now — try the next one."""


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


def _size_key(model_id):
    """Prefer mid-sized models (fast and good enough): 7B-130B first, smallest first; giants and tiny ones last."""
    m = re.search(r"(\d+(?:\.\d+)?)b", model_id.lower())
    size = float(m.group(1)) if m else 50.0
    return (0 if 7 <= size <= 130 else 1, size)


def _list_models(base, key):
    try:
        r = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"}, timeout=20)
        ids = [m.get("id", "") for m in r.json().get("data", [])]
    except Exception as e:  # noqa: BLE001
        print(f"[llm] could not list the provider's models ({type(e).__name__})")
        return []
    ok = [i for i in ids if i and any(g in i.lower() for g in _GOOD) and not any(b in i.lower() for b in _BAD)]
    ok.sort(key=_size_key)
    return ok


def _post(base, key, model, messages, max_tokens, temperature, timeout, thinking_off=True):
    body = {"model": model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stream": False}
    if thinking_off:
        body["chat_template_kwargs"] = {"enable_thinking": False}     # ignored by models without thinking
    return requests.post(f"{base}/chat/completions",
                         headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                         json=body, timeout=timeout)


def _try_model(base, key, model, messages, max_tokens, temperature):
    thinking_off = True
    for attempt in (1, 2, 3):
        try:
            r = _post(base, key, model, messages, max_tokens, temperature, TIMEOUT_SECONDS, thinking_off)
        except Exception as e:  # noqa: BLE001
            raise _Skip(f"{model}: {type(e).__name__}: {str(e)[:120]}")
        if r.status_code in (401, 403):
            raise LLMAuthError(f"HTTP {r.status_code}: key rejected ({r.text[:160]})")
        if r.status_code == 400 and thinking_off:
            thinking_off = False                              # this model dislikes the extra option — retry plain
            continue
        if r.status_code == 429 and attempt < 3:
            print(f"[llm] {model}: rate limited, waiting 20s and retrying")
            time.sleep(20)
            continue
        if r.status_code != 200:
            raise _Skip(f"{model}: HTTP {r.status_code} {r.text[:140]}")
        try:
            text = _clean(r.json()["choices"][0]["message"]["content"])
        except Exception as e:  # noqa: BLE001
            raise _Skip(f"{model}: unreadable response ({type(e).__name__})")
        if not text:
            raise _Skip(f"{model}: empty answer")
        return text
    raise _Skip(f"{model}: gave no usable answer after retries")


def chat(messages, max_tokens=2500, temperature=0.2):
    """Send a chat request. Returns (text, model_used).

    Tries each model in LLM_MODELS in order within a total time budget. If none works and LLM_MODELS was not
    set explicitly, the provider's own model list is consulted and a few mid-sized chat models are tried.
    A rejected key (401/403) stops at once.
    Raises LLMNotConfigured / LLMAuthError / RuntimeError (all models failed).
    """
    key = _setting("LLM_API_KEY")
    if not key:
        raise LLMNotConfigured("LLM_API_KEY is not set")
    base = _setting("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    deadline = time.time() + float(_setting("LLM_BUDGET_SECONDS", str(DEFAULT_BUDGET_SECONDS)))
    tried, last = [], "no models configured"

    def run(models):
        nonlocal last
        for model in models:
            if time.time() > deadline:
                last = f"time budget used up before trying {model}"
                print(f"[llm] {last}")
                return None
            tried.append(model)
            try:
                return _try_model(base, key, model, messages, max_tokens, temperature), model
            except _Skip as e:
                last = str(e)
                print(f"[llm] {last}")
        return None

    got = run(_models())
    if not got and not _setting("LLM_MODELS") and time.time() < deadline:
        extra = [m for m in _list_models(base, key) if m not in tried][:15]
        print(f"[llm] trying models the provider lists: {', '.join(extra[:15]) or 'none'}")
        got = run(extra)
    if got:
        return got
    raise RuntimeError(f"all models failed — last: {last}")


def probe():
    """Test which of the provider's models really answer for THIS key right now. Prints a ready-to-use
    LLM_MODELS value. Run it from the 'AI Digest (manual test)' workflow, mode = probe."""
    key = _setting("LLM_API_KEY")
    if not key:
        raise LLMNotConfigured("LLM_API_KEY is not set")
    base = _setting("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    ids = _list_models(base, key)
    print(f"[probe] testing {len(ids)} chat-capable models with a tiny question (4 at a time)...")
    msgs = [{"role": "user", "content": "Reply with exactly: OK"}]

    def one(mid):
        t0 = time.time()
        try:
            r = _post(base, key, mid, msgs, 20, 0.0, 40, True)
            if r.status_code == 400:
                r = _post(base, key, mid, msgs, 20, 0.0, 40, False)
            dt = time.time() - t0
            if r.status_code == 200:
                text = _clean(r.json()["choices"][0]["message"]["content"])
                return mid, ("WORKS" if text else "EMPTY"), dt, text[:20]
            return mid, f"HTTP {r.status_code}", dt, ""
        except Exception as e:  # noqa: BLE001
            return mid, type(e).__name__, time.time() - t0, ""

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, ids))
    for mid, status, dt, text in results:
        print(f"[probe] {status:<14} {dt:5.1f}s  {mid}  {text!r}")
    good = sorted([r for r in results if r[1] == "WORKS"], key=lambda r: r[2])
    if not good:
        print("[probe] NO model answered. Check the key, or try again later (the provider may be overloaded).")
        return []
    names = [r[0] for r in good]
    print("\n[probe] WORKING models, fastest first. Put this in the LLM_MODELS variable:\n")
    print("LLM_MODELS=" + ",".join(names[:4]))
    return names
