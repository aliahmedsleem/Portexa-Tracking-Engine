"""Provider interface and safe default adapter."""
from typing import Protocol

from portexa_tracking.models import TrackingResult


class CarrierAdapter(Protocol):
    def track(self, reference: str, reference_type: str) -> TrackingResult:
        ...


class PendingAuthorizationAdapter:
    def __init__(self, carrier: str):
        self.carrier = carrier

    def track(self, reference: str, reference_type: str) -> TrackingResult:
        return TrackingResult(
            carrier=self.carrier,
            reference=reference,
            reference_type=reference_type,
            status="unavailable",
            message="No authorized live carrier connector has been configured.",
        )
