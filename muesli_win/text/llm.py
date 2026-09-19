"""One chat client for every provider Muesli supports.

Ollama and LM Studio both speak the OpenAI chat format, so a single code path
covers local and hosted. Used for transcript cleanup, meeting notes and Quill.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120


class LLMError(RuntimeError):
    pass


@dataclass
class LLMTarget:
    backend: str            # ollama | lmstudio | openai | openrouter | custom | local
    model: str
    url: str = ""
    api_key: str = ""

    def chat_url(self) -> str:
        if self.backend in ("ollama", "local"):
            base = (self.url or "http://localhost:11434").rstrip("/")
            return f"{base}/v1/chat/completions"     # Ollama's OpenAI-compatible route
        if self.backend == "lmstudio":
            base = (self.url or "http://localhost:1234").rstrip("/")
            return f"{base}/v1/chat/completions"
        if self.backend == "openai":
            return "https://api.openai.com/v1/chat/completions"
        if self.backend == "openrouter":
            return "https://openrouter.ai/api/v1/chat/completions"
        if self.backend == "custom":
            base = self.url.rstrip("/")
            if not base:
                raise LLMError("custom LLM URL is not set")
            return base if base.endswith("/chat/completions") else f"{base}/v1/chat/completions"
        raise LLMError(f"unknown LLM backend {self.backend!r}")

    @property
    def is_local(self) -> bool:
        return self.backend in ("ollama", "local", "lmstudio")


def target_from_config(cfg, purpose: str) -> LLMTarget:
    """purpose: 'post' | 'summary' | 'quill'"""
    if purpose == "post":
        backend = cfg.get("post_processor_backend", "local")
        model = (cfg.get(f"post_processor_{backend}_model", "")
                 or cfg.get(f"{backend}_model", "")
                 or cfg.get("ollama_model", "qwen3.5"))
    elif purpose == "summary":
        backend = cfg.get("meeting_summary_backend", "openrouter")
        model = cfg.get("meeting_summary_model", "") or cfg.get(f"{backend}_model", "")
    else:
        backend = cfg.get("quill_backend", "ollama")
        model = cfg.get("quill_model", "") or cfg.get(f"{backend}_model", "")

    if backend == "local":
        backend = "ollama"
    url = {"ollama": cfg.get("ollama_url", ""), "lmstudio": cfg.get("lmstudio_url", ""),
           "custom": cfg.get("custom_llm_url", "")}.get(backend, "")
    key = {"openai": cfg.get("openai_api_key", ""),
           "openrouter": cfg.get("openrouter_api_key", ""),
           "custom": cfg.get("custom_llm_api_key", "")}.get(backend, "")
    if not model and backend == "ollama":
        model = cfg.get("ollama_model", "qwen3.5")
    return LLMTarget(backend=backend, model=model, url=url, api_key=key)


def chat(target: LLMTarget, system: str, user: str, *, temperature: float = 0.0,
         timeout: int = DEFAULT_TIMEOUT, max_tokens: int | None = None) -> str:
    try:
        import requests
    except ImportError as exc:
        raise LLMError("requests is not installed") from exc

    if not target.model:
        raise LLMError(f"no model configured for {target.backend}")
    if not target.is_local and not target.api_key:
        raise LLMError(f"{target.backend} needs an API key (Settings > Models)")

    headers = {"Content-Type": "application/json"}
    if target.api_key:
        headers["Authorization"] = f"Bearer {target.api_key}"
    if target.backend == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/"
        headers["X-Title"] = "Muesli for Windows"

    payload = {
        "model": target.model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens

    try:
        resp = requests.post(target.chat_url(), headers=headers, json=payload,
                             timeout=timeout)
    except Exception as exc:
        hint = ""
        if target.is_local:
            hint = " - is Ollama running? (ollama serve)"
        raise LLMError(f"could not reach {target.backend}{hint}: {exc}") from exc

    if resp.status_code != 200:
        raise LLMError(f"{target.backend} returned {resp.status_code}: {resp.text[:300]}")
    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"unexpected response from {target.backend}: {resp.text[:200]}") from exc
    return _strip_reasoning(str(content or ""))


def _strip_reasoning(text: str) -> str:
    """Reasoning models emit <think>...</think>; it must never reach the document."""
    import re
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"^\s*<think>.*", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def probe(target: LLMTarget, timeout: int = 10) -> tuple[bool, str]:
    """Used by Settings to show a live red/green next to the backend."""
    try:
        out = chat(target, "Reply with the single word: ok", "ping", timeout=timeout,
                   max_tokens=8)
        return True, out[:40] or "ok"
    except LLMError as exc:
        return False, str(exc)


def list_ollama_models(url: str = "http://localhost:11434") -> list[str]:
    try:
        import requests
        r = requests.get(f"{url.rstrip('/')}/api/tags", timeout=5)
        if r.status_code != 200:
            return []
        return sorted(m["name"] for m in r.json().get("models", []))
    except Exception:
        return []
