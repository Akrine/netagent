"""
connectors/ollama_dual.py

Two-model consensus connector for edge AI resilience.

Queries two local Ollama models with the same prompt and compares
responses. If both models agree on the key findings, returns the
better-written answer. If they disagree, flags the uncertainty
explicitly so the FDE knows to investigate further.

This directly addresses the resilience question: is adding two LLM
models better? Answer: yes, but not for availability — for correctness.
A confident wrong answer is worse than no answer. Two models that
disagree is a signal, not a failure.

Architecture:
  snapshot → prompt → model_a  ─┐
                    → model_b  ─┴→ consensus check → answer + confidence
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import requests

_DEFAULT_HOST = "http://localhost:11434"
_MODEL_A = "phi3:mini"
_MODEL_B = "phi3:mini"  # Can be overridden to a different model


@dataclass
class DualModelResponse:
    """
    Result from a two-model consensus query.

    answer:      The final answer to return to the user
    confidence:  "high" | "low" — high means models agreed, low means they diverged
    model_a:     Raw response from model A
    model_b:     Raw response from model B
    agreed:      Whether the models reached consensus
    note:        Human-readable explanation of the confidence level
    """
    answer: str
    confidence: str
    model_a: str
    model_b: str
    agreed: bool
    note: str


class DualModelOllamaConnector:
    """
    Queries two Ollama models and returns a consensus response.

    When models agree: returns the more detailed answer with high confidence.
    When models disagree: returns both perspectives with low confidence,
    flagging that the FDE should investigate further.

    This is the resilience pattern for edge AI — not redundancy against
    failure, but consensus against hallucination.
    """

    def __init__(
        self,
        host: str = _DEFAULT_HOST,
        model_a: str = _MODEL_A,
        model_b: str = _MODEL_B,
        agreement_threshold: float = 0.5,
    ) -> None:
        self._host = host.rstrip("/")
        self._model_a = model_a
        self._model_b = model_b
        self._threshold = agreement_threshold

    def query(self, snapshot, question: str) -> DualModelResponse:
        """
        Query both models and return a consensus response.
        """
        prompt = self._build_prompt(snapshot, question)

        response_a = self._query_model(self._model_a, prompt)
        response_b = self._query_model(self._model_b, prompt)

        return self._build_consensus(response_a, response_b)

    def _query_model(self, model: str, prompt: str) -> str:
        """Query a single Ollama model."""
        try:
            resp = requests.post(
                f"{self._host}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": False,
                },
                timeout=120,
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except requests.RequestException as exc:
            raise RuntimeError(f"Ollama request to model '{model}' failed: {exc}") from exc

    def _build_consensus(
        self, response_a: str, response_b: str
    ) -> DualModelResponse:
        """
        Compare two model responses and determine consensus.

        Consensus is measured by shared key terms — if both models
        identify the same root cause, they agree. If they focus on
        different issues, they disagree.
        """
        score = self._similarity_score(response_a, response_b)
        agreed = score >= self._threshold

        if agreed:
            # Pick the more detailed response
            answer = response_a if len(response_a) >= len(response_b) else response_b
            confidence = "high"
            note = (
                f"Both models identified the same root cause "
                f"(agreement score: {score:.0%}). High confidence."
            )
        else:
            # Return both perspectives with explicit uncertainty
            answer = (
                f"Two diagnostic models gave different assessments. "
                f"Review both and investigate further.\n\n"
                f"Assessment 1 ({self._model_a}):\n{response_a}\n\n"
                f"Assessment 2 ({self._model_b}):\n{response_b}"
            )
            confidence = "low"
            note = (
                f"Models disagreed (agreement score: {score:.0%}). "
                f"Low confidence — treat as a signal to investigate manually."
            )

        return DualModelResponse(
            answer=answer,
            confidence=confidence,
            model_a=response_a,
            model_b=response_b,
            agreed=agreed,
            note=note,
        )

    def _similarity_score(self, a: str, b: str) -> float:
        """
        Compute a simple term-overlap similarity score between two responses.

        Extracts meaningful words (length > 4) and computes Jaccard similarity.
        This is intentionally simple — we're looking for shared diagnostic
        concepts, not semantic similarity.
        """
        def extract_terms(text: str) -> set:
            words = re.findall(r'\b[a-zA-Z]{5,}\b', text.lower())
            stopwords = {
                'which', 'these', 'those', 'their', 'there', 'where',
                'while', 'would', 'could', 'should', 'about', 'after',
                'before', 'other', 'often', 'being', 'having', 'might',
                'since', 'still', 'first', 'second', 'third', 'because',
            }
            return set(w for w in words if w not in stopwords)

        terms_a = extract_terms(a)
        terms_b = extract_terms(b)

        if not terms_a or not terms_b:
            return 0.0

        intersection = terms_a & terms_b
        union = terms_a | terms_b
        return len(intersection) / len(union)

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
