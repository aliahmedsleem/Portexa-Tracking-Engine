import json
import pytest
from portexa_tracking.manual_import import load_verified_file


def example():
    return {
        "carrier": "CMA_CGM", "reference": "DEMO987654",
        "reference_type": "BL", "source": "operator_verified",
        "confirmed": True, "checked_at": "2030-03-12T10:00:00+03:00",
        "events": [{"code": "LOAD", "occurred_at": "2030-03-11T08:00:00+03:00",
                    "location": "EXAMPLE PORT", "description": "Example only"}],
    }


def test_verified_import(tmp_path):
    p = tmp_path / "import.json"
    p.write_text(json.dumps(example()), encoding="utf-8")
    result = load_verified_file(p)
    assert result.status == "found"
    assert result.source == "operator_verified"
    assert len(result.events) == 1


def test_confirmation_required(tmp_path):
    d = example()
    d["confirmed"] = False
    p = tmp_path / "import.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ValueError, match="confirmation"):
        load_verified_file(p)


def test_no_fake_fixture_as_verified(tmp_path):
    d = example()
    d["carrier"] = "FIXTURE"
    p = tmp_path / "import.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(ValueError, match="fixture"):
        load_verified_file(p)
