# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError

import logging
import qrcode
import base64
from io import BytesIO

_logger = logging.getLogger(__name__)

# FIX #8: ZRA Payment type codes — per VSDC spec §6.9 "Payment Method".
# NOTE: codes 03-06 were previously mislabeled here (e.g. '03' was shown as
# "Bank cheque" when the spec defines it as "Cash/Credit") — the *code* sent
# to ZRA was schema-valid but reported the wrong payment method whenever a
# user picked based on the (wrong) label. Corrected to match the spec exactly,
# including code 08 which was missing entirely.
ZRA_PAYMENT_TYPES = [
    ('01', '01 - Cash'),
    ('02', '02 - Credit'),
    ('03', '03 - Cash/Credit'),
    ('04', '04 - Bank Check'),
    ('05', '05 - Debit & Credit Card'),
    ('06', '06 - Mobile Money'),
    ('07', '07 - Other'),
    ('08', '08 - Bank Transfer'),
]

# FIX #10: ZRA Credit note reason codes
ZRA_REFUND_REASONS = [
    ('01', '01 - Cancelled invoice'),
    ('02', '02 - Returned goods'),
    ('03', '03 - Price adjustment'),
    ('04', '04 - Faulty/damaged goods'),
    ('05', '05 - Discount applied post-invoice'),
    ('06', '06 - Partial delivery'),
    ('07', '07 - Other'),
]

# FIX #9: ZRA Debit note reason codes
ZRA_DEBIT_REASONS = [
    ('01', '01 - Price increase'),
    ('02', '02 - Additional charges'),
    ('03', '03 - Omitted items'),
    ('04', '04 - Other'),
]


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ── ZRA receipt fields ──────────────────────────────────────────────────
    zra_receipt_number = fields.Char(string='ZRA Receipt Number', readonly=True, copy=False,
                                     help='Receipt number from ZRA (rcptNo).')
    zra_internal_data = fields.Char(string='ZRA Internal Data', readonly=True, copy=False)
    zra_signature = fields.Char(string='ZRA Receipt Signature', readonly=True, copy=False)
    zra_sdc_id = fields.Char(string='SDC ID', readonly=True, copy=False)
    zra_mrc_no = fields.Char(string='MRC Number', readonly=True, copy=False)
    zra_invoice_number = fields.Char(string='ZRA Invoice Number', readonly=True, copy=False)
    zra_vsdc_receipt_date = fields.Char(string='VSDC Receipt Date', readonly=True, copy=False)
    zra_total_tax_amount = fields.Float(string='ZRA Total Tax', readonly=True, copy=False)
    zra_qr_code = fields.Char(string='ZRA QR Code Data', readonly=True, copy=False)
    zra_qr_code_image = fields.Binary(
        string='ZRA QR Code Image', readonly=True, copy=False,
        compute='_compute_qr_code_image', store=True,
    )
    zra_sync_status = fields.Selection([
        ('pending', 'Pending'),
        ('synced',  'Synced'),
        ('failed',  'Failed'),
    ], string='ZRA Status', default='pending', copy=False, index=True)
    zra_sync_date = fields.Datetime(string='ZRA Sync Date', readonly=True, copy=False)
    zra_error_message = fields.Text(string='ZRA Error Message', readonly=True, copy=False)

    # FIX #8: Payment type — no longer hardcoded
    zra_payment_type = fields.Selection(
        ZRA_PAYMENT_TYPES, string='ZRA Payment Type', default='01',
        help='Payment method reported to ZRA. Defaults to Cash; change before syncing.'
    )

    # FIX #10: Credit note reason code
    zra_refund_reason = fields.Selection(
        ZRA_REFUND_REASONS, string='ZRA Refund Reason',
        help='Required by ZRA for all credit notes.'
    )

    # FIX #9: Debit note reason code + link to original invoice
    zra_debit_reason = fields.Selection(
        ZRA_DEBIT_REASONS, string='ZRA Debit Note Reason',
        help='Required by ZRA for all debit notes.'
    )
    zra_original_invoice_id = fields.Many2one(
        'account.move', string='Original Invoice (ZRA)',
        help='Original invoice this credit/debit note relates to.',
        copy=False
    )

    # ── Purchase Information (checklist #13-15, spec §5.9/§6.11) ──────────────
    zra_purchase_reg_type = fields.Selection([
        ('M', 'M - Manual (entered directly in the CIS)'),
        ('A', 'A - Automatic (fetched from ZRA Get Purchases)'),
    ], string='ZRA Registration Type', default='M', copy=False,
       help='regTyCd per VSDC spec §6.11. "Automatic" is set when this bill '
            'was created from ZRA\'s Get Purchases endpoint (the supplier is '
            'a Smart Invoice user themselves); "Manual" for bills entered '
            'directly, e.g. from a supplier not registered on Smart Invoice '
            '(checklist #15).')
    zra_supplier_invoice_no = fields.Char(
        string='Supplier CIS Invoice No.', copy=False,
        help='The supplier\'s own invoice number (spplrInvcNo) as recorded '
             'on their Smart Invoice / CIS. Used to de-duplicate purchases '
             'fetched via Get Purchases.'
    )

    # ── Reprint tracking (checklist #24 — reprints must show 'copy'/'duplicate') ──
    zra_print_count = fields.Integer(
        string='ZRA Print Count', default=0, copy=False, readonly=True,
        help='Number of times the ZRA fiscal invoice report has been printed. '
             'The report shows a COPY watermark from the second print onward.'
    )

    # ── Fields for the fiscal tax invoice report (checklist #19) ─────────────
    zra_supplier_tpin = fields.Char(
        string='Supplier TPIN (ZRA)', compute='_compute_zra_report_fields'
    )
    zra_discount_rate = fields.Float(
        string='Discount Rate (%)', compute='_compute_zra_report_fields',
        help='Effective discount rate over the whole invoice '
             '(total discount / gross-before-discount x 100), shown on the '
             'fiscal invoice totals block (checklist #19(viii)(b)).'
    )
    zra_total_discount = fields.Monetary(
        string='Total Discount', compute='_compute_zra_report_fields',
        currency_field='currency_id'
    )
    zra_invoice_type_label = fields.Char(
        string='ZRA Invoice Type', compute='_compute_zra_report_fields'
    )

    # ── QR code compute ─────────────────────────────────────────────────────
    @api.depends('zra_qr_code', 'zra_receipt_number', 'zra_internal_data',
                 'zra_signature', 'zra_sdc_id')
    def _compute_qr_code_image(self):
        for move in self:
            if move.zra_receipt_number and move.zra_sync_status == 'synced':
                try:
                    qr_data = move.zra_qr_code
                    if not qr_data:
                        parts = []
                        if move.zra_invoice_number:
                            parts.append(f"INV:{move.zra_invoice_number}")
                        if move.zra_receipt_number:
                            parts.append(f"RCPT:{move.zra_receipt_number}")
                        if move.zra_sdc_id:
                            parts.append(f"SDC:{move.zra_sdc_id}")
                        if move.zra_internal_data:
                            parts.append(f"DATA:{move.zra_internal_data}")
                        if move.zra_signature:
                            parts.append(f"SIGN:{move.zra_signature}")
                        if move.amount_total:
                            parts.append(f"AMT:{move.amount_total:.2f}")
                        qr_data = "|".join(parts)

                    if qr_data:
                        qr = qrcode.QRCode(
                            version=1,
                            error_correction=qrcode.constants.ERROR_CORRECT_M,
                            box_size=10, border=4,
                        )
                        qr.add_data(qr_data)
                        qr.make(fit=True)
                        img = qr.make_image(fill_color="black", back_color="white")
                        buf = BytesIO()
                        img.save(buf, format='PNG')
                        move.zra_qr_code_image = base64.b64encode(buf.getvalue())
                    else:
                        move.zra_qr_code_image = False
                except Exception as e:
                    _logger.error(f"QR code error for {move.name}: {str(e)}")
                    move.zra_qr_code_image = False
            else:
                move.zra_qr_code_image = False

    # ── Fiscal tax invoice report fields (checklist #19) ──────────────────────
    @api.depends('company_id', 'partner_id', 'move_type', 'zra_debit_reason',
                 'invoice_line_ids.discount', 'invoice_line_ids.price_unit',
                 'invoice_line_ids.quantity')
    def _compute_zra_report_fields(self):
        configs = {
            c.company_id.id: c for c in
            self.env['zra.config'].search([('company_id', 'in', self.company_id.ids)])
        }
        for move in self:
            config = configs.get(move.company_id.id)
            move.zra_supplier_tpin = config.tpin if config else ''

            total_discount = 0.0
            gross_before_discount = 0.0
            for line in move.invoice_line_ids:
                if line.display_type in ('line_section', 'line_note'):
                    continue
                line_gross = line.price_unit * line.quantity
                gross_before_discount += line_gross
                if line.discount:
                    total_discount += line_gross * (line.discount / 100.0)
            move.zra_total_discount = move.currency_id.round(total_discount)
            move.zra_discount_rate = (
                (total_discount / gross_before_discount * 100.0)
                if gross_before_discount else 0.0
            )

            if move.move_type == 'out_refund':
                move.zra_invoice_type_label = _('Credit Note')
            elif move.zra_debit_reason:
                move.zra_invoice_type_label = _('Debit Note')
            else:
                move.zra_invoice_type_label = _('Normal Sale')

    # ── Validation helpers ───────────────────────────────────────────────────
    def _zra_validate_before_sync(self):
        """Common pre-sync checks. Raises UserError if validation fails."""
        self.ensure_one()

        # FIX #2: Check all line products have classification codes
        for line in self.invoice_line_ids:
            if line.display_type in ('line_section', 'line_note'):
                continue
            if line.product_id and not line.product_id.zra_classification_code:
                raise UserError(
                    _('Product "%s" is missing a UNSPSC classification code. '
                      'Please set it in the product ZRA Information tab before syncing.')
                    % line.product_id.name
                )

        # FIX #10: Credit note must have a refund reason
        if self.move_type == 'out_refund' and not self.zra_refund_reason:
            raise UserError(
                _('ZRA requires a Refund Reason on all credit notes. '
                  'Please select one in the ZRA Details tab.')
            )

        # FIX #9: Debit note must have a reason
        if self.move_type == 'out_invoice' and self.zra_debit_reason and \
                not self.zra_original_invoice_id:
            raise UserError(
                _('A debit note must be linked to the original invoice. '
                  'Please set "Original Invoice (ZRA)" in the ZRA Details tab.')
            )

    # ── Immutability (checklist #18, #22, #23) ────────────────────────────────
    # Fields that change the fiscal substance of an already-fiscalized
    # invoice. zra_* bookkeeping fields (sync status, print count, QR image,
    # etc.) are deliberately excluded — those are written by this module's
    # own sync/print/regenerate flows after zra_sync_status is already 'synced'.
    _ZRA_PROTECTED_FIELDS = {
        'invoice_line_ids', 'partner_id', 'invoice_date', 'currency_id',
        'narration', 'ref', 'journal_id',
        # 'name' is the invoice number ZRA received as cisInvcNo and that's
        # printed on the fiscal PDF — it must be included here even though
        # it "just" comes from ir.sequence, because Odoo's own core
        # Invoices > Action > Resequence wizard (account.resequence.wizard)
        # writes to it via a plain `move.name = ...` assignment, which is a
        # normal write() call like any other. Without 'name' protected here,
        # that stock wizard can silently renumber an already ZRA-submitted
        # invoice — no exception, no re-sync, and now a mismatch between
        # what ZRA has on file and what's printed on the invoice.
        'name',
    }

    def write(self, vals):
        # SECURITY: no context-key bypass — an RPC client could smuggle one in
        # and defeat fiscal immutability (checklist #14/#19). The guard is
        # unconditional; the module's own sync flows only write zra_*
        # bookkeeping fields, which are deliberately not protected.
        touched = self._ZRA_PROTECTED_FIELDS & set(vals.keys())
        if touched:
            for move in self:
                if move.zra_sync_status == 'synced':
                    raise UserError(_(
                        'Invoice "%s" has already been submitted to ZRA '
                        '(Receipt No: %s) and its fiscal details cannot be '
                        'modified. ZRA requires fiscalized invoices to be '
                        'immutable — issue a credit note or debit note instead.'
                    ) % (move.name, move.zra_receipt_number or ''))
        return super().write(vals)

    def unlink(self):
        for move in self:
            if move.zra_sync_status == 'synced':
                raise UserError(_(
                    'Invoice "%s" has already been submitted to ZRA '
                    '(Receipt No: %s) and cannot be deleted. ZRA requires '
                    'fiscalized invoices to be retained permanently.'
                ) % (move.name, move.zra_receipt_number or ''))
        return super().unlink()

    def button_draft(self):
        for move in self:
            if move.zra_sync_status == 'synced':
                raise UserError(_(
                    'Invoice "%s" has already been submitted to ZRA '
                    '(Receipt No: %s) and cannot be reset to draft. ZRA '
                    'requires fiscalized invoices to be immutable — issue a '
                    'credit note or debit note instead.'
                ) % (move.name, move.zra_receipt_number or ''))
        return super().button_draft()

    def copy(self, default=None):
        """Internal checklist #20: a fiscalized (ZRA-submitted) invoice must
        never be duplicated — disable the Duplicate action for it."""
        for move in self:
            if move.zra_sync_status == 'synced':
                raise UserError(_(
                    'Invoice "%s" has been submitted to ZRA (Receipt No: %s) '
                    'and cannot be duplicated. ZRA requires fiscalized '
                    'invoices to be unique — create a new invoice instead.'
                ) % (move.name, move.zra_receipt_number or ''))
        return super().copy(default)

    @api.onchange('partner_id')
    def _onchange_partner_id_zra_supplier_warning(self):
        """Internal checklist #13: warn when recording a purchase from a
        supplier that is not registered on the Smart Invoice system."""
        if self.move_type in ('in_invoice', 'in_refund') and self.partner_id:
            partner = self.partner_id
            if partner.zra_si_registration == 'not_registered':
                return {'warning': {
                    'title': _('Supplier Not Registered on Smart Invoice'),
                    'message': _(
                        'Supplier "%s" (TPIN %s) is NOT registered on the ZRA '
                        'Smart Invoice system (verified %s). Ask them to '
                        'register with ZRA Smart Invoice. You may still record '
                        'this purchase with ZRA Registration Type "Manual".')
                    % (partner.name, partner.zra_tpin or '',
                       partner.zra_si_check_date or ''),
                }}
            if not partner.zra_tpin:
                return {'warning': {
                    'title': _('Supplier Has No TPIN'),
                    'message': _(
                        'Supplier "%s" has no TPIN set, so their Smart Invoice '
                        'registration cannot be verified. Set the TPIN and use '
                        '"Verify TPIN on ZRA" on the contact form.')
                    % partner.name,
                }}

    # ── action_post override ─────────────────────────────────────────────────
    def action_post(self):
        """Override post to auto-sync with ZRA."""
        # Internal checklist #13: never post a bill flagged "Automatic" (ZRA
        # Get Purchases) for a supplier confirmed NOT registered on Smart
        # Invoice — that combination is contradictory. Manual capture of
        # unregistered-supplier purchases stays allowed (checklist #15).
        for move in self:
            if move.move_type in ('in_invoice', 'in_refund') and move.partner_id:
                if move.partner_id.zra_si_registration == 'not_registered' \
                        and move.zra_purchase_reg_type != 'M':
                    raise UserError(_(
                        'Supplier "%s" (TPIN %s) is NOT registered on the ZRA '
                        'Smart Invoice system. Ask them to register, or set '
                        'the ZRA Registration Type on this bill to "Manual" '
                        'to record it as a manual purchase.'
                    ) % (move.partner_id.name, move.partner_id.zra_tpin or ''))
        res = super(AccountMove, self).action_post()

        for move in self:
            # FIX #5: Do NOT auto-sync if already synced (prevents CIS duplication)
            if move.zra_sync_status == 'synced':
                continue

            if move.move_type in ('out_invoice', 'out_refund'):
                try:
                    config = self.env['zra.config'].get_active_config(move.company_id.id)
                    if config.auto_sync_invoices and config.is_initialized:
                        move.action_send_to_zra()
                except Exception as e:
                    _logger.error(f"ZRA auto-sync failed for {move.name}: {str(e)}")
                    move.write({
                        'zra_sync_status': 'failed',
                        'zra_error_message': str(e),
                    })

            elif move.move_type in ('in_invoice', 'in_refund'):
                try:
                    config = self.env['zra.config'].get_active_config(move.company_id.id)
                    if config.auto_sync_purchases and config.is_initialized:
                        move.action_send_purchase_to_zra()
                except Exception as e:
                    _logger.error(f"ZRA purchase auto-sync failed for {move.name}: {str(e)}")
                    move.write({
                        'zra_sync_status': 'failed',
                        'zra_error_message': str(e),
                    })

        return res

    # ── Sales invoice / credit note ──────────────────────────────────────────
    def action_send_to_zra(self):
        """Send sales invoice or credit/debit note to ZRA."""
        for move in self:
            if move.move_type not in ('out_invoice', 'out_refund'):
                raise UserError(_('Only customer invoices and refunds can be sent to ZRA'))

            if move.state != 'posted':
                raise UserError(_('Only posted invoices can be sent to ZRA'))

            # FIX #5: Hard block on already-synced invoices
            if move.zra_sync_status == 'synced':
                raise UserError(_('This invoice is already synced with ZRA'))

            try:
                config = self.env['zra.config'].get_active_config(move.company_id.id)

                if not config.is_initialized:
                    raise UserError(
                        _('ZRA device is not initialized. Please initialize the device first.')
                    )

                # Run all pre-sync validations
                move._zra_validate_before_sync()

                api_client = self.env['zra.api.client']
                result = api_client.submit_sale(config, move)

                if result.get('resultCd') == '000':
                    # .get('data', {}) only applies the default when the key
                    # is MISSING, not when it's present but null — and ZRA
                    # confirmed-live sends "data": null even on a successful
                    # (resultCd 000) response for some endpoints. `or {}`
                    # guards against both cases.
                    data = result.get('data') or {}
                    # Confirmed keys per VSDC spec §5.8 Save Sales response
                    # sample (p. 100): rcptNo, intrlData, rcptSign, sdcId,
                    # mrcNo, vsdcRcptPbctDate, qrCodeUrl. Older/alternate key
                    # spellings kept as fallbacks in case a given VSDC build
                    # differs from the documented sample.
                    receipt_number = data.get('rcptNo', '')
                    internal_data = data.get('intrlData', '')
                    signature = data.get('rcptSign', '')
                    sdc_id = data.get('sdcId', '') or config.device_serial or ''
                    mrc_no = data.get('mrcNo', '')
                    vsdc_date = (data.get('vsdcRcptPbctDate', '')
                                 or data.get('rcptPbctDt', '')
                                 or data.get('sdcDateTime', ''))
                    qr_code = (data.get('qrCodeUrl', '')
                               or data.get('qrCdUrl', '')
                               or data.get('qrCode', ''))
                    total_tax = data.get('totTaxAmt', move.amount_tax)

                    if receipt_number:
                        zra_invoice_number = (
                            f"INV{sdc_id.replace('SDC', '') if sdc_id else ''}"
                            f"/{receipt_number}"
                        )
                    else:
                        zra_invoice_number = ''

                    move.write({
                        'zra_receipt_number': receipt_number,
                        'zra_internal_data': internal_data,
                        'zra_signature': signature,
                        'zra_sdc_id': (
                            sdc_id if sdc_id
                            else (f"SDC{config.device_serial}" if config.device_serial else '')
                        ),
                        'zra_mrc_no': mrc_no,
                        'zra_invoice_number': zra_invoice_number,
                        'zra_vsdc_receipt_date': vsdc_date,
                        'zra_qr_code': qr_code,
                        'zra_total_tax_amount': float(total_tax) if total_tax else move.amount_tax,
                        'zra_sync_status': 'synced',
                        'zra_sync_date': fields.Datetime.now(),
                        'zra_error_message': False,
                    })

                    if config.auto_sync_stock:
                        self._sync_stock_after_sale(config)

                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Success'),
                            'message': _(
                                'Invoice synced with ZRA! Receipt No: %s'
                            ) % receipt_number,
                            'type': 'success',
                            'sticky': False,
                        }
                    }
                else:
                    error_msg = result.get('resultMsg', 'Unknown error')
                    move.write({
                        'zra_sync_status': 'failed',
                        'zra_error_message': error_msg,
                    })
                    raise UserError(_('ZRA sync failed: %s') % error_msg)

            except Exception as e:
                move.write({
                    'zra_sync_status': 'failed',
                    'zra_error_message': str(e),
                })
                raise UserError(_('Error sending to ZRA: %s') % str(e))

    # ── Purchase / vendor bill ───────────────────────────────────────────────
    def action_send_purchase_to_zra(self):
        """Send vendor bill to ZRA."""
        for move in self:
            if move.move_type not in ('in_invoice', 'in_refund'):
                raise UserError(_('Only vendor bills and refunds can be sent to ZRA'))

            if move.state != 'posted':
                raise UserError(_('Only posted bills can be sent to ZRA'))

            # FIX #5: Block re-submission
            if move.zra_sync_status == 'synced':
                raise UserError(_('This bill is already synced with ZRA'))

            # FIX #4: Validate supplier TPIN format IF one is set — spplrTpin
            # is optional per spec (checklist #15: suppliers not registered
            # on Smart Invoice have no TPIN, and can still be synced manually).
            supplier_tpin = (move.partner_id.zra_tpin or '').strip()
            if supplier_tpin and len(supplier_tpin) != 10:
                raise UserError(
                    _('Supplier TPIN "%s" is not valid (must be exactly 10 digits).\n'
                      'Vendor: %s')
                    % (supplier_tpin, move.partner_id.display_name)
                )

            try:
                config = self.env['zra.config'].get_active_config(move.company_id.id)

                if not config.is_initialized:
                    raise UserError(_('ZRA device is not initialized.'))

                move._zra_validate_before_sync()

                api_client = self.env['zra.api.client']
                result = api_client.submit_purchase(config, move)

                if result.get('resultCd') == '000':
                    data = result.get('data') or {}
                    receipt_number = data.get('rcptNo', '') or data.get('pchsRcptNo', '')
                    internal_data = data.get('intrlData', '')
                    signature = data.get('rcptSign', '')
                    vsdc_date = (data.get('rcptPbctDt', '')
                                 or data.get('sdcDateTime', '')
                                 or data.get('resultDt', ''))
                    qr_code = (data.get('qrCdUrl', '')
                               or data.get('qrCode', '')
                               or data.get('qrCd', ''))
                    mrc_no = data.get('mrcNo', '')

                    if config.device_serial:
                        sdc_id = (config.device_serial if config.device_serial.startswith('SDC')
                                  else f"SDC{config.device_serial}")
                    else:
                        sdc_id = data.get('sdcId', '')

                    zra_doc_no = f"PCHS/{receipt_number}" if receipt_number else ''

                    move.write({
                        'zra_receipt_number': receipt_number,
                        'zra_internal_data': internal_data,
                        'zra_signature': signature,
                        'zra_sdc_id': sdc_id,
                        'zra_mrc_no': mrc_no,
                        'zra_invoice_number': zra_doc_no,
                        'zra_vsdc_receipt_date': vsdc_date,
                        'zra_qr_code': qr_code,
                        'zra_total_tax_amount': float(move.amount_tax),
                        'zra_sync_status': 'synced',
                        'zra_sync_date': fields.Datetime.now(),
                        'zra_error_message': False,
                    })

                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Success'),
                            'message': _('Purchase synced with ZRA successfully!'),
                            'type': 'success',
                            'sticky': False,
                        }
                    }

                move.write({
                    'zra_sync_status': 'failed',
                    'zra_error_message': result.get('resultMsg') or 'ZRA Error',
                    'zra_sync_date': fields.Datetime.now(),
                })
                raise UserError(
                    _('ZRA Error: %s') % (result.get('resultMsg') or 'Unknown error')
                )

            except Exception as e:
                move.write({
                    'zra_sync_status': 'failed',
                    'zra_error_message': str(e),
                    'zra_sync_date': fields.Datetime.now(),
                })
                raise

    # ── Debit note action (FIX #9) ───────────────────────────────────────────
    def action_send_debit_note_to_zra(self):
        """Send debit note to ZRA."""
        for move in self:
            if move.move_type != 'out_invoice':
                raise UserError(_('Debit notes must be customer invoices (out_invoice).'))

            if not move.zra_debit_reason:
                raise UserError(
                    _('ZRA requires a Debit Note Reason. '
                      'Please select one in the ZRA Details tab.')
                )

            if not move.zra_original_invoice_id:
                raise UserError(
                    _('Please link the original invoice in the ZRA Details tab.')
                )

            if move.zra_sync_status == 'synced':
                raise UserError(_('This debit note is already synced with ZRA.'))

            try:
                config = self.env['zra.config'].get_active_config(move.company_id.id)
                if not config.is_initialized:
                    raise UserError(_('ZRA device is not initialized.'))

                move._zra_validate_before_sync()

                api_client = self.env['zra.api.client']
                result = api_client.submit_debit_note(config, move)

                if result.get('resultCd') == '000':
                    # .get('data', {}) only applies the default when the key
                    # is MISSING, not when it's present but null — and ZRA
                    # confirmed-live sends "data": null even on a successful
                    # (resultCd 000) response for some endpoints. `or {}`
                    # guards against both cases.
                    data = result.get('data') or {}
                    move.write({
                        'zra_receipt_number': data.get('rcptNo', ''),
                        'zra_internal_data': data.get('intrlData', ''),
                        'zra_signature': data.get('rcptSign', ''),
                        'zra_sdc_id': data.get('sdcId', '') or config.device_serial or '',
                        'zra_mrc_no': data.get('mrcNo', ''),
                        'zra_sync_status': 'synced',
                        'zra_sync_date': fields.Datetime.now(),
                        'zra_error_message': False,
                    })
                    return {
                        'type': 'ir.actions.client',
                        'tag': 'display_notification',
                        'params': {
                            'title': _('Success'),
                            'message': _('Debit note synced with ZRA!'),
                            'type': 'success',
                            'sticky': False,
                        }
                    }
                else:
                    raise UserError(
                        _('ZRA debit note failed: %s') % result.get('resultMsg', 'Unknown error')
                    )

            except Exception as e:
                move.write({
                    'zra_sync_status': 'failed',
                    'zra_error_message': str(e),
                })
                raise UserError(_('Error sending debit note to ZRA: %s') % str(e))

    # ── Utility actions ──────────────────────────────────────────────────────
    def action_regenerate_qr_code(self):
        for move in self:
            if move.zra_sync_status == 'synced':
                move._compute_qr_code_image()
        return True

    def _sync_stock_after_sale(self, config):
        self.ensure_one()
        if self.invoice_origin:
            stock_moves = self.env['stock.move'].search([
                ('sale_line_id.order_id.name', '=', self.invoice_origin),
                ('state', '=', 'done'),
            ])
            if stock_moves:
                try:
                    api_client = self.env['zra.api.client']
                    api_client.submit_stock_movement(config, stock_moves)
                except Exception as e:
                    _logger.error(f"Stock sync failed: {str(e)}")

    def action_view_zra_logs(self):
        self.ensure_one()
        return {
            'name': _('ZRA API Logs'),
            'type': 'ir.actions.act_window',
            'res_model': 'zra.api.log',
            'view_mode': 'list,form',
            'domain': [('invoice_id', '=', self.id)],
        }

    def get_zra_receipt_details(self):
        self.ensure_one()
        return {
            'invoice_number': self.zra_invoice_number or '',
            'receipt_number': self.zra_receipt_number or '',
            'internal_data': self.zra_internal_data or '',
            'signature': self.zra_signature or '',
            'sdc_id': self.zra_sdc_id or '',
            'mrc_no': self.zra_mrc_no or '',
            'qr_code_image': self.zra_qr_code_image,
            'total_tax': self.zra_total_tax_amount or self.amount_tax,
            'total_excl': self.amount_untaxed,
            'total_incl': self.amount_total,
            'sync_date': self.zra_sync_date,
            'payment_type': self.zra_payment_type or '01',
            'refund_reason': self.zra_refund_reason or '',
        }
