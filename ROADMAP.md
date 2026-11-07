# Roadmap

This roadmap lists useful extensions beyond the working 0.1 CLI baseline. It is a proposal for development, not a claim of existing users, paid work, or an agreed delivery contract. The maintainer should prioritise it after trying the tool on a personal, non-sensitive inventory.

The estimates are planning ranges for implementation, review, focused tests and documentation. They are not recorded hours or guaranteed billable hours. There are **31-44 hours** of proposed remaining work; none of the items below is counted as already implemented.

Integration effort is included once: B03 includes adapting B01/B02 to bins, and B06 adds recovery tooling around B03's migration rather than charging for a second implementation of that migration.

| ID | Extension | Estimated hours | Depends on |
| --- | --- | ---: | --- |
| B01 | Previewed CSV import | 5-7 | Baseline |
| B02 | Stocktake and reconciliation sessions | 6-8 | Baseline |
| B03 | Multiple storage bins and transfers | 6-9 | Baseline, then adapt B01/B02 |
| B04 | Reusable kit templates and revision history | 5-7 | Baseline |
| B05 | Interactive terminal inventory browser | 5-7 | Baseline; reuse B01 preview |
| B06 | Backup, restore and schema upgrade tools | 4-6 | Baseline; release after B03 |
| | **Total** | **31-44** | |

## B01: Previewed CSV import

**Problem:** Entering an existing drawer list one item at a time is tedious, and blind imports risk overwriting quantities.

Support a documented CSV format with a preview of proposed item creations and stock adjustments. Show row-specific errors and duplicate SKUs before applying anything. Explicitly distinguish an absolute counted quantity from an adjustment. The preview is bound to the input bytes and relevant database state; reject application if either changed. Repeating an applied import must not add the same stock twice.

Acceptance: malformed rows never cause partial writes; UTF-8/BOM, duplicate rows, changed previews and reserved stock are covered by tests; idempotency behaviour and a synthetic example are documented. No supplier, customer, price or identity fields.

## B02: Stocktake and reconciliation

**Problem:** Counting a drawer takes time; stock can change between the first and last count.

Create a named stocktake session, record item counts and preview the differences. Detect changes since a count was recorded. Apply confirmed differences as labelled ledger movements in one transaction. Refuse adjustments below existing reservations and retain completed/cancelled session records.

Acceptance: stale counts require explicit resolution; cancellation changes no stock; an interrupted session can be resumed; applying twice cannot duplicate adjustments. Tests cover stock changes and reservations made during a session.

## B03: Multiple bins and transfers

**Problem:** A single storage label cannot represent parts split across drawers.

Introduce per-bin balances through a versioned migration. Preserve the existing item totals, movement history and reservations. Add transfers with paired movements and deterministic allocation rules. Keep aggregate and bin views consistent, then update import and stocktake workflows to target bins.

Acceptance: transfers preserve total stock; allocation cannot overdraw a bin; an interrupted migration leaves a usable old or new database; existing v1 fixtures upgrade correctly. Reserve/consume tests cover competing kits across bins.

## B04: Reusable kit templates

**Problem:** The baseline keeps a consumed kit as a permanent record; repeated builds require manual recreation.

Separate a reusable recipe from a kit instance. Permit immutable template revisions, cloning and batch quantities. A kit captures the selected revision so editing a template cannot change a reservation already in progress. Add explicit draft-line removal and quantity editing.

Acceptance: historical kits retain their exact requirements; changing templates cannot mutate reserved stock; quantity multiplication is checked for overflow; retries do not accidentally create duplicate instances.

## B05: Interactive terminal browser

**Problem:** The CLI is easy to script but less convenient when finding and counting parts at a workbench.

Add searchable, keyboard-accessible views for items, low stock and kits, reusing the core instead of duplicating its rules. Provide previews and explicit confirmation for consumption and stock corrections. Keep the current non-interactive CLI and JSON interface stable. Decide on an optional terminal dependency only after comparing maintainability and Windows support.

Acceptance: all views have an escape/cancel route; errors preserve entered values; Windows and Linux manual checks are recorded; non-interactive tests still pass; no personal accounts or remote service is introduced.

## B06: Verified backup, restore and upgrades

**Problem:** A snapshot export is not a tested recovery workflow.

Use SQLite's backup API for a consistent database backup. Validate format, schema version and integrity before restoring to a new path. Add upgrade preflight, a recovery copy and failure handling. Rehearse the multi-bin migration with v1 databases. Do not use raw file copying while a database is open as the advertised backup method.

Acceptance: backup during writes produces a valid, consistent snapshot; corruption and unsupported versions fail before replacing any working file; successful restore reproduces items, kits and movements; documented recovery commands work on a clean checkout.

## Boundaries

No payments, purchase prices, people/contact records, medical or biological workflows, security scanning, AI model work or scientific computation are planned. Basic quantities and integrity checks are ordinary inventory logic. Do not create features merely to fill an hour target, and do not leave known defects in the baseline to manufacture future work.
