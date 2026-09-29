/** @odoo-module **/

import { InvoiceButton } from "@point_of_sale/app/screens/ticket_screen/invoice_button/invoice_button";
import { patch } from "@web/core/utils/patch";

patch(InvoiceButton.prototype, {
    /**
     * Disable automatic PDF download/open.
     * The invoice is still created/posted/ZRA-synced normally;
     * this only stops the browser from auto-downloading the PDF.
     */
    async _downloadInvoice(orderId) {
        // Intentionally no-op: invoice creation/sync still happens,
        // we just skip the auto-download/open step.
        return;
    },
});
