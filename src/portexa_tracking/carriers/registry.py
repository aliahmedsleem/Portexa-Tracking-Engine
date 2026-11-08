"""Carrier ID normalization and adapter selection."""
import os
from .base import PendingAuthorizationAdapter
from .fixture import FixtureAdapter
from .browser_demo import BrowserDemoAdapter

CARRIERS = ("CMA_CGM", "MSC", "MAERSK", "HAPAG_LLOYD", "COSCO", "FIXTURE", "BROWSER_DEMO")
ALIASES = {
    "CMA CGM": "CMA_CGM", "CMACGM": "CMA_CGM",
    "HAPAG-LLOYD": "HAPAG_LLOYD", "HAPAG LLOYD": "HAPAG_LLOYD",
}


def normalize_carrier(value: str) -> str:
    value = (value or "").strip().upper()
    value = ALIASES.get(value, value)
    if value not in CARRIERS:
        raise ValueError("Unsupported carrier: " + value)
    return value


def get_adapter(carrier: str):
    carrier = normalize_carrier(carrier)
    if carrier == "BROWSER_DEMO":
        return BrowserDemoAdapter()
    if carrier == "FIXTURE":
        directory = os.environ.get("PORTEXA_FIXTURE_DIR")
        if not directory:
            return PendingAuthorizationAdapter(carrier)
        return FixtureAdapter(directory)
    return PendingAuthorizationAdapter(carrier)
