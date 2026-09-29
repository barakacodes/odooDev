# Security Model & Hardening

## Assets
- **VSDC credentials** — TPIN + branch ID + device serial (authenticate every
  VSDC call), stored on `zra.config`
- **API payloads** — full request/response bodies in `zra.api.log` and server
  logs (contain TPIN/device serial → credential-equivalent)
- **Fiscal integrity** — submitted invoices must stay immutable (ZRA law)
- **Client databases** — nightly backups (`tools/odoo_daily_backup.sh`)

## Controls

### Access control
- `security/ir.model.access.csv` — least privilege: invoicing users read-only
  on config/logs/reference data; write restricted to account/stock managers.
- `security/zra_security.xml` — **multi-company record rules** on `zra.config`,
  `zra.api.log`, `zra.import.declaration(.line)`: users only see their own
  companies' ZRA data. Reference-data tables are ZRA-wide constants and stay
  shared intentionally.

### Fiscal immutability (no bypass)
`account.move` overrides — apply to **all users including admin**:
| Guard | Blocks once synced |
|---|---|
| `write()` | edits to fiscal fields incl. `name` (the ZRA-reported invoice number — see below), partner, date, currency, ref, journal, narration — **unconditional; no context-key bypass** |
| `unlink()` | deletion |
| `button_draft()` | reset to draft |
| `copy()` | duplication |

Two bypasses an external audit found and closed in 18.0.1.9.0 (both were
real, not theoretical):
- **Line-level edits.** The `account.move` guard above only intercepts
  writes routed through the parent record. `account.move.line` had no
  guard of its own, so `env['account.move.line'].browse(id).write(...)` —
  a direct RPC call, `odoo shell`, a bulk update, an import — silently
  changed a fiscalized invoice's totals. `models/account_move_line.py` now
  has the same `write()`/`unlink()`/`create()` guard, keyed off the line's
  own `move_id.zra_sync_status`. `pos.order`/`pos.order.line` got the same
  treatment for the same reason.
- **Core "Resequence" wizard.** `name` (the number ZRA received as
  `cisInvcNo` and that's printed on the fiscal PDF) was not in the
  protected field set, so Odoo's own built-in Invoices ▸ Action ▸
  Resequence wizard — reachable by any ordinary invoicing user — could
  renumber an already-synced invoice with no error and no re-sync. `name`
  is now protected like every other fiscal field.

Corrections happen via credit/debit notes, per ZRA rules.

### Reprint / duplicate control — report-agnostic, not just our own template
The COPY watermark and print-count tracking (`models/ir_actions_report.py`,
`report/invoice_report_zra.xml`) only ever fired for this module's own "ZRA
Fiscal Invoice" report. Odoo's core "Invoice PDF" / "PDF without Payment"
report actions are bound to `account.move`'s Print menu by default and
render a completely different, unmarked template with none of the ZRA
SDC/QR fiscal details — so anyone could pick that instead and get an
unlimited, unmarked "duplicate" of a fiscalized invoice. `data/zra_report_
binding_data.xml` unbinds both core report actions from `account.move`
(re-asserted on every upgrade, in case a core update re-adds them), leaving
the compliant ZRA report as the only print path.

### Injection / XSS surface
- No raw SQL — 100% ORM.
- No `t-raw` in QWeb; all report output escaped.
- No `innerHTML` / `eval` / `document.write` in POS JS.
- No controllers / `@http.route` — the module exposes zero web endpoints.
- Outbound HTTP: TLS verification left at the `requests` default (on),
  configurable timeout, VSDC URL editable only by account managers.

### Backdoors / test hooks
- `action_force_sandbox_init` (skips real device initialisation) is blocked
  unless `environment = 'sandbox'`, and config write is manager-only.

### ZRA action buttons are manager-gated at the model level, not just the view
Every `zra.config` action that calls the live VSDC and/or persists data via
`.sudo()` (classification/standard-code sync, item/stock sync, branch-info
fetch, device init, test connection) now starts with
`_check_zra_manager_access()`, which raises unless the calling user has
`account.group_account_manager` — enforced in the method itself, so it
can't be defeated by calling the method directly (RPC, `odoo shell`) the
way a `groups=` attribute on the button alone could be. `views/zra_config_
views.xml` also has `groups=` on these buttons now, but that's UI
convenience layered on top of the real check, not the enforcement itself.
Before 18.0.1.9.0 none of this existed: the ACL's `perm_write=0` for
invoicing users only stopped the *final* bookkeeping write on `zra.config`
— by which point several of these actions had already fired the outbound
HTTP request and persisted `.sudo()`-created rows.

### Backups
- `tools/odoo_daily_backup.sh` remains available for host-level backups to
  a separate machine (real disaster-recovery protection — a dump sitting on
  the same volume as the live DB is not). But nothing ever installed its
  cron schedule automatically; "automatic" was aspirational, not real,
  until 18.0.1.9.0.
- **The default path is now `zra.backup.log` + the "ZRA: Nightly Database
  Backup" `ir.cron`** (`models/zra_backup.py`, `data/zra_backup_cron.xml`)
  — an Odoo-native scheduled action needing no per-server crontab setup,
  visible under Settings ▸ Technical ▸ Scheduled Actions, that leaves an
  auditable row (success/failed, file path, size, duration) every time it
  runs. `pg_dump` credentials are passed via `PGPASSWORD` in the child
  process's environment only — never on the command line or written to
  disk/logs. Dumps are written `chmod 600`.

## Hardening history
| Version | Change |
|---|---|
| 18.0.1.11.0 | Walked the internal developer checklist live in the UI/sandbox item by item (branch customers/users, item composition, import declarations, purchase fetch, supplier-TPIN warning, debit notes, stock sync, the fiscal PDF report) and found four more real bugs, all confirmed live: (1) `stock.move._action_done()`'s ZRA sync loop iterated the pre-super() recordset without checking the resulting `state` — a move that super() left short of `'done'` (e.g. unpicked lines) still got synced to ZRA as a completed stock movement; now skips any move not actually `state == 'done'`. (2) The 18.0.1.9.0 line-level immutability guard on `account.move.line` was unconditional on ANY write, which broke printing a synced invoice entirely — Odoo core's own `_sync_dynamic_line` batches a dirty-flag write (`discount_allocation_dirty`) across a synced invoice's product/tax/payment-term lines together as a normal side effect of computing totals for the report; scoped the guard to a `_ZRA_PROTECTED_LINE_FIELDS` allowlist (mirroring `account.move`'s own `_ZRA_PROTECTED_FIELDS` pattern) so real content edits are still blocked but core's internal bookkeeping isn't. (3) The fiscal invoice report's per-line tax-rate `t-esc` used `'%s%%' % t.amount` — two percent signs — which Odoo's own XML data loader (`odoo/tools/convert.py`, `s.replace('%%', '%')`, an explicit "backward compatibility" behavior) collapses to `'%s%'` on every module install/upgrade, which Python raises `ValueError: incomplete format` on for ANY invoice with a tax line — a hard crash that broke printing entirely, not a cosmetic glitch; needs four percent signs in the source to survive the loader's collapse and still read `%%` at QWeb render time. (4) Every monetary amount in that same report printed its currency symbol twice — `t-field` on a Monetary field already renders with its currency symbol, and the template also had an explicit, redundant `t-field="o.currency_id.symbol"` span right after each one (line item amount, untaxed amount, discount, total tax, total) — removed. See docs/COMPLIANCE.md's "Live checklist walkthrough, 2026-09-13" section for the full per-item test log. |
| 18.0.1.10.0 | Live sandbox test pass against the real ZRA VSDC sandbox (not just code review) found and fixed two correctness bugs that only surface when a sale is actually submitted: (1) `_get_zra_exchange_rate` fell back to `exchangeRt=1.0` for a non-ZMW company currency whenever the ZMW currency record is inactive — which it is on a fresh Odoo install (`search()` filters inactive records by default) — reproducing the exact ZRA rejection ("Exchange rate for USD Cannot be 1") the code's own comment said was already fixed; now raises a clear pre-flight error instead of silently sending 1.0, and also refuses a ZMW rate older than 30 days rather than use Odoo's stale seed-data rate (2010-01-01) to compute tax figures reported to ZRA. (2) Neither the sales/debit-note nor the purchase item-list builder checked that the Odoo tax actually applied to a line matches the fixed rate ZRA expects for that line's `vatCatCd` (e.g. category `A` = 16% exactly) — a mismatched tax (confirmed live: a 15% tax on a category-`A` line) got a cryptic `resultCd 910 "Wrong Amount computation"` from ZRA with no indication it was a rate mismatch; added `_check_zra_vat_rate()`, called from both builders, to catch this before the call goes out. See docs/COMPLIANCE.md's "Live sandbox test, 2026-09-13" section for the full test log, including a real fiscalized invoice (SDC receipt 137) and confirmation that all four immutability guards (line write, `name` write, `copy()`, `unlink()`) still hold against a live-synced record. |
| 18.0.1.9.0 | External audit fix pass: line-level immutability on `account.move.line`/`pos.order.line` (direct-write bypass); `name` added to the protected-field set (Resequence-wizard bypass); core invoice report actions unbound from `account.move` (unmarked-duplicate bypass); manager-only gate moved into the model layer for every ZRA sync/init action; `zra.api.log` now records ZRA's own `resultCd`/`resultMsg` alongside the transport status; HTTPS required when `environment = 'production'`; nightly backup is now a real `ir.cron` with an auditable log, not just a script someone has to remember to schedule |
| 18.0.1.8.0 | Removed unused `zra_allow_synced_write` context bypass (RPC-smugglable); added multi-company record rules; backup script umask 077; purged `__pycache__` artifacts |

## Operational recommendations
1. Restrict Odoo server log file permissions — API payloads are logged at
   INFO; consider WARNING level for this module in production.
2. HTTPS for `vsdc_url` is enforced (not just recommended) once
   `environment = 'production'` — see `_check_https_in_production` in
   `models/zra_config.py`.
3. Protect Odoo's database manager with a strong master password.
4. Keep `environment = 'production'` on live instances so the sandbox
   force-init hook stays inert.
5. Review `zra.api.log` retention — payloads accumulate; archive/purge on a
   schedule that matches your audit obligations.
6. Point `ir.config_parameter` `zra_smart_invoice_new.backup_dir` at a
   volume that's itself backed up/replicated off the Odoo host — the
   in-Odoo nightly backup protects against data mistakes, not against
   losing the machine entirely.
