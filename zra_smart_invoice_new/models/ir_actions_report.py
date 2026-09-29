# -*- coding: utf-8 -*-
from odoo import models


class IrActionsReport(models.Model):
    _inherit = 'ir.actions.report'

    def _pre_render_qweb_pdf(self, report_ref, res_ids=None, data=None):
        """Track reprints of the ZRA fiscal tax invoice (checklist #24 —
        reprints must be marked 'copy'/'duplicate'). Every PDF render of an
        already-synced invoice past the first counts as a reprint; the
        report template shows a COPY watermark once zra_print_count > 1.
        """
        report = self._get_report(report_ref)
        if report.report_name == 'zra_smart_invoice_new.report_zra_invoice_details' and res_ids:
            moves = self.env['account.move'].browse(res_ids).filtered(
                lambda m: m.zra_sync_status == 'synced'
            )
            for move in moves:
                move.zra_print_count += 1
        return super()._pre_render_qweb_pdf(report_ref, res_ids=res_ids, data=data)
