"""Bounded, low-frequency update jobs."""
from datetime import datetime, timedelta, timezone
from portexa_tracking.carriers.registry import get_adapter, normalize_carrier
from portexa_tracking.database import claim_due, store_result
from portexa_tracking.models import TrackingResult


def next_delay_hours(status: str, failures: int, events=()):
    if status in ("unavailable", "temporary_error", "not_found"):
        return min(24 * 3, 6 * (2 ** min(failures, 4)))
    if any(e.code.upper() in {"ARRIVAL", "VESSEL_ARRIVAL", "DISCHARGE"} for e in events):
        return 12
    return 8


def run_once(conn, carrier=None):
    carrier = normalize_carrier(carrier) if carrier else None
    job = claim_due(conn, carrier=carrier)
    if job is None:
        return None
    try:
        result = get_adapter(job["carrier"]).track(job["reference"], job["reference_type"])
        assert result.carrier == job["carrier"]
        assert result.reference == job["reference"]
        failures = 0 if result.status == "found" else job["failures"] + 1
        delay = next_delay_hours(result.status, failures, result.events)
    except Exception as exc:
        # No secrets or raw provider responses in logs.
        result = TrackingResult(
            carrier=job["carrier"], reference=job["reference"],
            reference_type=job["reference_type"], status="temporary_error",
            message=f"{type(exc).__name__}: carrier lookup failed")
        failures = job["failures"] + 1
        delay = next_delay_hours(result.status, failures)
    next_check = (datetime.now(timezone.utc) + timedelta(hours=delay)).isoformat(timespec="seconds")
    store_result(conn, job["id"], result, next_check, failures)
    return {
        "shipment_id": job["id"], "carrier": job["carrier"],
        "reference": job["reference"], "status": result.status,
        "checked_at": result.checked_at, "next_check_at": next_check,
        "events_received": len(result.events), "source": result.source,
        "message": result.message,
    }
