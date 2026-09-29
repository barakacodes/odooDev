import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";

patch(PosOrder.prototype, {
    get_zra_receipt_number() {
        return this.zra_receipt_number || '';
    },
    get_zra_invoice_number() {
        return this.zra_invoice_number || '';
    },
    get_zra_internal_data() {
        return this.zra_internal_data || '';
    },
    get_zra_signature() {
        return this.zra_signature || '';
    },
    get_zra_sdc_id() {
        return this.zra_sdc_id || '';
    },
    get_zra_mrc_no() {
        return this.zra_mrc_no || '';
    },
    get_zra_qr_code_image() {
        return this.zra_qr_code_image || '';
    },
    get_zra_sync_status() {
        return this.zra_sync_status || 'pending';
    }
});