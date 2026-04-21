"""
connectors/ollama.py

Local Ollama LLM connector.
Sends a DiagnosticSnapshot to a locally running Ollama model
and returns a natural language response.
"""

from __future__ import annotations

import requests

_DEFAULT_HOST = "http://localhost:11434"
_DEFAULT_MODEL = "phi3:mini"


class OllamaConnector:
    """
    Sends diagnostic context to a local Ollama model and returns
    a natural language response. This is the AI layer that sits
    on top of any data connector.
    """

    def __init__(
        self,
        host: str = _DEFAULT_HOST,
        model: str = _DEFAULT_MODEL,
    ) -> None:
        self._host = host.rstrip("/")
        self._model = model

    def query(self, snapshot, question: str) -> str:
        prompt = self._build_prompt(snapshot, question)
        try:
            resp = requests.post(
                f"{self._host}/api/generate",
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except requests.RequestException as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc

    def _build_prompt(self, snapshot, question: str) -> str:
        findings_text = ""
        for f in snapshot.findings:
            findings_text += (
                f"- [{f.severity.value.upper()}] {f.title}: {f.description}"
            )
            if f.technical_detail:
                findings_text += f" (Detail: {f.technical_detail})"
            findings_text += "\n"

        if not findings_text:
            findings_text = "No issues detected.\n"

        return f"""You are a network diagnostic assistant. Analyze the following network health data and answer the user's question in plain English with specific, actionable advice.

Network Health Data:
- Source: {snapshot.source_connector}
- Overall Severity: {snapshot.overall_severity.value}
- Findings:
{findings_text}
User Question: {question}

Answer:"""
