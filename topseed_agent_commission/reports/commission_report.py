from odoo import models, api

class CommissionReport(models.AbstractModel):
    _name = 'report.topseed_agent_commission.commission_report'
    _description = 'Commission Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        docs = self.env['topseed.commission.line'].browse(docids)
        return {
            'doc_ids': docids,
            'doc_model': 'topseed.commission.line',
            'docs': docs,
            'data': data,
        }
