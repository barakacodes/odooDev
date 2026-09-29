# ZRA Smart Invoice (VSDC) — Odoo 18 Module

`zra_smart_invoice_new` integrates Odoo 18 (Community/Enterprise) with the
**Zambia Revenue Authority (ZRA) Smart Invoice** system via a locally deployed
**VSDC** (Virtual Sales Data Controller). It fiscalizes customer invoices,
credit/debit notes and POS receipts in real time, and synchronises items,
stock, purchases, imports, customers, users and branch data with ZRA.

- **Version:** 18.0.1.8.0
- **Depends on:** `base`, `account`, `sale`, `point_of_sale`, `stock`, `product`, `mrp`
- **Python deps:** `requests`, `qrcode`, `openpyxl`
- **VSDC API spec:** v1.0.7 (field names / codes follow the spec throughout)

## Features

| Area | What you get |
|---|---|
| Sales | Auto fiscalisation of invoices & credit notes on post; manual re-sync button; debit notes linked to original invoices |
| POS | Auto fiscalisation on payment; fiscal receipt with QR code, SDC ID, MRC no., internal data & signature; no auto PDF download |
| Reports | **ZRA Fiscal Invoice** PDF (all mandatory tax-invoice features + COPY watermark on reprints), Sales Register & Purchase Register (Excel/CSV exportable) |
| Items | Product registration/update (`items/saveItem`, `items/updateItem`), UNSPSC classification codes, item composition from BoMs |
| Stock | Automatic movement reporting (`stock/saveStockItems`) + absolute quantity updates (`stockMaster/saveStockMaster`); adjustment wizard |
| Purchases | Fetch supplier invoices from ZRA into draft vendor bills; manual purchase capture; supplier Smart-Invoice registration check |
| Imports | Fetch import declarations, link to products, transmit updates (`imports/updateImportItems`), create stock receipts |
| Reference data | VSDC constants (`code/selectCodes`) and UNSPSC codes (`itemClass/selectItemsClass`) synced into local tables |
| Compliance | Fiscalised invoices are immutable: no edit / delete / reset-to-draft / duplicate — for **any** user including admin |
| Audit | Full request/response API log (survives rollbacks), chatter tracking, raw ZRA responses on declarations |

## Installation

```bash
pip install requests qrcode openpyxl
# copy module into your addons path, then:
odoo -u zra_smart_invoice_new -d <database>
```

## Configuration

1. **ZRA Smart Invoice → Configuration → ZRA Settings** — create a config per company:
   VSDC URL (e.g. `http://localhost:8080/zravsdc`), TPIN (10 digits), Branch ID,
   Device Serial, Environment (Sandbox/Production).
2. Click **Initialize Device** (Sandbox: *Force Sandbox Init* is available for testing).
3. Run **Sync Standard Codes** and **Sync Classification Codes**.
4. Set UNSPSC classification + ZRA tax type on products, then **Register with ZRA**.
5. Optional auto-sync toggles: invoices, POS orders, purchases, stock.

## Daily usage

- **Invoices:** post as usual — fiscalisation happens automatically; ZRA data
  (receipt no., QR, SDC ID…) is stored on the invoice and printed via
  *Print → ZRA Fiscal Invoice*.
- **POS:** pay as usual — the receipt shows the ZRA fiscal block with QR code.
- **Purchases:** *Fetch Purchases* wizard pulls supplier invoices from ZRA;
  use **Verify TPIN on ZRA** on supplier contacts to check Smart Invoice registration.
- **Stock:** movements report automatically; use the *ZRA Stock Adjustment*
  wizard for manual adjustments.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, models, data flows
- [docs/DESIGN.md](docs/DESIGN.md) — design decisions and rationale
- [docs/SECURITY.md](docs/SECURITY.md) — security model & hardening
- [docs/COMPLIANCE.md](docs/COMPLIANCE.md) — ZRA VSDC checklist mapping

## Backup

`tools/odoo_daily_backup.sh` — nightly pg_dump of client databases (cron-ready,
retention purge, optional filestore archive). See the script header for setup.
