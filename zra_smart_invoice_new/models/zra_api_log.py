# -*- coding: utf-8 -*-
from odoo import models, fields


class ZRAAPILog(models.Model):
    _name = 'zra.api.log'
    _description = 'ZRA API Call Log'
    _order = 'call_date desc'

    company_id = fields.Many2one('res.company', string='Company', required=True, index=True)
    endpoint = fields.Char(string='Endpoint', required=True, index=True)
    request_data = fields.Text(string='Request Data')
    response_data = fields.Text(string='Response Data')
    status = fields.Selection([
        ('success', 'Success'),
        ('failed', 'Failed')
    ], string='Status', required=True, index=True,
       help="Transport-layer outcome only: whether the HTTP call to the "
            "VSDC completed and returned parseable JSON. A '000' business "
            "resultCd is NOT required for this to be 'success' — e.g. a "
            "device-already-installed '902' response is a normal HTTP 200 "
            "with valid JSON. Check Result Code for the actual ZRA "
            "business outcome; do not assume 'success' here means ZRA "
            "accepted the request.")
    result_code = fields.Char(
        string='Result Code', index=True,
        help="ZRA's own resultCd from the response body (e.g. '000' = "
             "accepted). Populated whenever the response was valid JSON, "
             "regardless of the Status column above — this is what an "
             "auditor cross-referencing this log against a record's own "
             "zra_sync_status should actually check.")
    result_message = fields.Char(string='Result Message')
    call_date = fields.Datetime(string='Call Date', required=True, index=True)

    # Related records
    invoice_id = fields.Many2one('account.move', string='Invoice')
    pos_order_id = fields.Many2one('pos.order', string='POS Order')

    def action_retry(self):
        """Retry failed API call"""
        # This can be implemented later to retry failed calls
        pass
