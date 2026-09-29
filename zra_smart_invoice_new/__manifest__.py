# -*- coding: utf-8 -*-
{
    'name': 'ZRA Smart Invoice Integration',
    'version': '18.0.1.11.0',
    'category': 'Accounting/Localizations',
    'summary': 'Zambia Revenue Authority Smart Invoice Integration (VSDC API v1.0.7)',
    'description': """
        ZRA Smart Invoice Integration for Odoo 18
        ==========================================

        Developed by Esau Ngoma, Full Stack Software Engineer
        Contact: esaungoma004@gmail.com

        v1.5.0 — VSDC API Specification v1.0.7 Compliance Pass

        Rebuilt against the official ZRA VSDC API Specification v1.0.7 and
        the ZRA Smart Invoice Developer Self-Check List (32-item functional
        checklist). Fixed real correctness bugs found by cross-referencing
        every payload against the spec's own JSON samples, not just gaps:

        * Removed a fabricated 'invcNo' request field that doesn't exist
          anywhere in the spec; orgInvcNo on credit/debit notes now
          references ZRA's actual assigned receipt number, not Odoo's
          internal database id
        * Fixed credit note refund reason key (was 'rfndRsnCd', spec says
          'rfdRsnCd' — refund reasons were being silently dropped)
        * Rebuilt the tax model to the full VAT/IPL/TL/Excise/TOT category
          set (previously only VAT-style buckets existed; TOT was wrongly
          aliased into the Service Charge bucket)
        * Corrected payment type labels (codes 03-06 were mismatched
          against the real code table) and stock movement codes (previously
          a fabricated 01-16 list with no basis in the spec's real 12-code table)
        * Fixed stock sync to properly chain saveStockItems -> saveStockMaster
          with correct absolute-remaining-quantity semantics
        * Relaxed supplier TPIN validation on purchases — it's optional per
          spec, and the hard requirement was blocking manual capture from
          suppliers not registered on Smart Invoice
        * Added previously-missing endpoints: items/updateItem (mandatory),
          items/selectItems, items/saveItemComposition, imports/updateImportItems,
          trnsPurchase/selectTrnsPurchaseSales (mandatory), stock/selectStockItems,
          branches/selectBranches (mandatory), branches/saveBrancheCustomers,
          customers/selectCustomer, branches/saveBrancheUser
        * Invoice immutability enforcement (write/unlink/button_draft blocked
          once ZRA-synced) and reprint COPY watermark tracking
        * Enhanced fiscal tax invoice report (supplier/customer TPIN, per-line
          tax rate, discount, invoice type) and a ZRA Sales Register report
        * Local persistence of classification/standard codes instead of
          Excel-export-only

        Carried forward from v1.4.0 (DigiTax Parity Release):

        PARITY #1 — Multi-tax breakdown
        * VAT (A/B/C1/C2/C3/D/E/F/RVAT), IPL1/IPL2, Tourism Levy (TL),
          Excise (ECM/EXEEG), Turnover Tax (TOT) — see v1.5.0 above for the
          category-model correction
        * Each tax type populates its own taxblAmt / taxAmt / taxRt bucket
          in the ZRA payload — no longer everything shoved into bucket A
        * Secondary levy field on products (e.g. standard VAT + Tourism Levy)

        PARITY #2 — Import Declarations
        * New model: zra.import.declaration (header + lines)
        * Fetch wizard: pulls declarations from ZRA by date range
          via imports/selectImportItems endpoint
        * Auto-matches declaration lines to Odoo products by ZRA item code
        * "Create Stock Receipt" action generates a stock.picking with
          movement code 01 (Import) ready for validation
        * Full audit trail: raw JSON response stored on each declaration

        PARITY #3 — Product origin & packaging enforcement
        * zra_origin_country_id: country of origin (required before ZRA registration)
        * zra_pkg_unit_code: packaging unit selection (17 ZRA codes, required)
        * zra_qty_unit_code: quantity unit selection (15 ZRA codes, required)
        * register_item() now sends real orgnNatCd, pkgUnitCd, qtyUnitCd
          instead of hardcoded ZM / NT / U
        * Validation blocks ZRA registration if any of the three are missing

        Carried forward from v1.3.0:
        * Stock sync re-enabled (12 movement codes per spec §6.14 + standalone wizard)
        * Payment type field on invoices (codes 01-08)
        * Credit note refund reason codes (7 ZRA codes, required)
        * Debit note support with reason codes 01-04
        * CIS duplication prevention
        * Supplier TPIN blocking error at post time
        * POS sync always uses sudo()
    """,
    'author': 'Esau Ngoma',
    'maintainer': 'Esau Ngoma',
    'support': 'esaungoma004@gmail.com',
    'website': 'mailto:esaungoma004@gmail.com',
    'license': 'LGPL-3',
    'depends': [
        'base',
        'account',
        'sale',
        'point_of_sale',
        'stock',
        'product',
        'mrp',
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/zra_security.xml',
        'data/zra_data.xml',
        'data/zra_report_binding_data.xml',
        'data/zra_backup_cron.xml',
        'views/zra_config_views.xml',
        'views/zra_backup_log_views.xml',
        'views/zra_log_views.xml',
        'views/account_move_views.xml',
        'views/pos_order_views.xml',
        'views/product_template_views.xml',
        'views/product_product_views.xml',
        'views/res_partner_views.xml',
        'views/res_users_views.xml',
        'views/zra_stock_adjustment_views.xml',
        'views/zra_import_declaration_views.xml',
        'views/zra_purchase_fetch_views.xml',
        'views/zra_classification_code_views.xml',
        'views/zra_sales_register_views.xml',
        'views/zra_purchase_register_views.xml',
        'views/mrp_bom_views.xml',
        'views/menu_views.xml',
        'report/invoice_report_zra.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'zra_smart_invoice_new/static/src/js/receipt_patch.js',
            'zra_smart_invoice_new/static/src/js/disable_invoice_autodownload.js',
            'zra_smart_invoice_new/static/src/js/disable_payment_screen_autodownload.js',
            'zra_smart_invoice_new/static/src/js/print_zra_data_patch.js',
            'zra_smart_invoice_new/static/src/xml/simple_receipt.xml',
        ],
    },
    'images': ['static/description/icon.png'],
    'installable': True,
    'application': True,
    'auto_install': False,
    'external_dependencies': {
        'python': ['requests', 'qrcode', 'openpyxl'],
    },
}
