"""Opt-in local JSON fixtures for deterministic end-to-end tests only."""
import json
from pathlib import Path
from portexa_tracking.models import TrackingEvent, TrackingResult, utc_now


class FixtureAdapter:
    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def track(self, reference: str, reference_type: str) -> TrackingResult:
        if not reference.replace("-", "").isalnum():
            raise ValueError("Invalid reference")
        filename = self.directory / (reference.upper() + ".json")
        if not filename.is_file():
            return TrackingResult(
                carrier="FIXTURE", reference=reference, reference_type=reference_type,
                status="not_found", message="Fixture not found")
        data = json.loads(filename.read_text(encoding="utf-8"))
        if data.get("synthetic") is not True:
            raise ValueError("Fixture MUST be marked synthetic: true")
        if data.get("reference") != reference.upper():
            raise ValueError("Fixture reference mismatch")
        events = tuple(TrackingEvent(**event) for event in data.get("events", []))
        return TrackingResult(
            carrier="FIXTURE", reference=reference, reference_type=reference_type,
            status="found", checked_at=utc_now(), source="synthetic_fixture",
            eta=data.get("eta"), events=events,
            message="SYNTHETIC TEST DATA; never show as real carrier information",
        )
