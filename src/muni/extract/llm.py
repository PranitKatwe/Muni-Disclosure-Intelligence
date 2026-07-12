"""LLM wrappers for extraction: Anthropic (structured outputs) and any
OpenAI-compatible endpoint such as NVIDIA Build's free hosted models.

Raw LLM output is UNTRUSTED regardless of provider: every candidate field must
pass snippet verification (provenance.py) before it becomes an ExtractedField.
"""

from __future__ import annotations

import json
import os
from typing import Protocol

import time

import anthropic
from pydantic import BaseModel, ValidationError, field_validator


class RawField(BaseModel):
    value: str | None = None
    page: int | None = None  # the [PAGE N] label the snippet came from
    snippet: str | None = None  # verbatim quote from that page, <= 300 chars
    self_confidence: float | None = None  # logged only; never trusted


# Every field is optional: models legitimately return null for "not found",
# and open models return a literal null rather than a null-membered object.
class IssueRaw(BaseModel):
    issuer_name: RawField | None = None
    issue_purpose: RawField | None = None
    pledge_type: RawField | None = None
    revenue_source: RawField | None = None
    debt_service_reserve: RawField | None = None
    fiscal_year_end: RawField | None = None
    annual_filing_deadline: RawField | None = None
    key_covenants: list[RawField] | None = None

    @field_validator("key_covenants", mode="before")
    @classmethod
    def _coerce_covenants(cls, v):
        # Open models sometimes return a single object (or an empty RawField)
        # where the schema wants an array. Coerce instead of failing the run.
        if isinstance(v, dict):
            value = v.get("value")
            if value in (None, "", []):
                return None
            return [v]
        return v


class MaturityRaw(BaseModel):
    cusip: RawField | None = None
    coupon: RawField | None = None
    maturity_date: RawField | None = None
    call_features: RawField | None = None
    tax_status: RawField | None = None


class Extractor(Protocol):
    model: str

    def extract(self, output_model: type[BaseModel], system: str, user_prompt: str) -> BaseModel: ...


class ClaudeExtractor:
    def __init__(self, model: str = "claude-opus-4-8"):
        self.model = model
        self.client = anthropic.Anthropic()

    def extract(self, output_model: type[BaseModel], system: str, user_prompt: str) -> BaseModel:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
            output_format=output_model,
        )
        if response.stop_reason == "refusal":
            raise RuntimeError("model declined the extraction request (stop_reason=refusal)")
        if response.parsed_output is None:
            raise RuntimeError(
                f"structured output parse failed (stop_reason={response.stop_reason})"
            )
        return response.parsed_output


def extract_json_object(text: str) -> str:
    """Pull the JSON object out of a completion that may add prose or code fences."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    return text[start : end + 1]


class OpenAICompatExtractor:
    """Any OpenAI-compatible endpoint; defaults to NVIDIA Build's free hosted models.

    These endpoints lack Anthropic-style structured outputs, so the JSON schema
    is enforced by prompt + pydantic validation, with one repair retry.
    """

    NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

    def __init__(
        self,
        model: str,
        base_url: str = NVIDIA_BASE_URL,
        api_key: str | None = None,
        api_key_env: str = "NVIDIA_API_KEY",
    ):
        from openai import OpenAI

        key = api_key or os.environ.get(api_key_env)
        if not key:
            raise RuntimeError(
                f"{api_key_env} is not set. Sign up at build.nvidia.com, generate a free "
                f"API key, and add {api_key_env}=nvapi-... to .env"
            )
        self.model = model
        self.client = OpenAI(base_url=base_url, api_key=key)

    def extract(self, output_model: type[BaseModel], system: str, user_prompt: str) -> BaseModel:
        schema = json.dumps(output_model.model_json_schema())
        messages = [
            {
                "role": "system",
                "content": system
                + "\n\nReturn ONLY a single JSON object that validates against this JSON "
                "schema - no prose, no markdown code fences:\n" + schema,
            },
            {"role": "user", "content": user_prompt},
        ]
        last_error: Exception | None = None
        for _ in range(2):  # one attempt + one repair retry
            text = self._create_with_backoff(messages)
            try:
                return output_model.model_validate_json(extract_json_object(text))
            except (ValidationError, ValueError) as err:
                last_error = err
                messages.append({"role": "assistant", "content": text})
                messages.append({
                    "role": "user",
                    "content": f"That JSON failed validation: {err}\n"
                    "Return only the corrected JSON object.",
                })
        raise RuntimeError(f"model output failed schema validation twice: {last_error}")

    def _create_with_backoff(self, messages, attempts: int = 4) -> str:
        """Free-tier endpoints throw transient 429/5xx under load; wait and retry."""
        import openai

        for attempt in range(attempts):
            try:
                response = self.client.chat.completions.create(
                    model=self.model, messages=messages, temperature=0.0, max_tokens=4096
                )
                return response.choices[0].message.content or ""
            except (openai.APIStatusError, openai.APIConnectionError) as err:
                status = getattr(err, "status_code", None)
                retryable = status is None or status == 429 or status >= 500
                if not retryable or attempt == attempts - 1:
                    raise
                time.sleep(30 * (attempt + 1))
        raise RuntimeError("unreachable")


def make_extractor(settings, provider: str | None = None, model: str | None = None) -> Extractor:
    provider = (provider or settings.llm_provider).lower()
    if provider == "anthropic":
        return ClaudeExtractor(model=model or settings.extraction_model)
    if provider == "nvidia":
        return OpenAICompatExtractor(model=model or settings.nvidia_model)
    raise ValueError(f"unknown LLM provider: {provider!r} (expected 'anthropic' or 'nvidia')")
