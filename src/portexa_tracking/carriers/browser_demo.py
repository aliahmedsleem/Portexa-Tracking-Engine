"""Deterministic browser-based carrier simulation. NO external network calls."""
from html import escape
from playwright.sync_api import sync_playwright
from portexa_tracking.models import TrackingEvent, TrackingResult, utc_now

DEMO_REFERENCE = "BROWSER123"
DEMO_ETA = "2030-06-21T18:00:00+03:00"


def simulated_tracking_html(reference: str) -> str:
    """Owned HTML fixture mimics a shipment tracking page."""
    ref = escape(reference)
    return f"""<!doctype html><html><body>
      <h1>Local synthetic tracking simulator</h1>
      <input id="reference" value="{ref}">
      <button id="search" onclick="document.getElementById('results').hidden=false">Track</button>
      <section id="results" hidden data-eta="{DEMO_ETA}">
        <div class="event" data-code="GATE_IN" data-time="2030-06-01T09:00:00+03:00"
             data-location="DEMO ORIGIN PORT">Container received (synthetic)</div>
        <div class="event" data-code="VESSEL_DEPARTURE" data-time="2030-06-05T13:00:00+03:00"
             data-location="DEMO ORIGIN PORT">Vessel departed (synthetic)</div>
        <div class="event" data-code="PLANNED_VESSEL_ARRIVAL" data-time="{DEMO_ETA}"
             data-location="DEMO DESTINATION PORT">Estimated arrival (synthetic)</div>
      </section></body></html>"""


class BrowserDemoAdapter:
    """Exercises Chromium DOM interactions and real scheduler/database pipeline."""

    def track(self, reference: str, reference_type: str) -> TrackingResult:
        if reference != DEMO_REFERENCE:
            return TrackingResult(carrier="BROWSER_DEMO", reference=reference,
                                  reference_type=reference_type, status="not_found",
                                  source="synthetic_browser_demo",
                                  message="Only BROWSER123 is supported by this simulator")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content(simulated_tracking_html(reference))
                page.locator("#search").click()
                page.locator("#results").wait_for(state="visible")
                eta = page.locator("#results").get_attribute("data-eta")
                events = tuple(
                    TrackingEvent(
                        code=el.get_attribute("data-code"),
                        occurred_at=el.get_attribute("data-time"),
                        location=el.get_attribute("data-location") or "",
                        description=el.inner_text(),
                    ) for el in page.locator("#results .event").all()
                )
            finally:
                browser.close()
        return TrackingResult(
            carrier="BROWSER_DEMO", reference=reference,
            reference_type=reference_type, status="found",
            checked_at=utc_now(), source="synthetic_browser_demo",
            eta=eta, events=events,
            message="SYNTHETIC browser DOM test; not real carrier data",
        )
