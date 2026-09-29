# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

import logging
import json

_logger = logging.getLogger(__name__)


class ZRAConfig(models.Model):
    _name = 'zra.config'
    _description = 'ZRA Smart Invoice Configuration'
    _inherit = ['mail.thread']
    _rec_name = 'company_id'

    company_id = fields.Many2one('res.company', string='Company', required=True,
                                 default=lambda self: self.env.company, tracking=True)

    # VSDC Connection
    vsdc_url = fields.Char(string='VSDC URL', required=True,
                           default='http://localhost:8080/zravsdc', tracking=True,
                           help='Base URL where VSDC is deployed (e.g., http://localhost:8080/zravsdc)')

    # ZRA Credentials
    tpin = fields.Char(string='TPIN', required=True, size=10, tracking=True,
                       help='Taxpayer Identification Number (10 digits)')
    branch_id = fields.Char(string='Branch ID', required=True, size=3, default='000', tracking=True,
                            help='Branch identifier from ZRA (e.g., 000 for HQ)')
    device_serial = fields.Char(string='Device Serial Number', size=20, tracking=True,
                                help='Device serial number from ZRA after VSDC approval')

    # Environment
    environment = fields.Selection([
        ('sandbox', 'Sandbox (Testing)'),
        ('production', 'Production')
    ], string='Environment', default='sandbox', required=True)

    # Initialization Status
    is_initialized = fields.Boolean(string='Device Initialized', default=False, readonly=True)
    initialization_date = fields.Datetime(string='Initialization Date', readonly=True)
    last_sync_date = fields.Datetime(string='Last Sync Date', readonly=True)

    # API Settings
    timeout = fields.Integer(string='API Timeout (seconds)', default=30)
    auto_sync_invoices = fields.Boolean(string='Auto Sync Invoices', default=True,
                                        help='Automatically send invoices to ZRA when validated')
    auto_sync_pos = fields.Boolean(string='Auto Sync POS Orders', default=True,
                                   help='Automatically send POS orders to ZRA')
    auto_sync_purchases = fields.Boolean(string='Auto Sync Purchases', default=False,
                                         help='Automatically send vendor bills to ZRA')
    auto_sync_stock = fields.Boolean(string='Auto Sync Stock', default=True,
                                     help='Automatically sync stock movements to ZRA')

    # Statistics
    total_invoices_synced = fields.Integer(string='Total Invoices Synced',
                                           compute='_compute_stats', store=False)
    total_pos_synced = fields.Integer(string='Total POS Orders Synced',
                                      compute='_compute_stats', store=False)
    failed_sync_count = fields.Integer(string='Failed Syncs',
                                       compute='_compute_stats', store=False)

    # Active
    active = fields.Boolean(default=True)

    # ── Branch Information (checklist #7 — MANDATORY) ─────────────────────────
    branch_info_fetched_date = fields.Datetime(
        string='Branch Info Last Fetched', readonly=True,
        help='Last time branches/selectBranches was called successfully.'
    )
    branch_name = fields.Char(string='Branch Name (ZRA)', readonly=True)
    branch_status_code = fields.Char(string='Branch Status Code', readonly=True)
    branch_province = fields.Char(string='Province', readonly=True)
    branch_district = fields.Char(string='District', readonly=True)
    branch_sector = fields.Char(string='Sector', readonly=True)
    branch_manager_name = fields.Char(string='Manager Name', readonly=True)
    branch_manager_tel = fields.Char(string='Manager Contact', readonly=True)
    branch_manager_email = fields.Char(string='Manager Email', readonly=True)
    branch_is_hq = fields.Boolean(string='Is Head Office', readonly=True)

    _sql_constraints = [
        ('company_unique', 'unique(company_id)', 'Only one ZRA configuration per company is allowed!')
    ]

    @api.constrains('tpin')
    def _check_tpin(self):
        for record in self:
            if record.tpin and (len(record.tpin) != 10 or not record.tpin.isdigit()):
                raise ValidationError(_('TPIN must be exactly 10 digits'))

    @api.constrains('branch_id')
    def _check_branch_id(self):
        for record in self:
            if record.branch_id and len(record.branch_id) != 3:
                raise ValidationError(_('Branch ID must be exactly 3 characters'))

    @api.constrains('environment', 'vsdc_url')
    def _check_https_in_production(self):
        # Nothing anywhere else in the module enforces or even warns about
        # transport security — vsdc_url defaults to plain http:// and there
        # was no check preventing 'production' + http:// together. TPIN,
        # branch ID and (once initialized) every fiscal invoice payload
        # would then travel over plain HTTP whenever the VSDC isn't on the
        # same host as Odoo. Sandbox stays unrestricted since a local
        # Docker VSDC (this project's own dev setup included) legitimately
        # has no cert.
        for record in self:
            if record.environment == 'production' and record.vsdc_url \
                    and not record.vsdc_url.lower().startswith('https://'):
                raise ValidationError(_(
                    'The VSDC URL must use HTTPS when Environment is set to '
                    'Production ("%s" is not secure). TPIN, credentials and '
                    'fiscal invoice data must not travel over plain HTTP '
                    'once this is a live production device.'
                ) % record.vsdc_url)

    def _check_zra_manager_access(self):
        """Explicit permission gate for every action that talks to the live
        VSDC and/or writes via .sudo() (classification/standard-code sync
        persists rows via Code.sudo(), and the classification-code export
        creates an ir.attachment via .sudo()).

        Why this can't just be "the view button is hidden from non-managers
        with groups=...": nothing in views/zra_config_views.xml has ever
        had a groups= restriction, so any ordinary invoicing user with only
        perm_read on zra.config (security/ir.model.access.csv) can already
        see and click every one of these buttons — and even if the view
        were fixed, calling the method directly (RPC, `odoo shell`) skips
        view-level restrictions entirely. And unlike a plain self.write()
        call, which the ACL's perm_write=0 already blocks for that role,
        several of these actions fire the outbound HTTP request to the VSDC
        and persist data via .sudo() BEFORE they ever reach a write() call
        on `self` — so relying on the ACL alone lets an unprivileged user
        trigger real VSDC traffic and create real classification/standard
        code + attachment records, and only then hit an AccessError on the
        final bookkeeping write. This must be the first line of any action
        that shouldn't be triggerable at all by a non-manager.
        """
        if not self.env.user.has_group('account.group_account_manager'):
            raise UserError(_(
                'Only Accounting/Billing Administrators can perform this '
                'ZRA action.'
            ))

    def _compute_stats(self):
        for record in self:
            # Count synced invoices
            invoices = self.env['account.move'].search_count([
                ('company_id', '=', record.company_id.id),
                ('zra_receipt_number', '!=', False)
            ])
            record.total_invoices_synced = invoices

            # Count synced POS orders
            try:
                pos_orders = self.env['pos.order'].search_count([
                    ('company_id', '=', record.company_id.id),
                    ('zra_receipt_number', '!=', False)
                ])
                record.total_pos_synced = pos_orders
            except Exception:
                record.total_pos_synced = 0

            # Count failed syncs
            failed_logs = self.env['zra.api.log'].search_count([
                ('company_id', '=', record.company_id.id),
                ('status', '=', 'failed')
            ])
            record.failed_sync_count = failed_logs

    def action_force_sandbox_init(self):
        """Force sandbox initialization (for testing only)"""
        self.ensure_one()
        self._check_zra_manager_access()

        if self.environment != 'sandbox':
            raise ValidationError(_('This action only works in Sandbox mode!'))

        self.write({
            'is_initialized': True,
            'initialization_date': fields.Datetime.now()
        })

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Sandbox Mode'),
                'message': _('Device force-initialized for sandbox testing. This bypasses ZRA validation.'),
                'type': 'warning',
                'sticky': True,
            }
        }

    def action_initialize_device(self):
        """Initialize device with ZRA"""
        self.ensure_one()
        self._check_zra_manager_access()
        if not self.device_serial:
            raise ValidationError(_('Device Serial Number is required for initialization'))

        try:
            api_client = self.env['zra.api.client']
            result = api_client.initialize_device(self)
            result_cd = result.get('resultCd')

            # '000' = freshly initialized; '902' = this device serial is
            # already registered against this tpin/bhfId on ZRA's side.
            # Both are a successfully-initialized device from Odoo's point
            # of view — only a real error code should block the flow.
            if result_cd in ('000', '902'):
                self.write({
                    'is_initialized': True,
                    'initialization_date': fields.Datetime.now()
                })
                message = _('Device initialized successfully!') if result_cd == '000' \
                    else _('Device was already registered with ZRA — marked as initialized.')
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': message,
                        'type': 'success',
                        'sticky': False,
                    }
                }
            else:
                raise ValidationError(_('Initialization failed: %s') % result.get('resultMsg', 'Unknown error'))
        except Exception as e:
            raise ValidationError(_('Initialization error: %s') % str(e))

    def action_test_connection(self):
        """Test VSDC connection"""
        self.ensure_one()
        self._check_zra_manager_access()
        try:
            api_client = self.env['zra.api.client']
            result = api_client.test_connection(self)

            message = _('Connection successful!') if result else _('Connection failed!')
            msg_type = 'success' if result else 'danger'

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Test'),
                    'message': message,
                    'type': msg_type,
                    'sticky': False,
                }
            }
        except Exception as e:
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Connection Failed'),
                    'message': str(e),
                    'type': 'danger',
                    'sticky': False,
                }
            }

    def action_sync_classification_codes(self):
        """Sync UNSPSC classification codes from ZRA and download as Excel"""
        self.ensure_one()
        self._check_zra_manager_access()
        try:
            api_client = self.env['zra.api.client']
            result = api_client.get_classification_codes(self)

            # '001' = "There is no search result" per spec §6.13 — a
            # legitimate empty response, not an error. Only a code outside
            # {000, 001} is a genuine failure.
            if result.get('resultCd') not in ('000', '001'):
                raise ValidationError(_('Sync failed: %s') % result.get('resultMsg', 'Unknown error'))

            data = result.get('data') or {}
            # Confirmed response key per spec p.13 sample: data.itemClsList.
            # Fall back to a generic search for resilience against variants.
            rows = data.get('itemClsList')
            if not rows:
                for k, v in data.items():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        rows = v
                        break

            if not rows:
                # Nothing to export — still show success notification
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Success'),
                        'message': _('Classification codes synced, but no list was returned to export.'),
                        'type': 'success',
                        'sticky': False,
                    }
                }

            # checklist #3: persist locally, not just export to Excel.
            self._save_classification_codes(rows)

            # Build Excel in-memory (as attachment) and return download URL
            import base64
            from io import BytesIO
            from openpyxl import Workbook
            from openpyxl.utils import get_column_letter

            wb = Workbook()
            ws = wb.active
            ws.title = 'Classification Codes'

            # Collect all keys as columns (stable order: common keys first, then others)
            common_order = [
                'itemClsCd', 'itemClsNm', 'itemClsLvl', 'itemClsNo', 'useYn', 'regrNm', 'regrId', 'modrNm', 'modrId'
            ]
            keys = []
            for k in common_order:
                if any(k in r for r in rows):
                    keys.append(k)
            for r in rows:
                for k in r.keys():
                    if k not in keys:
                        keys.append(k)

            # Header row
            ws.append(keys)

            # Data rows
            for r in rows:
                ws.append([r.get(k, '') for k in keys])

            # Auto width (basic)
            for col_idx, col_name in enumerate(keys, start=1):
                max_len = len(str(col_name))
                for cell in ws[get_column_letter(col_idx)]:
                    if cell.value is not None:
                        max_len = max(max_len, len(str(cell.value)))
                ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 60)

            stream = BytesIO()
            wb.save(stream)
            stream.seek(0)

            filename = 'zra_classification_codes_%s.xlsx' % fields.Date.today()

            attachment = self.env['ir.attachment'].sudo().create({
                'name': filename,
                'type': 'binary',
                'datas': base64.b64encode(stream.read()),
                'res_model': self._name,
                'res_id': self.id,
                'mimetype': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            })

            self.last_sync_date = fields.Datetime.now()

            return {
                'type': 'ir.actions.act_url',
                'url': '/web/content/%s?download=true' % attachment.id,
                'target': 'self',
            }

        except Exception as e:
            raise ValidationError(_('Sync failed: %s') % str(e))

    def _save_classification_codes(self, rows):
        """Upsert UNSPSC classification codes into zra.classification.code,
        matched on item_cls_cd (checklist #3)."""
        Code = self.env['zra.classification.code'].sudo()
        existing = {c.item_cls_cd: c for c in Code.search([])}
        created, updated = 0, 0

        for row in rows:
            item_cls_cd = row.get('itemClsCd')
            if not item_cls_cd:
                continue
            vals = {
                'item_cls_cd': item_cls_cd,
                'item_cls_nm': row.get('itemClsNm') or item_cls_cd,
                'item_cls_lvl': row.get('itemClsLvl') or 0,
                'tax_ty_cd': row.get('taxTyCd') or '',
                'use_yn': row.get('useYn') or 'Y',
            }
            record = existing.get(item_cls_cd)
            if record:
                record.write(vals)
                updated += 1
            else:
                Code.create(vals)
                created += 1

        _logger.info(
            f"ZRA classification codes persisted: {created} created, {updated} updated"
        )
        return created, updated

    def action_sync_standard_codes(self):
        """Sync standard codes (VSDC constants) from ZRA and persist them
        locally (code/selectCodes, checklist item #2 — MANDATORY).

        Response shape is nested: data.clsList = [{cdCls, cdClsNm,
        dtlList: [{cd, cdNm}, ...]}, ...] — flattened into zra.standard.code
        rows keyed by (cd_cls, cd).
        """
        self.ensure_one()
        self._check_zra_manager_access()
        try:
            api_client = self.env['zra.api.client']
            result = api_client.get_standard_codes(self)

            # '001' = "There is no search result" (spec §6.13) — legitimate
            # empty response, not an error.
            if result.get('resultCd') not in ('000', '001'):
                raise ValidationError(_('Sync failed: %s') % result.get('resultMsg', 'Unknown error'))

            data = result.get('data') or {}
            cls_list = data.get('clsList', [])

            Code = self.env['zra.standard.code'].sudo()
            existing = {(c.cd_cls, c.cd): c for c in Code.search([])}
            created, updated = 0, 0

            for cls in cls_list:
                cd_cls = cls.get('cdCls')
                cd_cls_nm = cls.get('cdClsNm')
                for detail in cls.get('dtlList', []):
                    cd = detail.get('cd')
                    if not cd_cls or not cd:
                        continue
                    vals = {
                        'cd_cls': cd_cls,
                        'cd_cls_nm': cd_cls_nm or '',
                        'cd': cd,
                        'cd_nm': detail.get('cdNm') or '',
                    }
                    key = (cd_cls, cd)
                    record = existing.get(key)
                    if record:
                        record.write(vals)
                        updated += 1
                    else:
                        Code.create(vals)
                        created += 1

            self.last_sync_date = fields.Datetime.now()

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Standard codes synced: %d created, %d updated.') % (created, updated),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(_('Sync failed: %s') % str(e))

    def action_sync_items_from_zra(self):
        """Get Item List (items/selectItems, checklist item #10).

        Reconciles ZRA's item register against local products by matching
        itemCd, marking any local product ZRA already has on file as
        registered (useful after a device swap or DB restore where local
        zra_registered flags may be stale).
        """
        self.ensure_one()
        self._check_zra_manager_access()
        try:
            api_client = self.env['zra.api.client']
            result = api_client.get_item_list(self)

            # '001' = "There is no search result" (spec §6.13) — legitimate
            # empty response, not an error.
            if result.get('resultCd') not in ('000', '001'):
                raise ValidationError(_('Sync failed: %s') % result.get('resultMsg', 'Unknown error'))

            data = result.get('data') or {}
            items = data.get('itemList', [])

            Product = self.env['product.template']
            matched, unmatched = 0, 0
            for item in items:
                item_cd = item.get('itemCd')
                if not item_cd:
                    continue
                product = Product.search([('zra_item_code', '=', item_cd)], limit=1)
                if product:
                    if not product.zra_registered:
                        product.write({
                            'zra_registered': True,
                            'zra_registration_date': fields.Datetime.now(),
                        })
                    matched += 1
                else:
                    unmatched += 1

            self.last_sync_date = fields.Datetime.now()

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Items Synced from ZRA'),
                    'message': _('ZRA has %d item(s) on file. Matched to local products: %d | Not found locally: %d')
                                % (len(items), matched, unmatched),
                    'type': 'success' if unmatched == 0 else 'warning',
                    'sticky': False,
                }
            }
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(_('Sync failed: %s') % str(e))

    def action_sync_stock_from_zra(self):
        """Get Stock Item List (stock/selectStockItems, checklist item #28).

        Read-only reconciliation: reports any mismatch between ZRA's
        recorded remaining quantity (rsdQty) and Odoo's on-hand quantity
        for products ZRA has stock records for, without changing Odoo
        stock levels (Odoo remains the source of truth for physical stock;
        ZRA is kept in sync via save_stock_master on each movement).

        Note: the official spec's "Select Stock Items Response" section
        (v1.0.7, p.109) is malformed — it duplicates the Update Import
        Items table/sample instead of a real stock response, so the
        response's top-level list key isn't confirmed. Both plausible
        keys ('itemList', matching other select* endpoints, and
        'stockItemList', matching saveStockMaster's request) are checked.
        """
        self.ensure_one()
        self._check_zra_manager_access()
        try:
            api_client = self.env['zra.api.client']
            result = api_client.get_stock_items(self)

            # '001' = "There is no search result" (spec §6.13) — legitimate
            # empty response, not an error.
            if result.get('resultCd') not in ('000', '001'):
                raise ValidationError(_('Sync failed: %s') % result.get('resultMsg', 'Unknown error'))

            data = result.get('data') or {}
            items = data.get('itemList', []) or data.get('stockItemList', [])

            Product = self.env['product.template']
            mismatches = []
            checked = 0
            for item in items:
                item_cd = item.get('itemCd')
                zra_qty = item.get('qty')
                if not item_cd or zra_qty is None:
                    continue
                product = Product.search([('zra_item_code', '=', item_cd)], limit=1)
                if not product:
                    continue
                checked += 1
                odoo_qty = product.with_company(self.company_id).qty_available
                if abs(float(zra_qty) - odoo_qty) > 0.01:
                    mismatches.append(
                        '%s: ZRA=%.2f, Odoo=%.2f' % (product.name, float(zra_qty), odoo_qty)
                    )

            self.last_sync_date = fields.Datetime.now()

            message = _('Checked %d matched product(s).') % checked
            if mismatches:
                message += '\n' + _('Mismatches found:') + '\n' + '\n'.join(mismatches[:10])

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Stock Synced from ZRA'),
                    'message': message,
                    'type': 'warning' if mismatches else 'success',
                    'sticky': bool(mismatches),
                }
            }
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(_('Sync failed: %s') % str(e))

    def action_fetch_branch_info(self):
        """Get Branch Info (branches/selectBranches, checklist item #7 —
        MANDATORY). Fetches and stores this taxpayer's registered branch
        details from Smart Invoice, matched to this config's branch_id."""
        self.ensure_one()
        self._check_zra_manager_access()
        try:
            api_client = self.env['zra.api.client']
            result = api_client.get_branch_info(self)

            # '001' = "There is no search result" (spec §6.13) — legitimate
            # empty response, not an error; falls through to the "No Branch
            # Found" notification below.
            if result.get('resultCd') not in ('000', '001'):
                raise ValidationError(_('Fetch failed: %s') % result.get('resultMsg', 'Unknown error'))

            data = result.get('data') or {}
            branches = data.get('bhfList', [])
            branch = next(
                (b for b in branches if b.get('bhfId') == self.branch_id),
                branches[0] if branches else None
            )

            if not branch:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('No Branch Found'),
                        'message': _('ZRA returned no branch records for this TPIN.'),
                        'type': 'warning',
                        'sticky': False,
                    }
                }

            self.write({
                'branch_info_fetched_date': fields.Datetime.now(),
                'branch_name': branch.get('bhfNm') or '',
                'branch_status_code': branch.get('bhfSttsCd') or '',
                'branch_province': branch.get('prvncNm') or '',
                'branch_district': branch.get('dstrtNm') or '',
                'branch_sector': branch.get('sctrNm') or '',
                'branch_manager_name': branch.get('mgrNm') or '',
                'branch_manager_tel': branch.get('mgrTelNo') or '',
                'branch_manager_email': branch.get('mgrEmail') or '',
                'branch_is_hq': (branch.get('hqYn') == 'Y'),
            })

            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Branch Info Fetched'),
                    'message': _('Branch: %s (%s)') % (branch.get('bhfNm') or '', self.branch_id),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(_('Fetch failed: %s') % str(e))

    def action_view_logs(self):
        """View API logs"""
        self.ensure_one()
        return {
            'name': _('ZRA API Logs'),
            'type': 'ir.actions.act_window',
            'res_model': 'zra.api.log',
            'view_mode': 'list,form',
            'domain': [('company_id', '=', self.company_id.id)],
            'context': {'default_company_id': self.company_id.id}
        }

    @api.model
    def get_active_config(self, company_id=None):
        """Get active ZRA configuration for company"""
        if not company_id:
            company_id = self.env.company.id
        config = self.search([('company_id', '=', company_id), ('active', '=', True)], limit=1)
        if not config:
            raise ValidationError(_('ZRA configuration not found. Please configure ZRA settings first.'))
        return config