"""Validation and status-independent deduplication."""
import re
from portexa_tracking.carriers.registry import normalize_carrier


REFERENCE_TYPES = {"BL", "BOOKING", "CONTAINER"}


def normalize_reference(value: str, reference_type: str) -> str:
    kind = reference_type.strip().upper()
    if kind not in REFERENCE_TYPES:
        raise ValueError("Reference type must be BL, BOOKING or CONTAINER")
    reference = re.sub(r"\s+", "", (value or "").upper())
    if not re.fullmatch(r"[A-Z0-9-]{4,40}", reference):
        raise ValueError("Invalid shipment reference")
    return reference


def normalize_key(carrier: str, reference: str, reference_type: str):
    carrier = normalize_carrier(carrier)
    kind = reference_type.strip().upper()
    return carrier, normalize_reference(reference, kind), kind
