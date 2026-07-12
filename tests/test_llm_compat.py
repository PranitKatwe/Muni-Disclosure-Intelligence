import pytest
from pydantic import BaseModel

from muni.extract.llm import extract_json_object, make_extractor


class Tiny(BaseModel):
    name: str


def test_extract_json_object_plain():
    assert extract_json_object('{"name": "x"}') == '{"name": "x"}'


def test_extract_json_object_strips_fences_and_prose():
    text = 'Here is the result:\n```json\n{"name": "x"}\n```\nDone.'
    assert Tiny.model_validate_json(extract_json_object(text)).name == "x"


def test_extract_json_object_no_json_raises():
    with pytest.raises(ValueError):
        extract_json_object("I could not find anything.")


class _Settings:
    llm_provider = "nope"
    extraction_model = "claude-opus-4-8"
    nvidia_model = "meta/llama-3.3-70b-instruct"


def test_make_extractor_rejects_unknown_provider():
    with pytest.raises(ValueError):
        make_extractor(_Settings())


def test_nvidia_extractor_requires_key(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="build.nvidia.com"):
        make_extractor(_Settings(), provider="nvidia")


def test_issue_raw_coerces_covenant_object_to_list():
    from muni.extract.llm import IssueRaw

    single = IssueRaw.model_validate(
        {"key_covenants": {"value": "rate covenant", "page": 5, "snippet": "rate covenant"}}
    )
    assert len(single.key_covenants) == 1
    assert single.key_covenants[0].value == "rate covenant"

    empty = IssueRaw.model_validate({"key_covenants": {"value": None, "page": None}})
    assert empty.key_covenants is None


def test_issue_raw_accepts_literal_null_fields():
    from muni.extract.llm import IssueRaw

    raw = IssueRaw.model_validate(
        {"issuer_name": None, "key_covenants": None,
         "pledge_type": {"value": "GO", "page": 3, "snippet": "general obligation"}}
    )
    assert raw.issuer_name is None
    assert raw.key_covenants is None
    assert raw.pledge_type.value == "GO"
