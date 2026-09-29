/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onMounted, onWillUnmount, useState } from "@odoo/owl";

function _getPalette() {
    const isDark = document.body.classList.contains("fc-dark-mode");
    return isDark ? {
        c1: "#38BDF8", c2: "#7DD3FC", c3: "#93C5FD", c4: "#2DD4BF",
        c5: "#5EEAD4", c6: "#FBBF24", c7: "#FCD34D", c8: "#F87171",
        c9: "#A3D9A5",
        grid: "rgba(148, 163, 184, 0.1)", text: "#94A3B8",
        tooltipBg: "#1F2937",
        success: "#34D399", warning: "#FBBF24", danger: "#F87171",
    } : {
        c1: "#0F2942", c2: "#1E4976", c3: "#4A6FA5", c4: "#0E7C86",
        c5: "#14A3AF", c6: "#C9A961", c7: "#E0C486", c8: "#E76F51",
        c9: "#7FA88A",
        grid: "rgba(148, 163, 184, 0.12)", text: "#64748B",
        tooltipBg: "#0F2942",
        success: "#059669", warning: "#D97706", danger: "#DC2626",
    };
}

function _hexToRgba(hex, alpha) {
    if (!hex) return `rgba(0,0,0,${alpha})`;
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
}

export class ForecourtDashboard extends Component {
    static template = "forecourt_dashboard.Dashboard";

    setup() {
        this.orm = useService("orm");
        this._charts = {};
        this._refreshInterval = null;

        const now = new Date();
        const firstDay = new Date(now.getFullYear(), now.getMonth(), 1);
        const lastDay  = new Date(now.getFullYear(), now.getMonth() + 1, 0);

        this.state = useState({
            loading: false,
            activeTab: "cash",
            branch: "all",
            dateFrom: firstDay.toISOString().slice(0, 10),
            dateTo:   lastDay.toISOString().slice(0, 10),
            targetYear: now.getFullYear(),
            targetMonth: String(now.getMonth() + 1),
            darkMode: false,
            autoRefresh: true,
            lastRefresh: new Date().toLocaleTimeString(),
            data: {
                cash_sales: [], cash_sales_total: 0,
                credit_sales: [], credit_sales_total: 0,
                inventory: [],
                deposits: [], total_deposits: 0,
                variances: [], total_variance: 0, total_cash_variance: 0,
                tanks: [], branch_stock: [], targets: [],
                profitability: { rows: [], grand_revenue: 0, grand_cost: 0, grand_profit: 0, grand_margin: 0 },
                losses: { rows: [], grand_stock_loss: 0, grand_cash_var: 0, grand_net: 0 },
                stock_val: { rows: [], total_cost_value: 0, total_pump_value: 0 },
                trends: { labels: [], cash: [], variance: [], stock_var: [] },
                fuel_detail: { rows: [], grand: { petrol: {}, diesel: {} }, shift_grand: {}, combined: {} },
            },
        });

        onMounted(() => {
            const savedDark = localStorage.getItem("fc_dark_mode") === "true";
            if (savedDark) {
                this.state.darkMode = true;
                document.body.classList.add("fc-dark-mode");
            }
            this._tagOdooWrapper();
            this.loadData();
            this._refreshInterval = setInterval(() => {
                if (!this.state.loading && this.state.autoRefresh) {
                    this.loadData(true);
                }
            }, 30000);
        });

        onWillUnmount(() => {
            this._destroyCharts();
            if (this._refreshInterval) clearInterval(this._refreshInterval);
            const wrapper = document.querySelector(".o_action_manager.fc_dashboard_wrapper");
            if (wrapper) wrapper.classList.remove("fc_dashboard_wrapper");
        });
    }

    _tagOdooWrapper() {
        const dashboard = document.querySelector(".fc_dashboard");
        if (!dashboard) return;
        const wrapper = dashboard.closest(".o_action_manager");
        if (wrapper) {
            wrapper.classList.add("fc_dashboard_wrapper");
        }
    }

    _destroyCharts() {
        Object.values(this._charts).forEach(c => { try { c.destroy(); } catch(e) {} });
        this._charts = {};
    }

    toggleDarkMode() {
        this.state.darkMode = !this.state.darkMode;
        document.body.classList.toggle("fc-dark-mode", this.state.darkMode);
        localStorage.setItem("fc_dark_mode", this.state.darkMode);
        setTimeout(() => this._renderAllCharts(), 120);
    }

    toggleAutoRefresh() { this.state.autoRefresh = !this.state.autoRefresh; }

    setTab(tab) {
        this.state.activeTab = tab;
        setTimeout(() => this._renderAllCharts(), 50);
    }

    onPeriodChange(ev) {
        const period = ev.target.value;
        const now = new Date();
        let from, to;
        if (period === "daily") {
            from = new Date(now.getFullYear(), now.getMonth(), now.getDate());
            to = from;
        } else if (period === "weekly") {
            const day = now.getDay();
            const diff = (day === 0 ? -6 : 1 - day);
            from = new Date(now.getFullYear(), now.getMonth(), now.getDate() + diff);
            to = new Date(from.getFullYear(), from.getMonth(), from.getDate() + 6);
        } else if (period === "yearly") {
            from = new Date(now.getFullYear(), 0, 1);
            to = new Date(now.getFullYear(), 11, 31);
        } else {
            from = new Date(now.getFullYear(), now.getMonth(), 1);
            to = new Date(now.getFullYear(), now.getMonth() + 1, 0);
        }
        this.state.dateFrom = from.toISOString().slice(0, 10);
        this.state.dateTo = to.toISOString().slice(0, 10);
        this.loadData();
    }

    onBranchChange(ev) { this.state.branch = ev.target.value; }
    onDateFromChange(ev) { this.state.dateFrom = ev.target.value; }
    onDateToChange(ev) { this.state.dateTo = ev.target.value; }
    onTargetMonthChange(ev) { this.state.targetMonth = ev.target.value; }
    onTargetYearChange(ev) { this.state.targetYear = parseInt(ev.target.value); }

    fmtK(value) {
        const n = parseFloat(value) || 0;
        return "K " + n.toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    }
    fmtL(value) {
        const n = parseFloat(value) || 0;
        return (n === Math.floor(n)) ? n.toFixed(0) : n.toFixed(2);
    }
    sumK(rows, field) {
        if (!rows || !rows.length) return "K 0.00";
        return this.fmtK(rows.reduce((a, r) => a + (parseFloat(r[field]) || 0), 0));
    }
    sumL(rows, field) {
        if (!rows || !rows.length) return "0.00";
        return rows.reduce((a, r) => a + (parseFloat(r[field]) || 0), 0).toFixed(2);
    }
    varClass(v, thresh) {
        return Math.abs(parseFloat(v) || 0) > thresh ? "text-danger fw-bold" : "";
    }
    varColor(v, zero_thresh = 0.005) {
        // Directional variance colour:
        //   positive -> gain (green)
        //   negative -> loss (red)
        //   within ±zero_thresh -> neutral
        const n = parseFloat(v) || 0;
        if (n > zero_thresh) return "fc_var_gain";
        if (n < -zero_thresh) return "fc_var_loss";
        return "";
    }
    badgeClass(s) {
        return s === "mismatch" ? "badge bg-danger" : s === "ok" ? "badge bg-success" : "badge bg-secondary";
    }
    tankCardClass(s) { return "fc_tank_card fc_tank_" + (s || "ok"); }
    pctBarClass(pct) {
        const n = parseFloat(pct) || 0;
        if (n >= 100) return "fc_pct_bar fc_pct_bar_success";
        if (n >= 70) return "fc_pct_bar fc_pct_bar_warning";
        return "fc_pct_bar fc_pct_bar_danger";
    }
    pctBarStyle(pct) { return "width:" + Math.min(parseFloat(pct) || 0, 100).toFixed(1) + "%"; }
    pctBadgeClass(pct) {
        const n = parseFloat(pct) || 0;
        if (n >= 100) return "badge bg-success";
        if (n >= 70) return "badge bg-warning text-dark";
        return "badge bg-danger";
    }

    async loadData(silent = false) {
        this.state.loading = !silent;
        this._destroyCharts();
        try {
            const [result, targets, profitability, losses, stock_val, trends, fuel_detail] = await Promise.all([
                this.orm.call("forecourt.dashboard", "get_dashboard_data", [],
                    { branch: this.state.branch, date_from: this.state.dateFrom, date_to: this.state.dateTo }),
                this.orm.call("forecourt.dashboard", "get_targets_data", [],
                    { branch: this.state.branch, year: this.state.targetYear, month: this.state.targetMonth }),
                this.orm.call("forecourt.dashboard", "get_profitability_data", [],
                    { branch: this.state.branch, date_from: this.state.dateFrom, date_to: this.state.dateTo }),
                this.orm.call("forecourt.dashboard", "get_losses_gains_data", [],
                    { branch: this.state.branch, date_from: this.state.dateFrom, date_to: this.state.dateTo }),
                this.orm.call("forecourt.dashboard", "get_stock_valuation", [],
                    { branch: this.state.branch }),
                this.orm.call("forecourt.dashboard", "get_trends_data", [],
                    { branch: this.state.branch, months: 6 }),
                this.orm.call("forecourt.dashboard", "get_fuel_detail_data", [],
                    { branch: this.state.branch, date_from: this.state.dateFrom, date_to: this.state.dateTo }),
            ]);
            this.state.data = { ...result, targets: targets.rows || [], profitability, losses, stock_val, trends, fuel_detail };
            this.state.lastRefresh = new Date().toLocaleTimeString();
            setTimeout(() => this._renderAllCharts(), 100);
        } catch(e) {
            console.error("Forecourt Dashboard load error", e);
        } finally {
            this.state.loading = false;
        }
    }

    _getChartJs() {
        return new Promise((resolve) => {
            if (window.Chart) { resolve(window.Chart); return; }
            const s = document.createElement("script");
            s.src = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js";
            s.onload = () => resolve(window.Chart);
            document.head.appendChild(s);
        });
    }

    /* ═══════════════════════════════════════════════════════════════
       THEME
       ═══════════════════════════════════════════════════════════════ */
    _theme(P) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            animation: { duration: 900, easing: "easeOutQuart" },
            interaction: { mode: "index", intersect: false },
            plugins: {
                legend: {
                    position: "bottom",
                    labels: {
                        boxWidth: 10, boxHeight: 10, padding: 14,
                        font: { size: 11, family: "system-ui, -apple-system" },
                        color: P.text, usePointStyle: true, pointStyle: "circle",
                    },
                },
                tooltip: {
                    backgroundColor: P.tooltipBg,
                    titleColor: "#FFFFFF",
                    bodyColor: "#FFFFFF",
                    padding: 12,
                    cornerRadius: 8,
                    borderColor: P.c5,
                    borderWidth: 1,
                    titleFont: { size: 12, weight: "600" },
                    bodyFont: { size: 12 },
                    boxPadding: 6,
                    callbacks: {
                        label: (ctx) => {
                            const val = ctx.parsed.y != null ? ctx.parsed.y : ctx.parsed;
                            return `  ${ctx.dataset.label}: ${Number(val).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
                        },
                    },
                },
            },
            scales: {
                x: {
                    grid: { display: false, drawBorder: false },
                    ticks: { font: { size: 11 }, color: P.text, padding: 6 },
                },
                y: {
                    grid: { color: P.grid, drawBorder: false },
                    ticks: { font: { size: 11 }, color: P.text, padding: 8 },
                    beginAtZero: true,
                },
            },
        };
    }

    _barGradient(context, color1, color2) {
        const chart = context.chart;
        const { ctx, chartArea } = chart;
        if (!chartArea) return color1;
        const g = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
        g.addColorStop(0, color1);
        g.addColorStop(1, color2);
        return g;
    }

    _bar(id, data, opts) {
        const canvas = document.getElementById(id);
        if (!canvas) return;
        if (this._charts[id]) this._charts[id].destroy();
        this._charts[id] = new Chart(canvas, { type: "bar", data, options: opts });
    }

    _doughnut(id, pct, color) {
        const canvas = document.getElementById(id);
        if (!canvas) return;
        if (this._charts[id]) this._charts[id].destroy();
        this._charts[id] = new Chart(canvas, {
            type: "doughnut",
            data: {
                datasets: [{
                    data: [pct, 100 - pct],
                    backgroundColor: [color, "rgba(148, 163, 184, 0.15)"],
                    borderWidth: 0,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: "74%",
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
            },
        });
    }

    _sparkline(id, data, color) {
        const canvas = document.getElementById(id);
        if (!canvas || !data || !data.length) return;
        if (this._charts[id]) this._charts[id].destroy();
        this._charts[id] = new Chart(canvas, {
            type: "line",
            data: {
                labels: data.map((_, i) => i),
                datasets: [{
                    data: data,
                    borderColor: color,
                    backgroundColor: _hexToRgba(color, 0.18),
                    borderWidth: 2,
                    tension: 0.45,
                    fill: true,
                    pointRadius: 0,
                    pointHoverRadius: 0,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 1200 },
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                scales: { x: { display: false }, y: { display: false } },
                layout: { padding: 0 },
            },
        });
    }

    /* ═══════════════════════════════════════════════════════════════
       RENDER ALL
       ═══════════════════════════════════════════════════════════════ */
    async _renderAllCharts() {
        const Chart = await this._getChartJs();
        const P = _getPalette();
        const d = this.state.data;
        const theme = this._theme(P);

        const trends = d.trends || {};
        const cashTrend = trends.cash || [];
        const varTrend = trends.variance || [];
        const stockVarTrend = trends.stock_var || [];

        const safeTrend = (arr) => arr && arr.length ? arr : [0, 0, 0, 0, 0, 0];

        this._sparkline("sparkCash", safeTrend(cashTrend), P.c1);
        this._sparkline("sparkCredit", safeTrend(cashTrend.map(v => v * 0.05)), P.c6);
        this._sparkline("sparkDeposits", safeTrend(cashTrend.map(v => v * 0.7)), P.c4);
        this._sparkline("sparkStockVar", safeTrend(stockVarTrend), P.warning);
        this._sparkline("sparkCashVar", safeTrend(varTrend), P.danger);

        const tab = this.state.activeTab;

        if (tab === "cash") {
            this._bar("chartCash", {
                labels: (d.cash_sales || []).map(r => r.branch),
                datasets: [
                    { label: "Petrol", data: (d.cash_sales || []).map(r => r.petrol_cash),
                      backgroundColor: (c) => this._barGradient(c, P.c1, _hexToRgba(P.c1, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Diesel", data: (d.cash_sales || []).map(r => r.diesel_cash),
                      backgroundColor: (c) => this._barGradient(c, P.c2, _hexToRgba(P.c2, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Lubes", data: (d.cash_sales || []).map(r => r.lubes),
                      backgroundColor: (c) => this._barGradient(c, P.c4, _hexToRgba(P.c4, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "LPG", data: (d.cash_sales || []).map(r => r.lpg),
                      backgroundColor: (c) => this._barGradient(c, P.c6, _hexToRgba(P.c6, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "POS/Card", data: (d.cash_sales || []).map(r => r.pos),
                      backgroundColor: (c) => this._barGradient(c, P.c8, _hexToRgba(P.c8, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                ],
            }, theme);
        }

        if (tab === "credit") {
            this._bar("chartCredit", {
                labels: (d.credit_sales || []).map(r => r.branch),
                datasets: [
                    { label: "Petrol (L)", data: (d.credit_sales || []).map(r => r.petrol_litres),
                      backgroundColor: (c) => this._barGradient(c, P.c1, _hexToRgba(P.c1, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Diesel (L)", data: (d.credit_sales || []).map(r => r.diesel_litres),
                      backgroundColor: (c) => this._barGradient(c, P.c4, _hexToRgba(P.c4, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                ],
            }, theme);
        }

        if (tab === "inventory") {
            const rows = (d.inventory || []).filter(r => r.product === "Petrol");
            this._bar("chartInventory", {
                labels: rows.map(r => r.branch),
                datasets: [
                    { label: "Opening", data: rows.map(r => r.opening), type: "bar",
                      backgroundColor: (c) => this._barGradient(c, P.c1, _hexToRgba(P.c1, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Received", data: rows.map(r => r.received), type: "bar",
                      backgroundColor: (c) => this._barGradient(c, P.c6, _hexToRgba(P.c6, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Sold", data: rows.map(r => r.sold), type: "bar",
                      backgroundColor: (c) => this._barGradient(c, P.c8, _hexToRgba(P.c8, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Closing", data: rows.map(r => r.closing), type: "line",
                      borderColor: P.c4, backgroundColor: _hexToRgba(P.c4, 0.15),
                      borderWidth: 3, tension: 0.4, fill: true,
                      pointRadius: 6, pointBackgroundColor: P.c4,
                      pointBorderColor: "#FFF", pointBorderWidth: 2 },
                ],
            }, theme);
        }

        if (tab === "deposits") {
            this._bar("chartDeposits", {
                labels: (d.deposits || []).map(r => r.branch),
                datasets: [
                    { label: "Day Shift", data: (d.deposits || []).map(r => r.day_fuel),
                      backgroundColor: (c) => this._barGradient(c, P.c1, _hexToRgba(P.c1, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Night Shift", data: (d.deposits || []).map(r => r.night_fuel),
                      backgroundColor: (c) => this._barGradient(c, P.c2, _hexToRgba(P.c2, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Lubes", data: (d.deposits || []).map(r => r.lubes),
                      backgroundColor: (c) => this._barGradient(c, P.c6, _hexToRgba(P.c6, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "LPG", data: (d.deposits || []).map(r => r.lpg),
                      backgroundColor: (c) => this._barGradient(c, P.c9, _hexToRgba(P.c9, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                ],
            }, theme);
        }

        if (tab === "variances") {
            this._bar("chartVariances", {
                labels: (d.variances || []).map(r => r.branch),
                datasets: [
                    { label: "Petrol Var (L)", data: (d.variances || []).map(r => r.full_petrol_var),
                      backgroundColor: (d.variances || []).map(r => r.full_petrol_var < 0 ? _hexToRgba(P.danger, 0.85) : _hexToRgba(P.success, 0.85)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Diesel Var (L)", data: (d.variances || []).map(r => r.full_diesel_var),
                      backgroundColor: (d.variances || []).map(r => r.full_diesel_var < 0 ? _hexToRgba(P.danger, 0.5) : _hexToRgba(P.success, 0.5)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Cash Var (K)", data: (d.variances || []).map(r => r.full_cash_var),
                      backgroundColor: _hexToRgba(P.c6, 0.7),
                      borderRadius: 6, borderSkipped: false },
                ],
            }, theme);
        }

        if (tab === "targets") {
            this._bar("chartTargets", {
                labels: (d.targets || []).map(r => r.branch),
                datasets: [
                    { label: "Petrol Target", data: (d.targets || []).map(r => r.petrol_target),
                      backgroundColor: _hexToRgba(P.c1, 0.2), borderRadius: 6, borderSkipped: false },
                    { label: "Petrol Actual", data: (d.targets || []).map(r => r.petrol_actual),
                      backgroundColor: (c) => this._barGradient(c, P.c1, _hexToRgba(P.c1, 0.5)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Diesel Target", data: (d.targets || []).map(r => r.diesel_target),
                      backgroundColor: _hexToRgba(P.c4, 0.2), borderRadius: 6, borderSkipped: false },
                    { label: "Diesel Actual", data: (d.targets || []).map(r => r.diesel_actual),
                      backgroundColor: (c) => this._barGradient(c, P.c4, _hexToRgba(P.c4, 0.5)),
                      borderRadius: 6, borderSkipped: false },
                ],
            }, theme);
        }

        if (tab === "profitability") {
            this._bar("chartProfitability", {
                labels: (d.profitability.rows || []).map(r => r.branch),
                datasets: [
                    { label: "Revenue", data: (d.profitability.rows || []).map(r => r.revenue),
                      backgroundColor: (c) => this._barGradient(c, P.c1, _hexToRgba(P.c1, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Cost", data: (d.profitability.rows || []).map(r => r.cost),
                      backgroundColor: (c) => this._barGradient(c, P.c8, _hexToRgba(P.c8, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Gross Profit", data: (d.profitability.rows || []).map(r => r.gross_profit),
                      backgroundColor: (c) => this._barGradient(c, P.success, _hexToRgba(P.success, 0.5)),
                      borderRadius: 6, borderSkipped: false },
                ],
            }, theme);
        }

        if (tab === "losses") {
            this._bar("chartLosses", {
                labels: (d.losses.rows || []).map(r => r.branch),
                datasets: [
                    { label: "Stock Var (K)", data: (d.losses.rows || []).map(r => r.stock_var_k),
                      backgroundColor: (d.losses.rows || []).map(r => r.stock_var_k < 0 ? _hexToRgba(P.danger, 0.85) : _hexToRgba(P.success, 0.85)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Cash Var (K)", data: (d.losses.rows || []).map(r => r.cash_var),
                      backgroundColor: (d.losses.rows || []).map(r => r.cash_var < 0 ? _hexToRgba(P.warning, 0.85) : _hexToRgba(P.success, 0.5)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Net (K)", data: (d.losses.rows || []).map(r => r.net), type: "line",
                      borderColor: P.c6, backgroundColor: _hexToRgba(P.c6, 0.15),
                      borderWidth: 3, tension: 0.4, fill: false,
                      pointRadius: 6, pointBackgroundColor: P.c6,
                      pointBorderColor: "#FFF", pointBorderWidth: 2 },
                ],
            }, theme);
        }

        if (tab === "stock_val") {
            this._bar("chartStockVal", {
                labels: (d.stock_val.rows || []).map(r => r.branch + " · " + r.product.split(" ")[0]),
                datasets: [
                    { label: "Cost Value", data: (d.stock_val.rows || []).map(r => r.cost_value),
                      backgroundColor: (c) => this._barGradient(c, P.c1, _hexToRgba(P.c1, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Pump Value", data: (d.stock_val.rows || []).map(r => r.pump_value),
                      backgroundColor: (c) => this._barGradient(c, P.c6, _hexToRgba(P.c6, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                ],
            }, theme);
        }

        if (tab === "trends") {
            const canvas = document.getElementById("chartTrends");
            if (canvas) {
                if (this._charts["chartTrends"]) this._charts["chartTrends"].destroy();
                const ctx = canvas.getContext("2d");
                const cashFill = ctx.createLinearGradient(0, 0, 0, 380);
                cashFill.addColorStop(0, _hexToRgba(P.c1, 0.35));
                cashFill.addColorStop(1, _hexToRgba(P.c1, 0.01));

                const varFill = ctx.createLinearGradient(0, 0, 0, 380);
                varFill.addColorStop(0, _hexToRgba(P.danger, 0.25));
                varFill.addColorStop(1, _hexToRgba(P.danger, 0.01));

                this._charts["chartTrends"] = new Chart(canvas, {
                    type: "line",
                    data: {
                        labels: (d.trends && d.trends.labels) || [],
                        datasets: [
                            { label: "Cash Sales (K)", data: (d.trends && d.trends.cash) || [],
                              borderColor: P.c1, backgroundColor: cashFill,
                              borderWidth: 3, tension: 0.4, fill: true,
                              pointRadius: 5, pointBackgroundColor: P.c1,
                              pointBorderColor: "#FFF", pointBorderWidth: 2, pointHoverRadius: 8 },
                            { label: "Cash Variance (K)", data: (d.trends && d.trends.variance) || [],
                              borderColor: P.danger, backgroundColor: varFill,
                              borderWidth: 2.5, tension: 0.4, fill: true,
                              pointRadius: 4, pointBackgroundColor: P.danger,
                              pointBorderColor: "#FFF", pointBorderWidth: 2, pointHoverRadius: 7 },
                            { label: "Stock Variance (K)", data: (d.trends && d.trends.stock_var) || [],
                              borderColor: P.c6, backgroundColor: "transparent",
                              borderWidth: 2.5, tension: 0.4, borderDash: [6, 6], fill: false,
                              pointRadius: 4, pointBackgroundColor: P.c6,
                              pointBorderColor: "#FFF", pointBorderWidth: 2, pointHoverRadius: 7 },
                        ],
                    },
                    options: {
                        ...theme,
                        interaction: { mode: "index", intersect: false },
                        scales: {
                            ...theme.scales,
                            x: { ...theme.scales.x, grid: { display: false } },
                            y: { ...theme.scales.y, grid: { color: P.grid } },
                        },
                    },
                });
            }
        }

        if (tab === "tanks") {
            this._bar("chartBranchStock", {
                labels: (d.branch_stock || []).map(bs => bs.branch),
                datasets: [
                    { label: "Current Stock", data: (d.branch_stock || []).map(bs => bs.total_current),
                      backgroundColor: (c) => this._barGradient(c, P.c1, _hexToRgba(P.c1, 0.4)),
                      borderRadius: 6, borderSkipped: false },
                    { label: "Total Capacity", data: (d.branch_stock || []).map(bs => bs.total_capacity),
                      backgroundColor: _hexToRgba(P.c2, 0.2), borderRadius: 6, borderSkipped: false },
                ],
            }, theme);
            (d.branch_stock || []).forEach((bs, bsi) => {
                (bs.tanks || []).forEach((tank, ti) => {
                    const pct = Math.min(parseFloat(tank.pct) || 0, 100);
                    const color = tank.status === "critical" ? P.danger : tank.status === "low" ? P.warning : P.success;
                    this._doughnut("chartTank" + bsi + "_" + ti, pct, color);
                });
            });
        }
    }
}

registry.category("actions").add("forecourt_dashboard", ForecourtDashboard);




