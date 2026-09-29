# Architecture

## Overview

```
┌─────────────┐   HTTPS/HTTP   ┌──────────────┐   mTLS/HTTPS   ┌─────────────┐
│   Odoo 18   │ ─────────────► │  VSDC (local │ ─────────────► │  ZRA Smart  │
│  (this mod) │ ◄───────────── │   device)    │ ◄───────────── │  Invoice    │
└─────────────┘   JSON/POST    └──────────────┘                └─────────────┘
```

Odoo never talks to ZRA directly. All fiscal traffic goes through the
taxpayer's VSDC instance (`zra.config.vsdc_url`, default
`http://localhost:8080/zravsdc`). The VSDC signs fiscal data and returns
receipt numbers, internal data, signatures, SDC ID and MRC numbers.

## Architecture pattern

**Layered Anti-Corruption Layer (ACL) with a Gateway, over Odoo's MVC —
integrating through a fiscal middleware/sidecar (the VSDC).**

| Pattern | Where | Why it's the best fit |
|---|---|---|
| Anti-Corruption Layer (DDD) | `zra.api.client` translates Odoo domain objects ↔ ZRA's fiscal schema | ZRA's field names, tax buckets and quirks (`001` = no results, `902` = already registered) never leak into Odoo models; spec changes touch one file |
| Gateway | `_make_request()` — single choke point for all 19 endpoints | one place for auth, timeout, error mapping and audit logging; the endpoint surface is grep-able for compliance reviews |
| Layered (N-tier) | API layer → domain extensions → views/reports/POS JS | separation of concerns; each layer testable in isolation |
| Sidecar / intermediary | Odoo → VSDC → ZRA | the VSDC topology is legally mandated by ZRA — embraced, not fought; no direct-to-cloud path to maintain |
| Observer | syncs hooked on `action_post`, `_action_done`, `action_pos_order_paid` | idiomatic, upgrade-safe Odoo extension (no core monkey-patches); non-blocking — a VSDC outage never stops a sale or stock move |
| Append-only audit log | `zra.api.log` via independent cursor | fiscal evidence survives transaction rollback — a legal audit-trail requirement |

## Layers

### 1. API layer — `zra.api.client` (AbstractModel)
Single gateway for all VSDC traffic. `_make_request()` centralises:
URL building, JSON encoding, `Content-Type` header, timeout, response parsing,
error mapping (Timeout / ConnectionError / HTTPError / generic → `UserError`),
and audit logging.

Implemented endpoints (VSDC spec v1.0.7):

| Group | Endpoints |
|---|---|
| Init | `initializer/selectInitInfo` |
| Codes | `code/selectCodes`, `itemClass/selectItemsClass` |
| Items | `items/saveItem`, `items/updateItem`, `items/selectItems`, `items/saveItemComposition` |
| Sales | `trnsSales/saveSales` (invoices, credit notes, debit notes) |
| Purchases | `trnsPurchase/savePurchase`, `trnsPurchase/selectTrnsPurchaseSales` |
| Imports | `imports/selectImportItems`, `imports/updateImportItems` |
| Stock | `stock/saveStockItems`, `stockMaster/saveStockMaster`, `stock/selectStockItems` |
| Branch | `branches/saveBrancheCustomers`, `branches/saveBrancheUser`, `branches/selectBranches`, `customers/selectCustomer` |

### 2. Configuration — `zra.config` (mail.thread)
Per-company singleton-style record: VSDC URL, TPIN, branch ID, device serial,
environment, auto-sync flags, cached branch info, sync statistics. Hosts the
sync actions (initialize, test connection, sync codes/items/stock, fetch
branch info). `get_active_config(company_id)` is the entry point used by
every flow.

### 3. Audit — `zra.api.log`
Every request/response persisted with endpoint, status, timestamp and links
to the originating invoice / POS order. Written with an **independent cursor**
(`self.env.registry.cursor()`) so log entries survive transaction rollback —
a failed invoice post still leaves its ZRA attempt auditable.

### 4. Reference data
- `zra.standard.code` — VSDC constants (payment types, units, …)
- `zra.classification.code` — UNSPSC codes (level-4 enforced on products)

### 5. Core-model extensions

| Model | Adds |
|---|---|
| `account.move` | ZRA receipt fields (rcptNo, intrlData, rcptSign, sdcId, mrcNo, QR image, sync status), payment/refund/debit reason codes, purchase reg fields, immutability guards (`write`/`unlink`/`button_draft`/`copy`), auto-sync on post, debit-note flow, supplier SI-registration gate |
| `account.move.line` | `zra_line_tax_amount` (powers the registers) |
| `res.partner` | TPIN (validated, unique), save/fetch branch customer, Smart-Invoice registration status + verify action |
| `res.users` | Register as ZRA branch user |
| `product.template` / `product.product` | ZRA item code, UNSPSC class, tax types (VAT + IPL/TL/excise), units, origin, RRP, register/update actions, bulk registration |
| `mrp.bom` | Send composition to ZRA (one call per component pair) |
| `stock.move` | Auto stock sync on `_action_done` (never blocks operations) |
| `pos.order` | Auto fiscalisation on payment; manual resend; ZRA fields exported to the receipt |
| `ir.actions.report` | Counts PDF renders of synced invoices → COPY/DUPLICATE watermark |

### 6. Wizards / helper models
`zra.purchase.fetch` (pull purchases → draft bills), `zra.import.declaration`
+ lines + fetch wizard (imports flow), `zra.stock.adjustment` (manual stock
reporting).

### 7. POS front-end (`static/src/`)
- `receipt_patch.js` — reactive ZRA block on the receipt (loading → data)
- `print_zra_data_patch.js` — pre-fetches ZRA fields onto the order before
  print so the receipt never flashes the loading state
- `disable_*_autodownload.js` — stop browser auto-download of invoice PDFs
- `pos_receipt.xml` / `simple_receipt.xml` — fiscal receipt templates

### 8. Reports
`report/invoice_report_zra.xml` — QWeb PDF implementing every mandatory
tax-invoice feature (supplier/customer TPIN & address, line detail with tax
rates, totals incl. discount rate, QR code, SDC info block, COPY watermark).
`views/zra_sales_register_views.xml` / `views/zra_purchase_register_views.xml`
— line-level registers with tax amounts, exportable to Excel/CSV.

## Key data flows

### Invoice fiscalisation
```
action_post() ──► submit_sale() ──► trnsSales/saveSales
                     │                    │
                     │                    ▼
                     │         rcptNo / intrlData / rcptSign /
                     │         sdcId / mrcNo stored on move
                     ▼
            _compute_qr_code_image() (qrcode → base64 PNG)
```

### POS fiscalisation
```
action_pos_order_paid() ──► ensure partner ──► create invoice
        ──► post invoice ──► submit_sale() ──► receipt shows ZRA block + QR
```

### Stock (spec dependency order)
```
stock.move._action_done() ──► stock/saveStockItems (movement)
                          ──► stockMaster/saveStockMaster (absolute rsdQty)
```

### Purchases
```
Fetch wizard ──► trnsPurchase/selectTrnsPurchaseSales ──► draft vendor bills
Manual bill ──► (supplier SI check) ──► trnsPurchase/savePurchase (regTyCd M/A)
```

### Imports
```
Fetch declarations ──► link lines to products ──► imports/updateImportItems
                                                ──► create stock receipt
```
