import pytest
from portexa_tracking.browser import BrowserPolicy


def test_explicit_host_allowlist():
    p = BrowserPolicy(frozenset({"tracking.example.org"}))
    p.validate("https://tracking.example.org/track")
    with pytest.raises(PermissionError):
        p.validate("https://other.example.org/")


def test_reject_unsafe_urls():
    p = BrowserPolicy(frozenset({"tracking.example.org"}))
    for url in ["http://tracking.example.org/", "https://user:pass@tracking.example.org/",
                "https://tracking.example.org:8443/", "file:///etc/passwd"]:
        with pytest.raises(ValueError):
            p.validate(url)


def test_no_hosts_by_default(monkeypatch):
    monkeypatch.delenv("PORTEXA_BROWSER_ALLOWED_HOSTS", raising=False)
    p = BrowserPolicy.from_environment()
    with pytest.raises(PermissionError):
        p.validate("https://tracking.example.org/")
