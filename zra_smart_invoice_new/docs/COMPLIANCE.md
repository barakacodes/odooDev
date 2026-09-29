# ZRA VSDC Compliance Mapping

Maps the **Smart-Invoice-VSDC-Developers-Self-Check-list** workbook (and the
internal checklist) to the module's implementation. `*` = mandatory per ZRA.

## Endpoint sheets (17/17 implemented)

| Workbook tab | VSDC endpoint | Code |
|---|---|---|
| Device Initialization | `initializer/selectInitInfo` | `zra.api.client.initialize_device` |
| Get Code Data Sequence | `code/selectCodes` | `get_standard_codes` + config sync action |
| Get Branch Customers | `customers/selectCustomer` | `get_branch_customer` / partner *Fetch from ZRA* |
| Save Branch Customers | `branches/saveBrancheCustomers` | `save_branch_customer` / partner *Save to ZRA* |
| Item Class | `itemClass/selectItemsClass` | `get_classification_codes` + sync action |
| Save Branch User | `branches/saveBrancheUser` | `save_branch_user` / user form action |
| Get Branch Information | `branches/selectBranches` | `get_branch_info` / *Fetch Branch Info* |
| Save Items | `items/saveItem` (+`items/updateItem`) | product *Register/Update with ZRA* |
| Get Item List | `items/selectItems` | config *Sync Items from ZRA* |
| Get Import Items | `imports/selectImportItems` | declaration fetch (single + range wizard) |
| Update Import Item | `imports/updateImportItems` | declaration *Update Items on ZRA* (v18.0.1.6.0) |
| Save Sales | `trnsSales/saveSales` | `submit_sale` / `submit_debit_note` |
| Get Purchases | `trnsPurchase/selectTrnsPurchaseSales` | `zra.purchase.fetch` wizard |
| Get Stock Item List | `stock/selectStockItems` | config *Sync Stock from ZRA* |
| Save Stock Item | `stock/saveStockItems` | auto on `stock.move._action_done` + wizard |
| Save Stock Master | `stockMaster/saveStockMaster` | absolute `rsdQty` after each movement |
| Save Purchases | `trnsPurchase/savePurchase` | bill *Send Purchase to ZRA* (regTyCd M/A) |

Generic template sections (URL, params, headers, body, POST, response,
errors, security) are centralised in `_make_request()` — see
docs/ARCHITECTURE.md §1. Authorization header: N/A by VSDC architecture
(D1 in docs/DESIGN.md). Portal-verification rows (sales/stock/purchases
showing on the taxpayer portal) are **manual checks** during testing.

## Functional checklist (32 items)

| # | Item | Status | Implementation |
|---|---|---|---|
| 1* | Device initialisation | ✅ | config *Initialize Device* (handles 000/902) |
| 2* | Retrieve VSDC constants | ✅ | `code/selectCodes` → `zra.standard.code` |
| 3* | Retrieve & save classification codes | ✅ | `itemClass/selectItemsClass` → `zra.classification.code` |
| 4 | Save branch customers | ✅ | partner *Save to ZRA* |
| 5 | Retrieve branch customers | ✅ | partner *Fetch from ZRA* |
| 6 | Save branch users | ✅ | user *Save as ZRA Branch User* |
| 7* | Retrieve branch details | ✅ | config *Fetch Branch Info* |
| 8* | Save items | ✅ | `items/saveItem` / `updateItem` / bulk |
| 9* | Save item composition | ✅ | BoM *Send Composition to ZRA* |
| 10* | Retrieve items | ✅ | config *Sync Items from ZRA* |
| 11* | Retrieve import items | ✅ | `imports/selectImportItems` |
| 12* | Update import items | ✅ | `imports/updateImportItems` (v18.0.1.6.0) |
| 13* | Save retrieved purchases | ✅ | fetch wizard → draft bills |
| 14* | Retrieve purchases | ✅ | `trnsPurchase/selectTrnsPurchaseSales` |
| 15* | Manual purchase (unregistered supplier) | ✅ | `zra_purchase_reg_type = 'M'` |
| 16* | Record & upload sales | ✅ | auto on post / POS; manual resend |
| 17* | Unique consecutive invoice numbers | ✅ | Odoo `ir.sequence` per journal |
| 18* | Invoice numbers immutable | ✅ | `name` in `_ZRA_PROTECTED_FIELDS` (`account_move.py`) — closes the core "Resequence" wizard bypass an external audit found live in 18.0.1.8.0 (v18.0.1.9.0) |
| 19* | Tax-invoice minimum features (i–ix) | ✅ | `report/invoice_report_zra.xml` — incl. discount rate (v18.0.1.6.0) |
| 20* | Credit notes | ✅ | refund flow + reason codes |
| 21* | Debit notes | ✅ | debit flow + reason codes, linked to original |
| 22* | No edit after generation | ✅ | `write()` guard, unconditional — now covers `account.move.line`/`pos.order.line` directly too, not just the parent record (v18.0.1.9.0; the line-level gap was real, see docs/SECURITY.md) |
| 23* | No delete after generation | ✅ | `unlink()` guard, same line-level coverage as above |
| 24* | Reprint marked copy/duplicate | ✅ | print counting + COPY watermark on the module's own report; core's "Invoice PDF"/"PDF without Payment" report actions are unbound from `account.move` (v18.0.1.9.0) so they can no longer produce an unmarked duplicate |
| 25 | Backup strategy | ✅ | `zra.backup.log` + "ZRA: Nightly Database Backup" `ir.cron` (v18.0.1.9.0) — self-scheduling, no server crontab needed; `tools/odoo_daily_backup.sh` remains available for host-level/offsite backups |
| 26* | User passwords / access control | ✅ | Odoo auth + ACLs + record rules |
| 27* | Save stock items | ✅ | `stock/saveStockItems` |
| 28* | Retrieve stock items | ✅ | `stock/selectStockItems` |
| 29* | Update stock quantities | ✅ | `stockMaster/saveStockMaster` |
| 30* | Reports Excel/CSV/PDF | ✅ | native export + QWeb PDF |
| 31* | Transaction report w/ tax | ✅ | ZRA Sales Register (+ Purchase Register v18.0.1.7.0) |
| 32* | Audit trail | ✅ | `zra.api.log` (rollback-safe) + chatter |

## Internal checklist additions

| Item | Status | Implementation |
|---|---|---|
| Supplier not on Smart Invoice → warn/error | ⚠️ Partial | `action_post()` blocks the combination `zra_si_registration == 'not_registered'` + `zra_purchase_reg_type != 'M'` — real, model-level, RPC-safe. But `zra_si_registration` defaults to `'unknown'` and is only ever set by a human clicking "Verify TPIN on ZRA" first, and `zra_purchase_reg_type` defaults to `'M'` — so in practice almost every bill sails through unblocked unless someone opts in to verification. This is legitimate per checklist #15's "Manual" allowance, but don't present it as an automatic block — it's an available control, not a default-on one. |
| No edit/delete/archive incl. admin | ✅ | unconditional guards on `account.move` **and** `account.move.line`/`pos.order`/`pos.order.line` (v18.0.1.9.0 closed a direct-line-write bypass — see docs/SECURITY.md); posted invoices are read-only in core; no archive on `account.move` |
| No duplicate of fiscalised invoices | ✅ | `copy()` guard (v18.0.1.7.0) on `account.move`, **and** core's own unmarked "PDF without Payment"/"Invoice PDF" report actions are unbound from `account.move` (v18.0.1.9.0) so they can no longer be used as an unmarked-duplicate side door |
| Nightly DB backup | ✅ | `zra.backup.log` + "ZRA: Nightly Database Backup" `ir.cron` (v18.0.1.9.0) — genuinely self-scheduling and auditable in-DB. Before this version the claim was overstated: `tools/odoo_daily_backup.sh` existed but nothing installed its cron schedule anywhere, so "automatic" wasn't actually true until now. |
| Purchases report with tax | ✅ | ZRA Purchase Register (v18.0.1.7.0) |
| Sales report with tax | ✅ | ZRA Sales Register |

## Manual steps remaining (not code)
1. ~~Tick each endpoint sheet's boxes while testing against the ZRA
   sandbox.~~ Done live on 2026-09-13 for every mandatory endpoint except
   `imports/updateImportItems` (no import declaration exists for this TPIN
   on the sandbox to update — `imports/selectImportItems` itself was
   exercised and correctly returned "no search result") and credit notes
   (not yet exercised live, though the flow is symmetric with the
   already-confirmed debit-note flow). See "Live checklist walkthrough,
   2026-09-13" below for the full per-item log.
2. Portal checks: confirm sales/stock/purchases appear on the taxpayer
   portal. The QR/verification URL returned by a live sale (below) responds
   HTTP 200; a human still needs to open it and visually confirm the
   invoice details render correctly on the portal.
3. Fill the TAXPAYER DETAILS sheet.
4. Verify `imptItemSttsCd = '2'` (Confirmed) against the current spec code
   table before the ZRA demo.
5. Decide whether "Verify TPIN on ZRA" should be made mandatory before a
   vendor bill can post (currently opt-in — see the Internal checklist row
   above) — a policy decision, not a bug, but worth deciding deliberately
   before the ZRA demo rather than leaving it implicit.

## Live sandbox test, 2026-09-13 (v18.0.1.10.0)
Ran the mandatory chain against the real ZRA VSDC sandbox
(`http://159.198.68.21:8080/zravsdc`, TPIN 1001962263, branch 000) from a
fresh Odoo test company (default demo data, company currency USD) rather
than just reviewing the code:

- `items/saveItem` — registered a real product, `resultCd 000` first try.
- `trnsSales/saveSales` — found and fixed two real bugs before a sale would
  go through cleanly (see docs/SECURITY.md's 18.0.1.10.0 row for detail):
  a silent `exchangeRt=1.0` fallback for non-ZMW companies (ZMW ships
  inactive by default), and no pre-flight check that a line's actual VAT
  rate matches its `vatCatCd`'s fixed ZRA rate. After both fixes, a proper
  16%-taxed invoice fiscalized cleanly: `resultCd 000`, real receipt
  `rcptNo 137`, `sdcId SDC0060000925`, `mrcNo WIS00003942`, and a working
  `qrCodeUrl` to `sandboxportal.zra.org.zm` (HTTP 200).
- Also hit (and confirmed as expected, not a bug) `resultCd 924 "CIS
  Invoice number already exists"` when re-submitting an Odoo-sequence
  invoice number this same TPIN had already sent to ZRA in an earlier,
  separate test session — this is checklist #17's uniqueness requirement
  working correctly against a long-lived shared sandbox account, not a
  module defect; a real deployment's Odoo sequence and ZRA's own record
  only diverge like this if a DB is restored from a stale backup, which is
  exactly what the 18.0.1.9.0 nightly-backup work exists to avoid needing.
- Immutability guards re-verified against this real, ZRA-synced invoice
  (not a synthetic `zra_sync_status` flag): direct `account.move.line`
  write, `name` write, `copy()`, and `unlink()` all correctly raised
  `UserError`.
- Schema/data changes from 18.0.1.9.0 confirmed present in a live DB after
  a real `-u` upgrade: `zra.api.log.result_code`/`result_message` columns,
  `zra.backup.log` table, the "ZRA: Nightly Database Backup" `ir.cron`
  (which had already run once successfully before this test began), and
  both core invoice report actions (`account.account_invoices`,
  `account.account_invoices_without_payment`) confirmed unbound
  (`binding_model_id` empty) via direct DB query.

## Live checklist walkthrough, 2026-09-13 (v18.0.1.11.0)
Went through the internal developer checklist item by item against the
same live sandbox, not just re-reading code:

| Item | Result |
|---|---|
| Save/retrieve branch customer (`branches/saveBrancheCustomers` + `customers/selectCustomer`) | ✅ live — saved "Acme Corporation" (TPIN 1001962263), then fetched it back with matching name/address/email |
| Save branch user (`branches/saveBrancheUser`) | ✅ live, `resultCd 000` |
| Retrieve branch details (`branches/selectBranches`) | ✅ live — returned both real branches on file (Headquarter + Mango Branch). First attempt hit a transient sandbox `400 Bad Request` on this and 2 other endpoints in the same rapid-fire batch; isolated retries all succeeded — treated as sandbox-side flakiness under load, not a module defect, since the same call's own payload was verified correct in isolation |
| Save item composition for a BoM (`items/saveItemComposition`) | ✅ live — registered a parent + 3 components, then synced all 3 composition pairs, `resultCd 000` each |
| Retrieve saved items (`items/selectItems`) | ✅ live — 21 items on file for this TPIN, reconciled against local products |
| Retrieve import items (`imports/selectImportItems`) | ✅ live — correctly returned `resultCd 001` "no search result" (this TPIN has no import declarations on the sandbox); `imports/updateImportItems` not exercised as a result — nothing to update |
| Retrieve + save purchases (`trnsPurchase/selectTrnsPurchaseSales`) | ✅ live — 8 real supplier transactions retrieved, 8 draft vendor bills created |
| Supplier-TPIN warning before posting a purchase | ✅ confirmed working, but only once a supplier's `zra_si_registration` is genuinely `'not_registered'` (i.e. someone has clicked "Verify TPIN on ZRA" first) — a supplier still at the default `'unknown'` status posts unblocked. This is the same opt-in limitation already flagged in the Internal checklist section below, now reproduced live rather than just reasoned about |
| Debit note (`trnsSales/saveSales`, `rcptTyCd='D'`) | ✅ live — real fiscalized debit note against the earlier-synced invoice, `resultCd 000`, receipt 138 |
| Stock update after a stock movement (`stock/saveStockItems` + `stockMaster/saveStockMaster`) | ✅ live once fixed — found and fixed a real bug: the sync fired even for a move that never actually reached `state == 'done'` (see docs/SECURITY.md's 18.0.1.11.0 row) |
| Immutability (edit/delete/duplicate) on a real synced invoice | ✅ re-confirmed: line write, `name` write, `copy()`, `unlink()` all blocked. Also found and fixed a real regression: the line-level guard as shipped in 18.0.1.9.0 blocked Odoo's own internal recompute writes too, breaking the Print button for any synced invoice (see docs/SECURITY.md) |
| Archive a posted invoice | N/A — `account.move` has no `active` column in this Odoo version at all; there is no archive action to disable |
| Fiscal PDF: supplier TPIN/name/address, customer TPIN/name/address, SDC info (invoice type, VSDC date, SDC ID, MRC, QR) | ✅ visually confirmed on a real rendered PDF once two more bugs found this pass were fixed: a hard crash on any taxed line (see SECURITY.md) and a duplicated currency symbol on every amount |
| Sales/Purchase register reports | Data present to check (2 synced sales, 14 vendor bills in the test DB) but not visually walked through this pass |

## External audit, 2026-09-11 (v18.0.1.9.0)
An independent adversarial review of this module's own compliance claims
(not just the spec) found and closed six real gaps before this doc could be
presented to ZRA: two fiscal-immutability bypasses (direct line writes;
the core Resequence wizard), one unmarked-duplicate bypass (core's own
invoice PDF report), one access-control gap (ZRA sync actions triggerable
by non-managers because the check lived only in the view/final-write ACL,
not the action itself), one audit-trail inconsistency (`zra.api.log`
didn't surface ZRA's own `resultCd` next to the transport status), and one
overstated claim (nightly backup had no actual schedule installed
anywhere). See docs/SECURITY.md's Hardening History for the full list and
`git log` for the actual diffs — everything above marked "(v18.0.1.9.0)" is
a fix from this pass, not a pre-existing feature.
