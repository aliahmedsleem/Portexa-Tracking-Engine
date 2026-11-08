# Tracking service design (v0.1)

## Data boundaries
- One `shipments` row per (carrier, normalized reference, reference type).
- `tracking_events` keyed by a SHA-256 identity of normalized event fields.
- `latest_status` is the **latest lookup status**, not the vessel's movement state.
- `eta` and `source` are retained on a temporary lookup failure. The lookup failure is shown separately to avoid suggesting stale data is current.
- No private customer data or credentials in this repository.

## States
`pending` (no check), `found`, `not_found`, `unavailable`, `temporary_error`.

## Scheduling
Atomic SQLite leases prevent two workers from claiming the same due job simultaneously. Each job is a bounded single lookup. Temporary error, missing data or unavailable access delays future checks. Production deployment should run a single worker under systemd timer initially and bound provider timeouts.

## Provider work
Each carrier adapter must use a permitted integration and map source-specific milestones to `TrackingEvent`. Do not infer real location from routing schedules or AIS without explicit source labeling and customer consent.

## Integration roadmap
1. Verify allowed data access for CMA CGM and test `NAM8787725` under authorization.
2. Record raw-to-normalized event fixture from a lawful response (redact sensitive details).
3. Add provider rate-limit handling, observability, owner authorization and API auth.
4. Connect PORTEXA's shipment registry and notifications through an authenticated bridge.
5. Add the other providers incrementally after validation.
