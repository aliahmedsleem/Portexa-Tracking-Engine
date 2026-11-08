"""Import operator-verified tracking events without automated website access.

This does not claim live carrier connectivity. Import only data you are
authorized to process and that was obtained through permitted means.
"""
import json
from datetime import datetime
from pathlib import Path
from portexa_tracking.models import TrackingEvent, TrackingResult
from portexa_tracking.normalizer import normalize_key

MAX_IMPORT_BYTES = 256 * 1024
MAX_EVENTS = 500


def _date(value: str) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include timezone")
    return value


def load_verified_file(path: str | Path) -> TrackingResult:
    path = Path(path)
    if path.stat().st_size > MAX_IMPORT_BYTES:
        raise ValueError("Import exceeds 256 KiB limit")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Import must be a JSON object")
    carrier, reference, kind = normalize_key(
        data["carrier"], data["reference"], data["reference_type"])
    if carrier == "FIXTURE":
        raise ValueError("Use fixture connector for synthetic data")
    if data.get("source") != "operator_verified":
        raise ValueError("Source must be operator_verified")
    if data.get("confirmed") is not True:
        raise ValueError("Operator confirmation is required")
    checked_at = _date(data["checked_at"])
    eta = data.get("eta")
    if eta is not None:
        eta = _date(eta)
    raw_events = data.get("events", [])
    if not isinstance(raw_events, list) or len(raw_events) > MAX_EVENTS:
        raise ValueError("Invalid event list")
    events = []
    for item in raw_events:
        if not isinstance(item, dict):
            raise ValueError("Each event must be an object")
        code = item["code"]
        if not isinstance(code, str) or not (1 <= len(code) <= 40):
            raise ValueError("Invalid event code")
        location = item.get("location", "")
        description = item.get("description", "")
        container = item.get("container", "")
        if not all(isinstance(x, str) for x in (location, description, container)):
            raise ValueError("Invalid event text")
        if max(map(len, (location, description, container))) > 500:
            raise ValueError("Event text too long")
        events.append(TrackingEvent(
            code=code, occurred_at=_date(item["occurred_at"]),
            location=location, description=description, container=container))
    if not events and not eta:
        raise ValueError("At least one event or ETA is required")
    return TrackingResult(
        carrier=carrier, reference=reference, reference_type=kind,
        status="found", checked_at=checked_at, source="operator_verified",
        eta=eta, events=tuple(events),
        message="Manually verified by operator; not an automated live feed",
    )
