/** @odoo-module **/

import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { patch } from "@web/core/utils/patch";

patch(PaymentScreen.prototype, {
    /**
     * Disable automatic invoice PDF download/open right after payment validation.
     * Invoice creation/posting/ZRA sync still happens normally on the backend;
     * this only stops the browser from auto-downloading the PDF.
     */
    shouldDownloadInvoice() {
        return false;
    },
});
