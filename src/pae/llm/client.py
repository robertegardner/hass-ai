"""Sync Ollama chat client with structured output and primary->fallback failover.

Runs inside the RQ worker (SimpleWorker, in-process) — hence stdlib urllib,
no async. `format` carries a JSON schema: Ollama constrains decoding to it,
so content parses as JSON in the overwhelming majority of calls; a parse
failure is treated like any transport failure (retry, then fail over).
Model names containing ':cloud' are refused outright — Ollama would route
those to its hosted service, violating the local-only rule."""
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from pae.logging import get_logger

log = get_logger(__name__)

ATTEMPTS_PER_HOST = 2


class LLMError(Exception):
    pass


@dataclass
class LLMResponse:
    content: dict
    model: str
    host: str
    duration_seconds: float


class OllamaClient:
    def __init__(
        self,
        primary_url: str,
        fallback_url: str,
        primary_model: str,
        fallback_model: str,
        timeout_seconds: float = 120.0,
    ) -> None:
        for model in (primary_model, fallback_model):
            if ":cloud" in model:
                raise ValueError(f"model {model!r} is an Ollama cloud model — local only")
        self._targets = [(primary_url, primary_model), (fallback_url, fallback_model)]
        self._timeout = timeout_seconds

    def chat_json(self, messages: list[dict], schema: dict) -> LLMResponse:
        last_error: Exception | None = None
        for host, model in self._targets:
            for attempt in range(ATTEMPTS_PER_HOST):
                try:
                    return self._request(host, model, messages, schema)
                except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                    last_error = exc
                    log.warning(
                        "llm_attempt_failed",
                        host=host,
                        model=model,
                        attempt=attempt,
                        error=str(exc),
                    )
        raise LLMError(f"all Ollama hosts failed: {last_error}")

    def _request(self, host: str, model: str, messages: list[dict], schema: dict) -> LLMResponse:
        body = json.dumps(
            {
                "model": model,
                "messages": messages,
                "stream": False,
                "format": schema,
                "options": {"temperature": 0.2},
            }
        ).encode()
        req = urllib.request.Request(
            f"{host}/api/chat", data=body, headers={"Content-Type": "application/json"}
        )
        started = time.monotonic()
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            payload = json.load(resp)
        try:
            content = json.loads(payload["message"]["content"])
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError(f"unparseable Ollama response: {exc}") from exc
        if not isinstance(content, dict):
            raise ValueError("Ollama response content is not a JSON object")
        return LLMResponse(
            content=content,
            model=model,
            host=host,
            duration_seconds=time.monotonic() - started,
        )
