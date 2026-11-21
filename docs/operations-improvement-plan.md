# PORTEXA Operations — UX and Bill of Lading Workflow

## Goal
Improve the existing authenticated staff operations module in `src/portexa_tracking/operations_web.py`, making it a polished, responsive Arabic RTL application, improving visibility into shipment/B/L data and clarity of fair allocation. Preserve the established payroll, roles, source database boundaries, task lifecycle, and existing URL contracts.

## Verified baseline
- `operations_web.py` includes the whole HTML/CSS layout and current Flask routes.
- `operations_distribution.py` implements the eligible-source allowlist and fair assignment for new shipments. Existing ownership is preserved.
- `portexa_bridge.py` reads shipment data from PORTEXA's production SQLite database, with write access explicitly gated by `PORTEXA_SOURCE_WRITE_ENABLED=YES`.
- `operations_scheduler.py` previews six-hourly payable task creation; `--apply` changes data.
- `OPERATIONS.md` contains important deployment and status-validation warnings.
- Tests already cover auth, distribution, bridge, and operations behavior.

## Implementation task for Codex
1. Refactor UI into maintainable Flask/Jinja templates and static CSS/JS, retaining the public URL and endpoint behavior and all role guards, CSRF verification, and login semantics. No React rewrite required. Ensure every HTML page uses Arabic RTL, clear hierarchy and meaningful spacing, modern accessible typography, proper focus indicators, mobile-friendly forms and tables, consistent status tags, and restrained navy/gold brand accents.
2. Design separate pages or well-defined navigation areas for overview, shipment/B/L list, distribution preview, pending supervisor approvals, operator task queue, notifications, employees, and payroll. Respect roles; do not show admin-only controls to operators. Ensure navigation is persistent and functional on mobile; avoid cramming all forms into one dashboard.
3. For shipment/B/L browsing, present real source columns (e.g. `bol_no`, `invoice_no`, `msc_tracking`, dates and status) with search/filter/pagination, accurate empty/error states, and a shipment detail/review history view. Prefer server-side queries, limit result sizes, never invent a B/L reference and distinguish internal shipment ID from B/L number. Keep production source queries read-only for browsing.
4. Improve distribution UX: show eligible/already-assigned/new counts, workload distribution, new assignment plan, status-allowlist guidance and explicit confirmation. Keep `source_eligible_ids` fail-closed; do not reassign existing shipments or auto-create payable tasks in a preview.
5. Improve operator workflow: see assigned tasks with status and context, submit evidence/source and clear change/no-change assessment, prevent misleading status or simulated data. Keep supervisor explicit approval and payroll invariants.
6. Analyze how source B/L references are stored versus actual B/L PDF/image documents. Do not claim OCR exists. Add a documented, testable pipeline only if an authorized document input exists. For any ingestion feature, use safe file type/size checks, private storage, provenance, extracted field validation, duplicate checks and mandatory human review before production writes. Start with a read-only extraction preview; never automatically overwrite source shipment records. If documents are unavailable, implement useful source-record B/L browsing and propose the separate document-import stage.
7. Add regression tests for endpoints, role access, CSRF, source read-only behavior, pagination/filters, valid/invalid B/L data and distribution preview/confirmation. Run all tests in a clean environment and report exact results.
8. Update `OPERATIONS.md` and README with environment variables, local development commands, migrations if any, screenshots or clear review instructions, and safe deployment/rollback procedure.

## Safety requirements
- Development branch only: `feature/operations-ux-bol-workflow`; no direct changes to `main`, automatic merge or production deployment.
- Preserve local-only binding/security unless a separately reviewed secure deployment is implemented.
- No unauthorized scraping or calls to carrier websites.
- No live shipping positions inferred from manual data; label verified/source/operator/manual data clearly.
- Do not modify production PORTEXA database during tests, use temporary SQLite fixtures.
- Never alter approved earnings, prior tasks, or existing shipment assignments.
- Never turn on recurring assignment, scheduler, payroll creation, source write, or disbursement as part of a UI change.
- Prefer small reviewable commits and a draft pull request with test evidence.

## Acceptance criteria
- All previously working flows preserved.
- Arabic RTL layouts function at 360px, tablet and desktop widths, no unexpected horizontal page scroll.
- Admin/supervisor/operator views expose only authorized features.
- A shipment can be found by its actual B/L number and opened for context without source mutation.
- Distribution can be previewed with no write and explicitly confirmed, preserving assigned ownership.
- Existing automated and new regression tests pass.
- Document reading capability is described accurately, distinguishing source DB fields from uploaded original documents.
