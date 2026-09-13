from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import httpx

from .models import Item, RerankResult


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ModelCandidate:
    id: str
    name: str
    context_length: int
    capability_score: int
    supports_json: bool
    supports_reasoning_effort: bool


def require_free_model(model: str) -> str:
    if not model or not model.endswith(":free"):
        raise ValueError("model must be an explicitly free OpenRouter model ending in :free")
    return model


def rank_free_models(
    models: list[dict[str, Any]], min_context_length: int = 8192, min_capability_score: int = 7
) -> list[ModelCandidate]:
    """Filter and rank the current free model catalog without pinning a model ID."""
    ranked: list[ModelCandidate] = []
    for metadata in models:
        model_id = str(metadata.get("id", ""))
        if not model_id.endswith(":free") or not _is_zero_priced(metadata.get("pricing")):
            continue
        context_length = _as_int(metadata.get("context_length"))
        parameters = {str(value) for value in (metadata.get("supported_parameters") or [])}
        if context_length < min_context_length or not parameters & {
            "reasoning",
            "reasoning_effort",
        }:
            continue
        score = _capability_score(metadata, parameters, context_length)
        if score < min_capability_score:
            continue
        ranked.append(
            ModelCandidate(
                id=model_id,
                name=str(metadata.get("name", model_id)),
                context_length=context_length,
                capability_score=score,
                supports_json=bool(parameters & {"response_format", "structured_outputs"}),
                supports_reasoning_effort="reasoning_effort" in parameters,
            )
        )
    return sorted(
        ranked,
        key=lambda candidate: (
            candidate.capability_score,
            candidate.context_length,
            candidate.id,
        ),
        reverse=True,
    )


def _is_zero_priced(pricing: Any) -> bool:
    if not isinstance(pricing, dict):
        return False
    return all(
        str(pricing.get(key, "")) in {"0", "0.0", "0.00"} for key in ("prompt", "completion")
    )


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _capability_score(metadata: dict[str, Any], parameters: set[str], context_length: int) -> int:
    score = 4 if "reasoning_effort" in parameters else 2
    if "structured_outputs" in parameters:
        score += 3
    elif "response_format" in parameters:
        score += 2
    if context_length >= 131072:
        score += 3
    elif context_length >= 32768:
        score += 2
    elif context_length >= 8192:
        score += 1
    top_provider = metadata.get("top_provider")
    if (
        isinstance(top_provider, dict)
        and _as_int(top_provider.get("max_completion_tokens")) >= 4096
    ):
        score += 1
    return score


class OpenRouter:
    models_url = "https://openrouter.ai/api/v1/models"
    completions_url = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        key: str | None = None,
        model: str | None = None,
        client: httpx.Client | None = None,
        min_context_length: int = 8192,
        min_capability_score: int = 7,
        max_attempts: int = 3,
    ) -> None:
        self.key = key or os.getenv("OPENROUTER_API_KEY", "")
        self.model_override = require_free_model(model) if model else None
        self.client = client or httpx.Client(timeout=30)
        self.min_context_length = min_context_length
        self.min_capability_score = min_capability_score
        self.max_attempts = max_attempts
        self.selected_model: str | None = None

    def rerank(self, items: list[Item]) -> dict[str, RerankResult]:
        if not self.key:
            raise LLMError("OPENROUTER_API_KEY is missing")
        candidates = self._candidates()
        if not candidates:
            raise LLMError("no free OpenRouter model meets the configured capability threshold")
        errors: list[str] = []
        for candidate in candidates[: self.max_attempts]:
            try:
                result = self._request(candidate, items)
                self.selected_model = candidate.id
                return result
            except LLMError as exc:
                errors.append(f"{candidate.id}: {exc}")
        raise LLMError("all selected free OpenRouter models failed: " + "; ".join(errors))

    def _candidates(self) -> list[ModelCandidate]:
        if self.model_override:
            return [ModelCandidate(self.model_override, self.model_override, 0, 0, True, False)]
        response = self.client.get(self.models_url, headers=self._headers())
        if response.status_code >= 400:
            raise LLMError(f"OpenRouter model catalog HTTP {response.status_code}")
        try:
            body = response.json()
            models = body["data"]
            if not isinstance(models, list):
                raise TypeError("model catalog is not a list")
            return rank_free_models(models, self.min_context_length, self.min_capability_score)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMError("OpenRouter model catalog was malformed") from exc

    def _request(self, candidate: ModelCandidate, items: list[Item]) -> dict[str, RerankResult]:
        payload: dict[str, Any] = {
            "model": candidate.id,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": "Return JSON keyed by item id. Each value has decision wow/useful/skip, Korean summary, exactly three tags, and optional skip_reason.",
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        [
                            {
                                "id": i.id,
                                "title": i.title,
                                "description": i.description[:1200],
                                "url": i.canonical_url,
                                "raw_score": i.raw_score,
                            }
                            for i in items
                        ],
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        if candidate.supports_json:
            payload["response_format"] = {"type": "json_object"}
        if candidate.supports_reasoning_effort:
            payload["reasoning_effort"] = "medium"

        response = self.client.post(
            self.completions_url,
            headers=self._headers(),
            json=payload,
        )
        if response.status_code >= 400:
            raise LLMError(f"OpenRouter HTTP {response.status_code}")
        try:
            body: dict[str, Any] = response.json()
            content = body["choices"][0]["message"]["content"]
            values = json.loads(_content_text(content))
            if not isinstance(values, dict):
                raise TypeError("rerank response is not an object")
            expected_ids = {item.id for item in items}
            if set(map(str, values)) != expected_ids:
                raise ValueError("rerank response did not contain exactly the requested item ids")
            return {str(key): self._parse(value) for key, value in values.items()}
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise LLMError("OpenRouter returned malformed JSON") from exc

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}

    @staticmethod
    def _parse(value: Any) -> RerankResult:
        if not isinstance(value, dict):
            raise ValueError("invalid rerank value")
        decision = value.get("decision")
        summary = value.get("summary")
        tags_value = value.get("tags")
        if decision not in {"wow", "useful", "skip"}:
            raise ValueError("invalid rerank decision")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("rerank summary is missing")
        if not isinstance(tags_value, list) or len(tags_value) != 3:
            raise ValueError("rerank response must contain exactly three tags")
        tags = [str(v).strip() for v in tags_value]
        if any(not tag for tag in tags):
            raise ValueError("rerank tag is empty")
        skip_reason = value.get("skip_reason")
        if decision == "skip" and (not isinstance(skip_reason, str) or not skip_reason.strip()):
            raise ValueError("skip decision requires a reason code")
        return RerankResult(
            decision,
            " ".join(summary.split())[:280],
            tags,
            skip_reason,
        )


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        text = "".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        ).strip()
    else:
        raise TypeError("message content is not text")
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0].strip()
    return text


def apply(items: list[Item], results: dict[str, RerankResult]) -> list[Item]:
    for item in items:
        result = results.get(item.id)
        if result:
            item.llm_decision, item.llm_summary, item.llm_tags = (
                result.decision,
                result.summary,
                result.tags,
            )
            item.final_score = (
                item.raw_score + {"wow": 0.08, "useful": 0.02, "skip": -0.05}[result.decision]
            )
    return sorted(items, key=lambda i: i.final_score, reverse=True)
