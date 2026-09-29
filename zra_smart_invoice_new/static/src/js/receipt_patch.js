/** @odoo-module **/

import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { patch } from "@web/core/utils/patch";
import { onMounted, useState } from "@odoo/owl";

patch(OrderReceipt.prototype, {
    /**
     * Setup with state management for ZRA data
     */
    setup() {
        super.setup();

        // Create reactive state for ZRA data
        this.zraState = useState({
            isLoading: true,
            hasData: false,
            retryCount: 0,
            maxRetries: 5
        });

        // Start loading ZRA data when component mounts
        onMounted(async () => {
            console.log("OrderReceipt mounted, checking for ZRA data");

            // Check if ZRA data is already present in props
            if (this.props.data.zra_receipt_number && this.props.data.zra_receipt_number !== '') {
                console.log("ZRA data already present in props:", {
                    receipt: this.props.data.zra_receipt_number,
                    invoice: this.props.data.zra_invoice_number,
                    status: this.props.data.zra_sync_status
                });
                this.zraState.hasData = true;
                this.zraState.isLoading = false;
            } else {
                console.log("ZRA data not in props, loading from server");
                await this.loadZRAData();
            }
        });
    },

    /**
     * Load ZRA data with retry logic
     */
    async loadZRAData() {
        try {
            const order = this.props.data;

            // In Odoo 18, we need to find the order by the receipt reference
            // The props have 'name' but this might not match database 'name' field
            const orderName = order.name;
            const ticketCode = order.ticket_code;

            console.log("Order data structure:", {
                has_name: !!orderName,
                orderName: orderName,
                has_ticket_code: !!ticketCode,
                ticketCode: ticketCode,
                orderKeys: Object.keys(order)
            });

            if (!orderName) {
                console.log("No order identifier found, skipping ZRA data load");
                this.zraState.isLoading = false;
                return;
            }

            console.log(`Loading ZRA data for order ${orderName} (attempt ${this.zraState.retryCount + 1}/${this.zraState.maxRetries})`);

            // Try multiple search strategies to find the order
            let orders = null;

            // Strategy 1: Search by pos_reference (most likely to match)
            try {
                orders = await this.env.services.orm.searchRead(
                    'pos.order',
                    [['pos_reference', '=', orderName]],
                    ['id', 'name', 'zra_receipt_number', 'zra_invoice_number', 'zra_internal_data',
                     'zra_signature', 'zra_sdc_id', 'zra_mrc_no', 'zra_sync_status',
                     'zra_qr_code_image'],
                    {limit: 1}
                );
                if (orders && orders.length > 0) {
                    console.log("Found order by pos_reference");
                }
            } catch (e) {
                console.log("Search by pos_reference failed:", e);
            }

            // Strategy 2: If not found, try by name field
            if (!orders || orders.length === 0) {
                try {
                    orders = await this.env.services.orm.searchRead(
                        'pos.order',
                        [['name', '=', orderName]],
                        ['id', 'name', 'zra_receipt_number', 'zra_invoice_number', 'zra_internal_data',
                         'zra_signature', 'zra_sdc_id', 'zra_mrc_no', 'zra_sync_status',
                         'zra_qr_code_image'],
                        {limit: 1}
                    );
                    if (orders && orders.length > 0) {
                        console.log("Found order by name");
                    }
                } catch (e) {
                    console.log("Search by name failed:", e);
                }
            }

            // Strategy 3: If still not found, try searching recent orders and match by name pattern
            if (!orders || orders.length === 0) {
                try {
                    // Get last 50 orders and find matching one
                    const recentOrders = await this.env.services.orm.searchRead(
                        'pos.order',
                        [],
                        ['id', 'name', 'pos_reference', 'zra_receipt_number', 'zra_invoice_number',
                         'zra_internal_data', 'zra_signature', 'zra_sdc_id', 'zra_mrc_no',
                         'zra_sync_status', 'zra_qr_code_image'],
                        {limit: 50, order: 'id desc'}
                    );

                    // Try to match by pos_reference or name containing the order name
                    orders = recentOrders.filter(o =>
                        o.pos_reference === orderName ||
                        o.name === orderName ||
                        orderName.includes(o.name) ||
                        o.name.includes(orderName)
                    );

                    if (orders && orders.length > 0) {
                        console.log("Found order by pattern matching in recent orders");
                        orders = [orders[0]]; // Take first match
                    }
                } catch (e) {
                    console.log("Search in recent orders failed:", e);
                }
            }

            if (orders && orders.length > 0) {
                const zraData = orders[0];
                console.log("Updated ZRA data received:", zraData);

                // Update the receipt data with ZRA information
                Object.assign(this.props.data, {
                    id: zraData.id,
                    zra_receipt_number: zraData.zra_receipt_number || '',
                    zra_invoice_number: zraData.zra_invoice_number || '',
                    zra_internal_data: zraData.zra_internal_data || '',
                    zra_signature: zraData.zra_signature || '',
                    zra_sdc_id: zraData.zra_sdc_id || '',
                    zra_mrc_no: zraData.zra_mrc_no || '',
                    zra_sync_status: zraData.zra_sync_status || '',
                    zra_qr_code_image: zraData.zra_qr_code_image || '',
                });

                // Check if we have actual ZRA data
                if (zraData.zra_receipt_number && zraData.zra_receipt_number !== false) {
                    this.zraState.hasData = true;
                    this.zraState.isLoading = false;
                    console.log("ZRA data loaded successfully");
                } else if (this.zraState.retryCount < this.zraState.maxRetries) {
                    // No data yet, retry after delay
                    this.zraState.retryCount++;
                    console.log(`No ZRA data yet, retrying in 2 seconds... (attempt ${this.zraState.retryCount})`);
                    setTimeout(() => this.loadZRAData(), 2000);
                } else {
                    // Max retries reached
                    this.zraState.isLoading = false;
                    console.log("Max retries reached, no ZRA data available");
                }
            } else {
                console.log("No order found with name:", orderName);

                // Retry if we haven't exceeded max retries
                if (this.zraState.retryCount < this.zraState.maxRetries) {
                    this.zraState.retryCount++;
                    console.log(`Order not found yet, retrying in 2 seconds... (attempt ${this.zraState.retryCount})`);
                    setTimeout(() => this.loadZRAData(), 2000);
                } else {
                    this.zraState.isLoading = false;
                }
            }
        } catch (error) {
            console.error("Failed to load ZRA data:", error);

            // Retry if we haven't exceeded max retries
            if (this.zraState.retryCount < this.zraState.maxRetries) {
                this.zraState.retryCount++;
                setTimeout(() => this.loadZRAData(), 2000);
            } else {
                this.zraState.isLoading = false;
            }
        }
    },

    /**
     * Get ZRA debug information
     */
    get zraDebugInfo() {
        if (!this.props || !this.props.data) {
            return {
                status: 'No Props',
                receipt: 'No Data',
                invoice: 'No Data',
                company: 'No Data'
            };
        }

        return {
            status: this.props.data.zra_sync_status || 'No Status',
            receipt: this.props.data.zra_receipt_number || 'No Receipt',
            invoice: this.props.data.zra_invoice_number || 'No Invoice',
            company: this.props.data.headerData?.company?.name || 'No Company',
            isLoading: this.zraState.isLoading,
            hasData: this.zraState.hasData,
            retryCount: this.zraState.retryCount
        };
    }
});