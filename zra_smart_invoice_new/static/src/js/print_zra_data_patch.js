/** @odoo-module **/

import { ReceiptScreen } from "@point_of_sale/app/screens/receipt_screen/receipt_screen";
import { PosOrder } from "@point_of_sale/app/models/pos_order";
import { patch } from "@web/core/utils/patch";
import { useTrackedAsync } from "@point_of_sale/app/utils/hooks";

const ZRA_FIELDS = [
    'id', 'zra_receipt_number', 'zra_invoice_number', 'zra_internal_data',
    'zra_signature', 'zra_sdc_id', 'zra_mrc_no', 'zra_sync_status',
    'zra_qr_code_image',
];

async function ensureZraDataOnOrder(ormService, order) {
    if (order.zra_receipt_number) {
        return; // already present, nothing to do
    }
    try {
        const orderName = order.pos_reference || order.name;
        if (!orderName) {
            return;
        }
        let rows = await ormService.searchRead(
            'pos.order', [['pos_reference', '=', orderName]], ZRA_FIELDS, { limit: 1 }
        );
        if (!rows || !rows.length) {
            rows = await ormService.searchRead(
                'pos.order', [['name', '=', orderName]], ZRA_FIELDS, { limit: 1 }
            );
        }
        if (rows && rows.length && rows[0].zra_receipt_number) {
            Object.assign(order, rows[0]);
        }
    } catch (e) {
        console.error("ZRA print pre-fetch failed:", e);
    }
}

patch(ReceiptScreen.prototype, {
    setup() {
        super.setup();
        this.doFullPrint = useTrackedAsync(async () => {
            await ensureZraDataOnOrder(this.env.services.orm, this.currentOrder);
            return this.pos.printReceipt();
        });
        this.doBasicPrint = useTrackedAsync(async () => {
            await ensureZraDataOnOrder(this.env.services.orm, this.currentOrder);
            return this.pos.printReceipt({ basic: true });
        });
    },
});

patch(PosOrder.prototype, {
    export_for_printing(baseUrl, headerData) {
        const result = super.export_for_printing(...arguments);
        result.zra_receipt_number = this.zra_receipt_number || '';
        result.zra_invoice_number = this.zra_invoice_number || '';
        result.zra_internal_data = this.zra_internal_data || '';
        result.zra_signature = this.zra_signature || '';
        result.zra_sdc_id = this.zra_sdc_id || '';
        result.zra_mrc_no = this.zra_mrc_no || '';
        result.zra_sync_status = this.zra_sync_status || '';
        result.zra_qr_code_image = this.zra_qr_code_image || '';
        return result;
    },
});
