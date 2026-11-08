"""Optional, explicitly authorized browser automation foundation.

No CAPTCHA bypass, stealth fingerprints, login circumvention, or proxy rotation.
Do not enable against a carrier unless the operator has permission.
"""
import os
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class BrowserPolicy:
    allowed_hosts: frozenset[str]
    headless: bool = True
    timeout_ms: int = 30000

    @classmethod
    def from_environment(cls):
        hosts = frozenset(
            host.strip().lower()
            for host in os.getenv("PORTEXA_BROWSER_ALLOWED_HOSTS", "").split(",")
            if host.strip()
        )
        return cls(allowed_hosts=hosts)

    def validate(self, url: str):
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Browser target must be HTTPS")
        if parsed.username or parsed.password or parsed.port:
            raise ValueError("Browser target may not contain credentials or a custom port")
        if parsed.hostname.lower() not in self.allowed_hosts:
            raise PermissionError("Host not allowlisted for authorized browser access")


def capture_authorized_page(url: str, *, policy: BrowserPolicy | None = None) -> str:
    """Capture DOM for an authorized target, without bypassing access controls.

    Returns HTML only; provider-specific extraction belongs in an adapter.
    """
    policy = policy or BrowserPolicy.from_environment()
    policy.validate(url)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Install optional browser dependencies: pip install -e '.[browser]'") from exc
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=policy.headless)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=policy.timeout_ms)
            if page.url != url:
                policy.validate(page.url)
            return page.content()
        finally:
            browser.close()
