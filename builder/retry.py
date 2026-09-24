"""
Provider-agnostic retry for rate limits and oversized requests.

Two failures show up constantly in production and neither is a bug in the
prompt:

  * 429 / TPM rate limits - back off and retry.
  * 413 "request too large" - the provider counts `max_tokens` against the
    per-minute budget, so a generous max_tokens can make a small prompt
    unsendable. Groq's free tier caps TPM at 8000, which a 16k max_tokens
    blows on its own. We parse the provider's own numbers out of the error
    and shrink max_tokens to fit, rather than guessing a constant.
"""
from __future__ import annotations
import re, time

_LIMITS = re.compile(r"limit\s+(\d[\d,]*)\D+requested\s+(\d[\d,]*)", re.I)
_RETRY_AFTER = re.compile(r"try again in\s+([\d.]+)\s*s", re.I)
_RATE = ("rate_limit", "rate limit", "429", "too many requests",
         "overloaded", "503", "502", "timeout")
_TOO_BIG = ("request too large", "413", "reduce your message size",
            "maximum context length", "context_length_exceeded")

MIN_TOKENS = 2048
MAX_GROWTH = 32000


def _num(s: str) -> int:
    return int(s.replace(",", ""))


def _target(model):
    """Unwrap RunnableBinding (bind_tools) to reach the chat model itself."""
    seen = 0
    while hasattr(model, "bound") and seen < 5:
        model, seen = model.bound, seen + 1
    return model


def _get_max(m) -> tuple[str, int] | tuple[None, None]:
    for attr in ("max_tokens", "max_output_tokens", "num_predict"):
        v = getattr(m, attr, None)
        if isinstance(v, int) and v > 0:
            return attr, v
    return None, None


def _shrink(model, limit: int, requested: int, say) -> bool:
    m = _target(model)
    attr, cur = _get_max(m)
    if attr is None:
        return False
    new = max(MIN_TOKENS, cur - (requested - limit) - 256)
    if new >= cur:
        new = max(MIN_TOKENS, cur // 2)
    if new == cur:
        return False
    try:
        setattr(m, attr, new)
    except Exception:
        return False
    say("fail", f"  request too large ({requested} > {limit}); "
                f"{attr} {cur} -> {new}, retrying")
    return True


def invoke_with_retry(model, messages, max_attempts: int = 6, say=None):
    say = say or (lambda *a: None)
    delay = 2.0
    last = None
    for attempt in range(1, max_attempts + 1):
        try:
            return model.invoke(messages)
        except Exception as e:                       # provider SDKs differ
            last, msg = e, str(e)
            low = msg.lower()
            mm = _LIMITS.search(msg)
            limit, requested = (_num(mm.group(1)), _num(mm.group(2))) if mm else (None, None)

            if any(t in low for t in _TOO_BIG) and limit and requested:
                if _shrink(model, limit, requested, say):
                    continue

            if any(t in low for t in _RATE):
                ra = _RETRY_AFTER.search(msg)
                wait = float(ra.group(1)) + 0.5 if ra else delay
                # a TPM window is at most a minute; never sleep longer
                wait = min(wait, 60.0)
                say("fail", f"  rate limited, waiting {wait:.1f}s "
                            f"(attempt {attempt}/{max_attempts})")
                time.sleep(wait)
                delay = min(delay * 2, 30.0)
                continue
            raise
    raise RuntimeError(f"gave up after {max_attempts} attempts: {last}") from last


def grow_budget(model, say=None) -> bool:
    """Raise max_tokens after a truncated response.

    A truncated spine is not a modelling error and re-prompting at the same
    budget just truncates again, burning a repair attempt. Grow the budget
    instead, up to MAX_GROWTH.
    """
    say = say or (lambda *a: None)
    m = _target(model)
    attr, cur = _get_max(m)
    if attr is None or cur >= MAX_GROWTH:
        return False
    new = min(MAX_GROWTH, int(cur * 1.6))
    try:
        setattr(m, attr, new)
    except Exception:
        return False
    say("fail", f"  output truncated; {attr} {cur} -> {new}")
    return True
