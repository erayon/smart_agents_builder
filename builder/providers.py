"""
Multi-provider LLM factory for the spine builder.

Every provider is imported lazily so you only need the packages you actually
use. Keys come from the environment (.env is loaded by builder.cli).

    from builder.providers import get_llm, available_providers
    llm = get_llm("xai")                 # Grok, XAI_API_KEY
    llm = get_llm("anthropic", model="claude-opus-5")
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Callable, Any


@dataclass(frozen=True)
class Provider:
    name: str
    env_key: str | None           # env var holding the API key
    default_model: str
    package: str                  # pip package needed
    _build: Callable[..., Any] = field(repr=False, default=None)

    def has_key(self) -> bool:
        return self.env_key is None or bool(os.getenv(self.env_key))


# A spine for a rich domain runs ~3k output tokens, but reasoning models spend
# their budget on thinking first. Several providers default to 1-4k, which
# truncates the JSON mid-object, so set it explicitly everywhere.
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "16000"))


def _anthropic(model, temperature, **kw):
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(model=model, temperature=temperature,
                         max_tokens=kw.pop("max_tokens", MAX_TOKENS), **kw)


def _openai(model, temperature, **kw):
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=model, temperature=temperature,
                      max_tokens=kw.pop("max_tokens", MAX_TOKENS), **kw)


def _xai(model, temperature, **kw):
    """xAI Grok. Needs XAI_API_KEY and `pip install langchain-xai`."""
    from langchain_xai import ChatXAI
    return ChatXAI(model=model, temperature=temperature,
                   max_tokens=kw.pop("max_tokens", MAX_TOKENS), **kw)


def _groq(model, temperature, **kw):
    """Groq inference (not the same thing as xAI's Grok). GROQ_API_KEY."""
    from langchain_groq import ChatGroq
    return ChatGroq(model=model, temperature=temperature,
                    max_tokens=kw.pop("max_tokens", MAX_TOKENS), **kw)


def _google(model, temperature, **kw):
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(model=model, temperature=temperature,
                                  max_output_tokens=kw.pop("max_tokens", MAX_TOKENS), **kw)


def _ollama(model, temperature, **kw):
    """Local Ollama.

    Two settings matter far more here than for hosted providers:

    * num_ctx - Ollama defaults to 4096 for most models, which silently
      truncates our ~1.2k-token system prompt plus a ~5k-token spine. Running
      locally removes rate limits, NOT context limits.
    * format="json" - Ollama's native JSON mode. Small local models drift into
      prose without it; with it, the output is guaranteed parseable JSON.
    """
    from langchain_ollama import ChatOllama
    opts = dict(model=model, temperature=temperature,
                num_ctx=int(os.getenv("OLLAMA_NUM_CTX", "16384")),
                num_predict=kw.pop("max_tokens", MAX_TOKENS))
    if os.getenv("OLLAMA_JSON", "1") != "0" and not kw.get("tools"):
        opts["format"] = "json"
    if os.getenv("OLLAMA_BASE_URL"):
        opts["base_url"] = os.getenv("OLLAMA_BASE_URL")
    opts.update(kw)
    return ChatOllama(**opts)


def _openai_compat(model, temperature, **kw):
    """Any OpenAI-compatible endpoint: OpenRouter, DeepSeek, Together, vLLM."""
    from langchain_openai import ChatOpenAI
    base = kw.pop("base_url", None) or os.getenv("OPENAI_COMPAT_BASE_URL")
    key = kw.pop("api_key", None) or os.getenv("OPENAI_COMPAT_API_KEY")
    if not base:
        raise ValueError("openai_compat needs OPENAI_COMPAT_BASE_URL")
    return ChatOpenAI(model=model, temperature=temperature, base_url=base,
                      api_key=key, max_tokens=kw.pop("max_tokens", MAX_TOKENS), **kw)


def _mock(model, temperature, **kw):
    """Offline test double. Responses are supplied via kw['responses']."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    return FakeListChatModel(responses=kw.get("responses", ["{}"]))


# Default models are overridable with --model or <PROVIDER>_MODEL in .env.
# Check them against your provider's current catalogue before relying on them.
PROVIDERS: dict[str, Provider] = {
    "anthropic":     Provider("anthropic", "ANTHROPIC_API_KEY", "claude-sonnet-5",
                              "langchain-anthropic", _anthropic),
    "xai":           Provider("xai", "XAI_API_KEY", "grok-4",
                              "langchain-xai", _xai),
    "openai":        Provider("openai", "OPENAI_API_KEY", "gpt-4.1",
                              "langchain-openai", _openai),
    "google":        Provider("google", "GOOGLE_API_KEY", "gemini-2.5-pro",
                              "langchain-google-genai", _google),
    "groq":          Provider("groq", "GROQ_API_KEY", "llama-3.3-70b-versatile",
                              "langchain-groq", _groq),
    "ollama":        Provider("ollama", None, "llama3.1",
                              "langchain-ollama", _ollama),
    "openai_compat": Provider("openai_compat", "OPENAI_COMPAT_API_KEY", "gpt-4.1",
                              "langchain-openai", _openai_compat),
    "mock":          Provider("mock", None, "mock", "langchain-core", _mock),
}

# Order tried by get_llm("auto").
FALLBACK_ORDER = ["anthropic", "xai", "openai", "google", "groq", "ollama"]


def _importable(p: Provider) -> bool:
    import importlib.util
    return importlib.util.find_spec(p.package.replace("-", "_")) is not None


def available_providers() -> list[str]:
    """Providers whose API key is present in the environment."""
    return [n for n, p in PROVIDERS.items()
            if n not in ("mock", "openai_compat") and p.has_key() and _importable(p)]


def resolve(provider: str | None) -> Provider:
    provider = (provider or os.getenv("LLM_PROVIDER") or "auto").lower()
    if provider == "auto":
        for name in FALLBACK_ORDER:
            # in auto mode a provider must have BOTH its key and its package,
            # otherwise keyless providers (ollama) always win and then fail
            if PROVIDERS[name].has_key() and _importable(PROVIDERS[name]):
                return PROVIDERS[name]
        raise RuntimeError(
            "No provider key found. Set one of: "
            + ", ".join(p.env_key for p in PROVIDERS.values() if p.env_key)
            + "  (copy .env.example to .env)")
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider '{provider}'. "
                         f"Choose from: {', '.join(PROVIDERS)}")
    return PROVIDERS[provider]


def get_llm(provider: str | None = None, model: str | None = None,
            temperature: float = 0.0, **kw):
    """Build a LangChain chat model. temperature defaults to 0 (D24)."""
    p = resolve(provider)
    if not p.has_key():
        raise RuntimeError(f"{p.name}: environment variable {p.env_key} is not set")
    model = model or os.getenv(f"{p.name.upper()}_MODEL") or p.default_model
    try:
        llm = p._build(model, temperature, **kw)
    except ImportError as e:
        raise RuntimeError(
            f"{p.name} needs `pip install {p.package}` ({e})") from e
    setattr(llm, "_sab_provider", p.name)
    setattr(llm, "_sab_model", model)
    return llm
