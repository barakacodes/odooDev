import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

patch(PaymentScreen.prototype, {
    async validateOrder(isForceValidate) {
        await this._super(isForceValidate);
        
        const order = this.pos.get_order();
        if (order && order.get_server_id()) {
            // Try to auto-sync with ZRA after validation
            try {
                const zraData = await this.env.pos.rpc({
                    model: 'pos.order',
                    method: 'sync_zra_data_to_order',
                    args: [order.get_server_id()],
                });
                
                if (zraData && zraData.zra_sync_status === 'synced') {
                    // Update order with ZRA data
                    order.zra_receipt_number = zraData.zra_receipt_number;
                    order.zra_invoice_number = zraData.zra_invoice_number;
                    order.zra_internal_data = zraData.zra_internal_data;
                    order.zra_signature = zraData.zra_signature;
                    order.zra_sdc_id = zraData.zra_sdc_id;
                    order.zra_mrc_no = zraData.zra_mrc_no;
                    order.zra_qr_code_image = zraData.zra_qr_code_image;
                    order.zra_sync_status = zraData.zra_sync_status;
                    
                    // Show notification that ZRA sync was successful
                    this.env.services.notification.add(_t('ZRA Sync Successful'), {
                        type: 'success',
                        sticky: false,
                    });
                }
            } catch (error) {
                console.error('Auto ZRA sync failed:', error);
                // Don't show error to user immediately, as order is already validated
            }
        }
    },
});
