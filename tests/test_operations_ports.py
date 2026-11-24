"""Port metadata compatibility tests; never touch production."""
import pytest
from portexa_tracking.operations_ports import split_details, combine_details


def test_legacy_details_preserved_without_ports():
    original = "Existing shipping notes\nSecond line"
    assert split_details(original)["details"] == original
    assert combine_details(original,"","") == original


def test_roundtrip_with_ports():
    original = "Cargo shipped via carrier portal"
    encoded = combine_details(original,"Umm Qasr","Jebel Ali")
    parsed = split_details(encoded)
    assert parsed == {"loading_port":"Umm Qasr","destination_port":"Jebel Ali","details":original}
    assert combine_details(parsed["details"],parsed["loading_port"],parsed["destination_port"]) == encoded


def test_empty_and_arabic_ports():
    encoded = combine_details("","ميناء أم قصر","ميناء جبل علي")
    assert split_details(encoded)["loading_port"] == "ميناء أم قصر"
    assert split_details(encoded)["destination_port"] == "ميناء جبل علي"


def test_reject_invalid_or_oversized():
    with pytest.raises(ValueError):
        combine_details("notes","bad\nport","destination")
    with pytest.raises(ValueError):
        combine_details("x"*2000,"A","B")
    with pytest.raises(ValueError):
        split_details("[PORTEXA_PORTS_V1]\ninvalid\n[/PORTEXA_PORTS_V1]")
