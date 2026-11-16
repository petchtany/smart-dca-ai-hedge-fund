"""LLM provider protocol + the concrete clients."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol, runtime_checkable

import requests

from hedge_fund.llm import contract
from hedge_fund.llm.registry import (
    env_var_for,
    is_supported,
    provider_for,
    SUPPORTED_PROVIDERS,
)

DEFAULT_MODEL = "gemini-2.5-flash"

# Called with each piece of text as it arrives. None means don't stream.
TokenListener = Callable[[str], None] | None

# Google Gemini free-tier model pool (each has its own separate rate/daily quota)
_GOOGLE_POOL = (
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-3.8-flash",
)
_GOOGLE_LOCK = threading.Lock()
_GOOGLE_RR_INDEX = 0
_GOOGLE_EXHAUSTED: set[str] = set()


class LLMParseError(ValueError):
    """The model's response did not contain parseable JSON."""


class LLMCallError(RuntimeError):
    """Provider failure with optional, credential-free persistence context."""

    def __init__(self, message: str, diagnostic_record: dict | None = None) -> None:
        super().__init__(message)
        self.diagnostic_record = diagnostic_record


@runtime_checkable
class LLMClient(Protocol):
    """Protocol all LLM providers must satisfy."""

    model: str

    def complete(self, system: str, user: str) -> str: ...


class ChatLLM:
    """A langchain chat model behind the LLMClient protocol."""

    def __init__(self, model: str, chat, on_token: TokenListener = None) -> None:
        self.model = model
        self._chat = chat
        self._on_token = on_token

    def complete(self, system: str, user: str) -> str:
        messages = [("system", system), ("human", user)]
        if self._on_token is None:
            return _flatten(self._chat.invoke(messages).content)

        parts: list[str] = []
        for chunk in self._chat.stream(messages):
            text = _flatten(chunk.content, sep="")
            if text:
                parts.append(text)
                self._on_token(text)
        return "".join(parts)


class GoogleRotatingLLM:
    """Google Gemini client with automatic round-robin load balancing and
    quota-exhaustion fallback across all available Gemini Flash/Pro models."""

    def __init__(
        self,
        model: str,
        api_key: str,
        timeout: float = 60.0,
        on_token: TokenListener = None,
    ) -> None:
        self.model = model
        self._api_key = api_key
        self._timeout = timeout
        self._on_token = on_token

    def _ordered_candidates(self) -> list[str]:
        global _GOOGLE_RR_INDEX
        with _GOOGLE_LOCK:
            active = [m for m in _GOOGLE_POOL if m not in _GOOGLE_EXHAUSTED]
            if not active:
                active = list(_GOOGLE_POOL)
            start = _GOOGLE_RR_INDEX % len(active)
            _GOOGLE_RR_INDEX += 1
            rotated = active[start:] + active[:start]
            if self.model not in _GOOGLE_EXHAUSTED and self.model not in rotated:
                rotated.insert(0, self.model)
            return rotated

    def complete(self, system: str, user: str) -> str:
        from langchain_google_genai import ChatGoogleGenerativeAI

        messages = [("system", system), ("human", user)]
        last_exc: Exception | None = None
        candidates = self._ordered_candidates()

        for candidate in candidates:
            try:
                chat = ChatGoogleGenerativeAI(
                    model=candidate,
                    api_key=self._api_key,
                    timeout=self._timeout,
                    max_retries=0,
                    api_version="v1beta",
                )
                if self._on_token is None:
                    return _flatten(chat.invoke(messages).content)

                parts: list[str] = []
                for chunk in chat.stream(messages):
                    text = _flatten(chunk.content, sep="")
                    if text:
                        parts.append(text)
                        self._on_token(text)
                return "".join(parts)
            except Exception as exc:
                last_exc = exc
                err_str = str(exc)
                if "PerDay" in err_str or "NOT_FOUND" in err_str or "404" in err_str:
                    with _GOOGLE_LOCK:
                        _GOOGLE_EXHAUSTED.add(candidate)
                elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    time.sleep(1.0)
                continue

        if last_exc is not None:
            raise last_exc
        raise LLMCallError("All Google Gemini candidate models failed")


class JevLLM:
    """Native TypeSafe adapter returning compatibility JSON without streaming."""

    ENDPOINT = "https://api.typesafe.ai/v1/systemone"
    _RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 529})

    def __init__(self, api_key: str, model: str = "jev-1.13.0", timeout: float = 60.0) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("A non-empty TypeSafe API key is required")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        self.model = model
        self._api_key = api_key
        self._timeout = timeout

    def cache_key(self, agent: str, system: str, user: str) -> str:
        """Hash semantic inputs only; do not disturb legacy prompt keys."""
        identity = {
            "provider": "TypeSafe",
            "endpoint": self.ENDPOINT,
            "agent": agent,
            "request": contract.build_jev_request(system, user, self.model),
            "contract_version": contract.JEV_CONTRACT_VERSION,
        }
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:24]

    def complete(self, system: str, user: str) -> str:
        request = contract.build_jev_request(system, user, self.model)
        started = time.monotonic()
        diagnostics = {
            "provider": "TypeSafe",
            "contract_version": contract.JEV_CONTRACT_VERSION,
            "request": request,
            "attempt_count": 0,
            "status_code": None,
            "raw_response": None,
        }

        def fail(category: str, message: str) -> None:
            diagnostics.update(failure_category=category, elapsed_seconds=time.monotonic() - started)
            raise LLMCallError(message, self._redact(diagnostics)) from None

        for attempt in range(1, 3):
            diagnostics["attempt_count"] = attempt
            try:
                with requests.post(
                    self.ENDPOINT,
                    json=request,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=self._timeout,
                    allow_redirects=False,
                ) as response:
                    status = response.status_code
                    raw = response.text
                    retry_after = response.headers.get("Retry-After")
            except requests.RequestException:
                fail("transport", "TypeSafe request failed during transport")

            diagnostics.update(status_code=status, raw_response=raw)
            if status in self._RETRY_STATUSES and attempt == 1:
                delay = _retry_delay(retry_after)
                if delay > self._timeout:
                    fail("http", f"TypeSafe HTTP {status}: retry delay exceeds timeout")
                time.sleep(delay)
                continue
            if not 200 <= status < 300:
                fail("http", f"TypeSafe returned HTTP {status}")
            try:
                native_response = json.loads(raw)
            except ValueError:
                fail("json_decode", "TypeSafe returned invalid JSON")
            try:
                payload, metadata = contract.normalize_jev_response(native_response)
            except contract.JevContractError as exc:
                fail("contract", f"TypeSafe response failed validation: {exc}")

            metadata.update(diagnostics, elapsed_seconds=time.monotonic() - started)
            payload["provider_metadata"] = {"jev": self._redact(metadata)}
            return json.dumps(payload)

        raise AssertionError("unreachable: every attempt returns, retries, or raises")

    def _redact(self, value):
        """Protect against a service echoing the API key in an error body."""
        if isinstance(value, str):
            return value.replace(self._api_key, "[REDACTED]")
        if isinstance(value, dict):
            return {self._redact(key): self._redact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact(item) for item in value]
        return value


def _retry_delay(header: str | None) -> float:
    """One-second backoff, honoring valid delta-seconds and HTTP-date values."""
    if header is not None:
        try:
            seconds = float(header)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(header)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                return 1.0
        if math.isfinite(seconds):
            return max(1.0, seconds)
    return 1.0


def make_llm(
    model: str | None = None,
    timeout: float = 60.0,
    max_tokens: int = 4096,
    on_token: TokenListener = None,
) -> LLMClient:
    """Build the client for a model id, routed by the registry's provider."""
    model = model or os.environ.get("HEDGE_FUND_LLM_MODEL") or DEFAULT_MODEL
    provider = provider_for(model)
    if provider is None:
        provider = "Anthropic"
    if not is_supported(provider):
        raise ValueError(
            f"No v2 client for {provider} (model {model}). "
            f"Supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}."
        )

    api_key = _require_key(provider)

    if provider == "TypeSafe":
        return JevLLM(api_key=api_key, model=model, timeout=timeout)

    if provider == "Google":
        return GoogleRotatingLLM(model=model, api_key=api_key, timeout=timeout, on_token=on_token)

    if provider == "Anthropic":
        from langchain_anthropic import ChatAnthropic
        chat = ChatAnthropic(model=model, api_key=api_key, timeout=timeout,
                             max_retries=1, max_tokens=max_tokens)
    elif provider == "OpenAI":
        from langchain_openai import ChatOpenAI
        chat = ChatOpenAI(model=model, api_key=api_key, timeout=timeout,
                          max_retries=1, base_url=os.getenv("OPENAI_API_BASE"))
    elif provider == "DeepSeek":
        from langchain_deepseek import ChatDeepSeek
        chat = ChatDeepSeek(model=model, api_key=api_key, timeout=timeout,
                            max_retries=1)
    elif provider == "xAI":
        from langchain_xai import ChatXAI
        chat = ChatXAI(model=model, api_key=api_key, timeout=timeout,
                       max_retries=1)
    elif provider == "Kimi":
        from langchain_openai import ChatOpenAI
        chat = ChatOpenAI(
            model=model, api_key=api_key, timeout=timeout, max_retries=1,
            base_url=(os.getenv("MOONSHOT_BASE_URL")
                      or "https://api.moonshot.ai/v1"))
    else:  # pragma: no cover - SUPPORTED_PROVIDERS is checked above
        raise ValueError(f"Unhandled provider {provider}")

    return ChatLLM(model, chat, on_token)


def AnthropicLLM(model: str | None = None, **kwargs) -> LLMClient:  # noqa: N802
    """Back-compat shim: v2 was Anthropic-only, and this name is exported."""
    return make_llm(model or DEFAULT_MODEL, **kwargs)


def _flatten(content, sep: str = "\n") -> str:
    """One provider's response as plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return sep.join(parts)
    return "" if content is None else str(content)


def _require_key(provider: str) -> str:
    """The provider's API key, or a failure that names the variable to set."""
    env_var = env_var_for(provider)
    key = (os.getenv("MOONSHOT_API_KEY") if provider == "Kimi" else None)
    key = key or (os.getenv(env_var) if env_var else None)
    if not key:
        raise ValueError(
            f"{env_var} not found. Set it in your .env to use {provider} models."
        )
    return key


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of an LLM response."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1))
        except json.JSONDecodeError:
            pass

    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break

    raise LLMParseError(f"no JSON object found in response: {text[:200]!r}")
