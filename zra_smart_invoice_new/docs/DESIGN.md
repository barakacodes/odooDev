# Design Decisions

Rationale for the non-obvious choices in this module. Each entry exists
because of a spec requirement, a live-sandbox finding, or a compliance rule.

## D1 — VSDC-first, never direct-to-ZRA
All fiscal calls go to the local VSDC. Authentication is `tpin` + `bhfId` +
`dvcSrl` in the payload — there is deliberately **no Authorization header**;
the VSDC owns ZRA-side security. Keep this in mind when filling the ZRA
self-check list ("Authorization header" row = N/A by architecture).

## D2 — One gateway method
`_make_request()` is the only place that speaks HTTP. Every endpoint method
builds a plain dict and delegates. This gives uniform logging, timeout and
error behaviour, and makes the endpoint surface auditable with a single grep.

## D3 — Independent-cursor audit logging
`_log_api_call()` opens a fresh cursor. Rationale: fiscal audit records must
survive the rollback of the business transaction that triggered them (e.g. a
failed invoice post). Without this, the exact evidence of a failed ZRA call
would vanish together with the invoice error.

## D4 — Tax buckets mirror the spec exactly
ZRA's Save Sales schema has one header bucket per tax code
(`taxblAmtA/taxAmtA/taxRtA` … `Rvat/Ipl1/Ipl2/Tl/Ecm/Exeeg/Tot`).
`_build_tax_block()` + `_accumulate_line_taxes()` accumulate per line into
those buckets; all buckets are always sent (spec: Required Y, pass 0.0).
VAT-style codes share `vatCatCd`; IPL/Tourism/Excise are separate levy fields
— they are **not** vatCatCd values. TOT never appears on a VAT-registered
taxpayer's invoice.

## D5 — Never send exchangeRt = 1 for foreign currency
Live sandbox finding (resultCd 910): ZRA rejects `exchangeRt=1` for non-ZMW
invoices. `_get_zra_exchange_rate()` resolves the real ZMW rate; 1.0 only
when the invoice is already ZMW.

## D6 — Template-level item registration
ZRA's item master is keyed per item code; this module registers
`product.template` (variants share the template's item code) to avoid
duplicate item codes for size/colour variants.

## D7 — Stock: movement first, master second
Spec dependency: `stock/saveStockItems` must succeed before
`stockMaster/saveStockMaster` sets the **absolute** remaining quantity
(`rsdQty` is the balance after the movement, not the moved amount).
Stock sync never blocks warehouse operations — failures are logged, not raised.

## D8 — Immutability is server-side and unconditional
Fiscalised invoices (`zra_sync_status = 'synced'`) reject writes to fiscal
fields, deletion, reset-to-draft and duplication — for every user including
admin. Earlier drafts allowed a context-key bypass; removed because RPC
clients can smuggle context keys (see docs/SECURITY.md). Legitimate
corrections go through credit/debit notes, as ZRA intends.

## D9 — COPY watermark via render counting
`ir.actions.report._pre_render_qweb_pdf` increments `zra_print_count` for
synced invoices; the template shows a COPY watermark and DUPLICATE badge when
count > 1. Counting renders (not button clicks) catches reprints via any path.

## D10 — Purchases: warn, don't hard-block
Two checklist rules coexist: *warn when the supplier isn't on Smart Invoice*
and *allow manual capture from unregistered suppliers* (mandatory). Resolution:
- partner carries a verified `zra_si_registration` status (Verify TPIN button);
- bill onchange warns; posting a bill marked "Automatic" for a
  not-registered supplier is blocked; "Manual" (the default) stays allowed.

## D11 — Fallback code lists are marked untrusted
Hardcoded packaging/qty unit selections exist only so a fresh install is
usable before the first code sync. Comments in `product_template.py` document
known mismatches with ZRA's live tables — always run **Sync Standard Codes**.

## D12 — POS receipt: pre-fetch, then render
`print_zra_data_patch.js` loads ZRA fields onto the order before printing and
`receipt_patch.js` checks synchronously at setup — so a print-time mount never
flashes the loading placeholder. Auto PDF downloads are disabled; the fiscal
receipt is the document of record.

## D13 — Composition = one call per component pair
`items/saveItemComposition` accepts a single parent/component pair, so a BoM
with N lines makes N calls; failures are collected per line and reported
together instead of aborting the batch.

## D14 — '001' is not an error
ZRA uses resultCd `001` ("no search result") for legitimate empty responses.
All fetch flows treat `000`/`001` as success and handle empty lists gracefully.

## Rejected alternative architectures

| Alternative | Why it didn't qualify |
|---|---|
| Direct Odoo → ZRA cloud (no VSDC) | Legally disqualified — ZRA certification requires the VSDC, which owns device registration, signing keys and the fiscal signature. Reimplementing signing in Odoo would be uncertifiable and would place key material in the app layer. |
| Event-driven / message queue (RabbitMQ, Kafka) | Fiscalisation is synchronous: receipt no., QR and signature must return before the invoice/receipt prints. Queue adds infra and ordering/idempotency complexity for no gain — the VSDC already buffers when ZRA is down. |
| OCA `queue_job` connector | Strongest alternative; rejected to stay dependency-free (stock Odoo 18 + requests/qrcode/openpyxl) and because syncs must run inline at post/payment. Failed syncs use `failed` status + manual resend. Candidate for future bulk/background retries. |
| Standalone fiscal microservice | Duplicates the VSDC's role; extra service to deploy/secure/monitor plus data duplication. The VSDC *is* the fiscal microservice. |
| No gateway (per-model HTTP calls) | Scattered auth/logging/errors; no single audit point; spec changes touch N files; endpoint surface unverifiable for the compliance checklist. |
| Batch/ETL nightly sync | ZRA requires real-time fiscalisation at point of sale — a receipt without QR/signature is non-compliant. Batch only fits reference data (done on demand). |
| Webhooks / push from ZRA | The VSDC API is pull-only (`selectX`); no push channel exists. Disqualified by spec. |
| DB-level integration (triggers/CDC) | Bypasses ORM logic and ACLs, breaks on upgrades, no app-level audit trail. |
| Full hexagonal/CQRS ceremony | Over-engineering at module scale — Odoo's ORM is already the domain layer; only the ACL concept was borrowed. |
| Browser-direct calls (POS JS → VSDC) | Exposes TPIN/device serial to the client, CORS issues, bypasses server-side logging/immutability, complicates POS offline mode. All VSDC traffic stays server-side. |

Rule of thumb: rejected options either violate a ZRA constraint, duplicate
something the VSDC/Odoo already provide, or trade away auditability/security
for no benefit. Only `queue_job` remains a legitimate future enhancement.
