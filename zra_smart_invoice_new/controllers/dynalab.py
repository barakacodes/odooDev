# -*- coding: utf-8 -*-
import logging

from odoo import fields, http
from odoo.http import request


_logger = logging.getLogger(__name__)
_API_TOKEN = 'DynalabProcessDialSecret2026!'
_OUTLET_COMPANY_PARAM = 'zra_dynalab.outlet.%s.company_id'


class DynalabZRAController(http.Controller):

    @http.route(
        '/api/v1/zra/submit_invoice',
        type='json',
        auth='none',
        methods=['POST'],
        csrf=False,
    )
    def submit_invoice(self, **kwargs):
        if request.httprequest.headers.get('API-Token') != _API_TOKEN:
            _logger.warning('Rejected Dynalab ZRA request: invalid API-Token')
            return {'status': 'ERROR', 'error': 'Invalid API-Token'}

        try:
            payload = request.httprequest.get_json(silent=True) or {}
            outlet_code = str(payload.get('outlet_code') or '').strip().upper()
            receipt_no = payload.get('receipt_no')
            payment_method = str(payload.get('payment_method') or 'Cash').strip()
            lines = payload.get('lines')

            if not outlet_code:
                return {'status': 'ERROR', 'error': 'outlet_code must be a non-empty string'}
            if not isinstance(receipt_no, str) or not receipt_no.strip():
                return {'status': 'ERROR', 'error': 'receipt_no must be a non-empty string'}
            if not isinstance(lines, list) or not lines:
                return {'status': 'ERROR', 'error': 'lines must be a non-empty array'}

            env = request.env.sudo()
            company_id_value = env['ir.config_parameter'].get_param(
                _OUTLET_COMPANY_PARAM % outlet_code
            )
            try:
                company_id = int(company_id_value or 0)
            except (TypeError, ValueError):
                company_id = 0
            company = env['res.company'].browse(company_id).exists()
            if not company:
                return {
                    'status': 'ERROR',
                    'error': 'Outlet is not mapped to a valid company: %s' % outlet_code,
                }

            config = env['zra.config'].search([
                ('company_id', '=', company.id),
                ('active', '=', True),
            ], limit=1)
            if not config:
                return {
                    'status': 'ERROR',
                    'error': 'Active ZRA configuration not found for outlet %s' % outlet_code,
                }

            company_env = env.with_company(company)
            move_model = company_env['account.move']
            reference = 'Dynalab-%s-%s' % (outlet_code, receipt_no.strip())
            existing = move_model.search([
                ('ref', '=', reference),
                ('company_id', '=', company.id),
            ], limit=1)
            if existing and existing.zra_sync_status == 'synced':
                return self._success_response(existing)

            payment_codes = {
                'cash': '01',
                'credit': '02',
                'cash/credit': '03',
                'bank check': '04',
                'cheque': '04',
                'check': '04',
                'debit & credit card': '05',
                'card': '05',
                'mobile money': '06',
                'mobile': '06',
                'other': '07',
                'bank transfer': '08',
            }
            payment_code = payment_codes.get(payment_method.casefold())
            if not payment_code:
                return {
                    'status': 'ERROR',
                    'error': 'Unsupported payment_method: %s' % payment_method,
                }

            product_model = company_env['product.product']
            invoice_lines = []
            for line_number, line in enumerate(lines, 1):
                if not isinstance(line, dict):
                    return {'status': 'ERROR', 'error': 'Line %s must be an object' % line_number}
                itemcode = line.get('itemcode')
                if not isinstance(itemcode, str) or not itemcode.strip():
                    return {'status': 'ERROR', 'error': 'Line %s has no itemcode' % line_number}
                try:
                    quantity = float(line['qty'])
                    price = float(line['price'])
                except (KeyError, TypeError, ValueError):
                    return {
                        'status': 'ERROR',
                        'error': 'Line %s has invalid qty or price' % line_number,
                    }
                if quantity <= 0 or price < 0:
                    return {
                        'status': 'ERROR',
                        'error': 'Line %s has invalid qty or price' % line_number,
                    }
                product = product_model.search([
                    '|',
                    ('default_code', '=', itemcode.strip()),
                    ('barcode', '=', itemcode.strip()),
                ], limit=1)
                if not product:
                    return {'status': 'ERROR', 'error': 'Product not found: %s' % itemcode}
                invoice_lines.append((0, 0, {
                    'product_id': product.id,
                    'name': product.display_name,
                    'quantity': quantity,
                    'price_unit': price,
                }))

            partner = company_env['res.partner'].search([
                ('name', '=ilike', 'Walkin Customer'),
                '|', ('company_id', '=', company.id), ('company_id', '=', False),
            ], limit=1)
            if not partner:
                return {'status': 'ERROR', 'error': 'Walkin Customer is not configured'}

            move = existing or move_model.create({
                'move_type': 'out_invoice',
                'ref': reference,
                'invoice_date': fields.Date.context_today(move_model),
                'company_id': company.id,
                'partner_id': partner.id,
                'zra_payment_type': payment_code,
                'invoice_line_ids': invoice_lines,
            })
            if move.zra_sync_status != 'synced':
                if move.state != 'posted':
                    move.action_post()
                if move.zra_sync_status != 'synced':
                    move.action_send_to_zra()

            if move.zra_sync_status != 'synced':
                return {
                    'status': 'ERROR',
                    'error': move.zra_error_message or 'ZRA submission failed',
                }
            return self._success_response(move)
        except Exception as error:
            _logger.exception('Dynalab ZRA submission failed')
            return {'status': 'ERROR', 'error': str(error)}

    @staticmethod
    def _success_response(move):
        return {
            'status': 'SUCCESS',
            'irn': move.zra_invoice_number or move.zra_receipt_number or '',
            'hash': move.zra_signature or move.zra_internal_data or '',
            'qr_data': move.zra_qr_code or '',
            'receipt_number': move.zra_receipt_number or '',
            'sdc_id': move.zra_sdc_id or '',
            'mrc_no': move.zra_mrc_no or '',
            'internal_data': move.zra_internal_data or '',
            'signature': move.zra_signature or '',
            'vsdc_receipt_date': move.zra_vsdc_receipt_date or '',
        }
