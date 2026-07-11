import pytest
from pydantic import ValidationError

from muni.extract.schema import ExtractedField, Provenance


def test_bare_value_without_provenance_is_rejected():
    with pytest.raises(ValidationError):
        ExtractedField(value="5.00%", provenance=None)


def test_not_disclosed_is_valid():
    field = ExtractedField.not_disclosed()
    assert field.value is None
    assert field.provenance is None
    assert field.confidence == 0.0


def test_value_with_provenance_is_valid():
    field = ExtractedField(
        value="City of Springfield",
        provenance=Provenance(doc_id="abc", page=1, snippet="City of Springfield"),
        confidence=0.8,
    )
    assert field.provenance.page == 1
