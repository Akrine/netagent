"""
core/tracer.py

LangChain-style observability for Savvy's LLM calls.

Wraps any LLM backend and records a structured trace for every call:
- prompt sent (system + messages)
- response received
- latency in milliseconds
- token estimates
- backend and model used
- success or failure

Traces are written to logs/llm_traces.ndjson by default.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.llm import BaseLLMBackend, LLMResponse


_DEFAULT_TRACE_PATH = Path("logs/llm_traces.ndjson")


class LLMTrace:
    def __init__(self, trace_id, backend, model, system_prompt, messages, response, latency_ms, success, error=None):
        self.trace_id = trace_id
        self.backend = backend
        self.model = model
        self.system_prompt = system_prompt
        self.messages = messages
        self.response = response
        self.latency_ms = latency_ms
        self.success = success
        self.error = error
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self):
        prompt_chars = len(self.system_prompt) + sum(len(m.get("content","")) for m in self.messages)
        response_chars = len(self.response) if self.response else 0
        return {
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
            "backend": self.backend,
            "model": self.model,
            "success": self.success,
            "latency_ms": round(self.latency_ms, 2),
            "token_estimates": {
                "prompt": prompt_chars // 4,
                "response": response_chars // 4,
                "total": (prompt_chars + response_chars) // 4,
            },
            "prompt": {"system": self.system_prompt, "messages": self.messages},
            "response": self.response,
            "error": self.error,
        }


class LLMTracer:
    """
    Wraps any LLM backend with LangChain-style observability.
    Transparent — same interface as BaseLLMBackend.complete().
    Writes a structured trace to disk for every LLM call.
    """

    def __init__(self, backend, trace_path=_DEFAULT_TRACE_PATH, enabled=True):
        self._backend = backend
        self._trace_path = Path(trace_path)
        self._trace_path.parent.mkdir(parents=True, exist_ok=True)
        self._enabled = enabled
        self._session_traces = []

    @property
    def name(self):
        return self._backend.name

    @property
    def session_traces(self):
        return list(self._session_traces)

    def complete(self, system_prompt, messages, max_tokens=1024):
        trace_id = str(uuid.uuid4())
        start = time.perf_counter()
        try:
            response = self._backend.complete(system_prompt, messages, max_tokens)
            latency_ms = (time.perf_counter() - start) * 1000
            trace = LLMTrace(trace_id, self._backend.name, response.model, system_prompt, messages, response.text, latency_ms, True)
            self._record(trace)
            return response
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            trace = LLMTrace(trace_id, self._backend.name, getattr(self._backend, "_model", "unknown"), system_prompt, messages, None, latency_ms, False, str(exc))
            self._record(trace)
            raise

    def _record(self, trace):
        self._session_traces.append(trace)
        if not self._enabled:
            return
        with open(self._trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(trace.to_dict()) + "\n")

    def summary(self):
        if not self._session_traces:
            return {"total": 0}
        total = len(self._session_traces)
        successful = sum(1 for t in self._session_traces if t.success)
        avg_latency = sum(t.latency_ms for t in self._session_traces) / total
        total_tokens = sum(t.to_dict()["token_estimates"]["total"] for t in self._session_traces)
        return {
            "total": total,
            "successful": successful,
            "failed": total - successful,
            "avg_latency_ms": round(avg_latency, 2),
            "estimated_total_tokens": total_tokens,
            "backend": self._backend.name,
        }
