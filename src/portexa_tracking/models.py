"""Transport-neutral shipment event model."""
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class TrackingEvent:
    code: str
    occurred_at: str
    location: str = ""
    description: str = ""
    container: str = ""

    def identity(self) -> tuple[str, str, str, str, str]:
        return (self.code.upper(), self.occurred_at, self.location.casefold(),
                self.container.upper(), self.description.casefold())


@dataclass(frozen=True)
class TrackingResult:
    carrier: str
    reference: str
    reference_type: str
    status: str  # found | not_found | unavailable | temporary_error
    checked_at: str = field(default_factory=utc_now)
    source: str = ""
    eta: str | None = None
    events: tuple[TrackingEvent, ...] = ()
    message: str = ""

    def __post_init__(self):
        if self.status not in {"found", "not_found", "unavailable", "temporary_error"}:
            raise ValueError("Invalid tracking status")
        if self.status != "found" and (self.eta or self.events):
            raise ValueError("Do not attach unverified events to a failed lookup")
