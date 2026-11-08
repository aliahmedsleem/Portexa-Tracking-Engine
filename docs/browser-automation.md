# Authorized browser connector foundation

The engine can optionally use Playwright to read DOM content from an HTTPS
site **only when the operator has permission** and has explicitly allowlisted
the host. This is not a CMA CGM live connector and is not wired into scheduled
carrier jobs yet.

Install on Ubuntu in the engine's isolated virtual environment:

```bash
pip install -e '.[browser,dev]'
python -m playwright install chromium
pytest -q
```

Before using any site, verify permission and configure an explicit host:

```bash
export PORTEXA_BROWSER_ALLOWED_HOSTS=tracking.example.org
```

The module does not bypass CAPTCHA, bot protections, access controls or
authentication. It does not submit tracking references automatically. Each
carrier needs a separate authorized adapter, response parser, rate limit and
reliability tests. The public PORTEXA production deployment is unaffected.
