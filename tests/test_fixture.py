import json
from pathlib import Path
from portexa_tracking.carriers.fixture import FixtureAdapter


def test_fixture_reads_synthetic_data():
    directory = Path(__file__).parent / "fixtures"
    result = FixtureAdapter(directory).track("DEMO12345", "BL")
    assert result.status == "found"
    assert result.source == "synthetic_fixture"
    assert len(result.events) == 2
    assert result.eta


def test_fixture_cannot_be_mislabeled_real(tmp_path):
    (tmp_path / "EXAMPLE123.json").write_text(json.dumps({
        "reference": "EXAMPLE123", "events": []
    }), encoding="utf-8")
    import pytest
    with pytest.raises(ValueError, match="synthetic"):
        FixtureAdapter(tmp_path).track("EXAMPLE123", "BL")
