import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { apiRequest, notifyApiError, useNotify } from "./notifications.jsx";
import { formatSaleDate, formatSaleDateTime, formatSaleMoney } from "./salesFormat.js";

const SECTIONS = [
  ["sales_overview", "Sales overview", "/api/dashboard/sales-overview/"],
  ["payments", "Payments", "/api/dashboard/payments/"],
  ["returns", "Returns", "/api/dashboard/returns/"],
  ["inventory", "Inventory", "/api/dashboard/inventory/"],
  ["finished_goods", "Finished goods", "/api/dashboard/finished-goods/"],
  ["production", "Production", "/api/dashboard/production/"],
];
const REPORTS = [
  ["sales", "Sales report", "sales_overview"],
  ["inventory", "Inventory report", "inventory"],
  ["payments", "Pending payments report", "payments"],
  ["returns", "Returns report", "returns"],
  ["gst", "GST summary report", "sales_overview", "SUPER_ADMIN"],
];
const QUICK_RANGES = [
  ["today", "Today"],
  ["7_days", "7 days"],
  ["30_days", "30 days"],
  ["this_month", "This month"],
  ["this_year", "This year"],
];
const RANGE_LABELS = Object.fromEntries(QUICK_RANGES);
const STATUS_LABELS = {
  READY_TO_SHIP: "Ready to Ship",
  DELIVERED: "Delivered",
  PAYMENT_PENDING: "Payment Pending",
  PAYMENT_RECEIVED: "Payment Received",
  RETURNED: "Returned",
};

function rangeQuery(params) {
  const range = params.get("range") || "30_days";
  const query = new URLSearchParams({ range });
  if (range === "custom") {
    if (params.get("date_from")) query.set("date_from", params.get("date_from"));
    if (params.get("date_to")) query.set("date_to", params.get("date_to"));
  }
  return query.toString();
}

function formatRange(period) {
  if (!period) return "";
  return `${formatSaleDate(period.date_from)} – ${formatSaleDate(period.date_to)}`;
}

async function downloadReport(report, format, query, token) {
  let response;
  try {
    response = await fetch(`/api/dashboard/reports/${report}/?${query}&export_format=${format}`, {
      headers: { Authorization: `Token ${token}` },
    });
  } catch {
    throw new Error("Network connection failed. Check your internet and try again.");
  }
  if (!response.ok) {
    let message = `The server could not complete the request (${response.status}).`;
    try {
      const body = await response.json();
      message = body.detail || Object.values(body).flat().join(" ") || message;
    } catch {
      // Keep the readable status message when the response is not JSON.
    }
    throw new Error(String(message).replace(/<[^>]*>/g, " ").slice(0, 240));
  }
  return response;
}

export default function DashboardPage({ token, user }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const { changed } = useNotify();
  const [profile, setProfile] = useState(user);
  const [sections, setSections] = useState({});
  const [sectionErrors, setSectionErrors] = useState({});
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [report, setReport] = useState(null);
  const [reportError, setReportError] = useState("");
  const [reportLoading, setReportLoading] = useState(false);
  const [exporting, setExporting] = useState("");
  const [retry, setRetry] = useState(0);
  const range = searchParams.get("range") || "30_days";
  const dateFrom = searchParams.get("date_from") || "";
  const dateTo = searchParams.get("date_to") || "";
  const dateRangeReady = range !== "custom" || Boolean(dateFrom && dateTo);
  const query = useMemo(() => rangeQuery(searchParams), [searchParams]);
  const allowedSections = profile?.role === "SUPER_ADMIN"
    ? SECTIONS.map(([key]) => key)
    : profile?.dashboard_sections || [];
  const canExport = profile?.role === "SUPER_ADMIN" || profile?.can_export_dashboard_reports;
  const allSectionsFailed = !loading
    && allowedSections.length > 0
    && allowedSections.every((section) => sectionErrors[section]);

  const loadDashboard = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    setSectionErrors({});
    setReport(null);
    try {
      const currentProfile = await apiRequest("/api/auth/me/", token);
      setProfile(currentProfile);
      if (new URLSearchParams(query).get("range") === "custom"
        && (!new URLSearchParams(query).get("date_from") || !new URLSearchParams(query).get("date_to"))) {
        setSections({});
        return;
      }
      const permitted = SECTIONS.filter(([key]) => currentProfile.role === "SUPER_ADMIN"
        || currentProfile.dashboard_sections?.includes(key));
      const results = await Promise.allSettled(permitted.map(async ([key, , path]) => (
        [key, await apiRequest(`${path}?${query}`, token)]
      )));
      const next = {};
      const errors = {};
      results.forEach((result, index) => {
        const key = permitted[index][0];
        if (result.status === "fulfilled") next[key] = result.value[1];
        else errors[key] = dashboardErrorMessage(result.reason, "This section could not be loaded.");
      });
      setSections(next);
      setSectionErrors(errors);
    } catch (error) {
      setLoadError(dashboardErrorMessage(error, "Dashboard data could not be loaded."));
      setSections({});
    } finally {
      setLoading(false);
    }
  }, [query, retry, token]);

  useEffect(() => {
    loadDashboard();
  }, [loadDashboard]);

  function updatePeriod(nextRange, nextFrom = "", nextTo = "") {
    const next = new URLSearchParams(searchParams);
    next.set("range", nextRange);
    if (nextRange === "custom") {
      if (nextFrom) next.set("date_from", nextFrom);
      else next.delete("date_from");
      if (nextTo) next.set("date_to", nextTo);
      else next.delete("date_to");
    } else {
      next.delete("date_from");
      next.delete("date_to");
    }
    setSearchParams(next);
  }

  async function openReport(reportName, offset = 0) {
    setReportLoading(true);
    setReportError("");
    try {
      const result = await apiRequest(
        `/api/dashboard/reports/${reportName}/?${query}&limit=50&offset=${offset}`,
        token,
      );
      setReport(result);
    } catch (error) {
      setReport(null);
      setReportError(dashboardErrorMessage(error, "The report could not be loaded."));
    } finally {
      setReportLoading(false);
    }
  }

  async function exportReport(reportName, format) {
    const key = `${reportName}-${format}`;
    setExporting(key);
    try {
      const response = await downloadReport(reportName, format, query, token);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1]
        || `${reportName}-report.${format}`;
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      changed("dashboardReportExported", { name: REPORTS.find(([name]) => name === reportName)?.[1] || reportName, format: format.toUpperCase() });
    } catch (error) {
      notifyApiError(error.message);
    } finally {
      setExporting("");
    }
  }

  const currentPeriod = Object.values(sections).find((section) => section?.period)?.period;

  return (
    <section className="page-content dashboard-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow">TAMIRABOT / WORKSPACE</div>
          <h1>Dashboard</h1>
          <p className="page-subtitle">
            Welcome, {profile?.username || user?.username || "Admin"}. Review your sales, inventory and production at a glance.
          </p>
        </div>
        <button className="catalog-secondary-button" disabled={loading} onClick={() => setRetry((value) => value + 1)} type="button">
          {loading ? "Refreshing…" : "↻ Refresh"}
        </button>
      </div>

      <div className="dashboard-filter-panel">
        <div className="dashboard-quick-filters" aria-label="Dashboard date range">
          {QUICK_RANGES.map(([value, label]) => (
            <button
              aria-pressed={range === value}
              className={range === value ? "selected" : ""}
              key={value}
              onClick={() => updatePeriod(value)}
              type="button"
            >
              {label}
            </button>
          ))}
          <button
            aria-pressed={range === "custom"}
            className={range === "custom" ? "selected" : ""}
            onClick={() => updatePeriod("custom", dateFrom, dateTo)}
            type="button"
          >
            Custom range
          </button>
        </div>
        {range === "custom" && (
          <div className="dashboard-custom-range">
            <label>From<input max={dateTo || undefined} onChange={(event) => updatePeriod("custom", event.target.value, dateTo)} type="date" value={dateFrom} /></label>
            <label>To<input min={dateFrom || undefined} onChange={(event) => updatePeriod("custom", dateFrom, event.target.value)} type="date" value={dateTo} /></label>
          </div>
        )}
        {range === "custom" && !dateRangeReady && <p className="dashboard-range-hint">Choose both dates to load the dashboard and reports.</p>}
        <div className="dashboard-period-label">
          {currentPeriod ? formatRange(currentPeriod) : range === "custom" && !(dateFrom && dateTo) ? "Choose both dates to load custom range" : RANGE_LABELS[range] || "Custom range"}
        </div>
      </div>

      {loadError && (
        <div className="dashboard-error" role="alert">
          <span>{loadError}</span>
          <button onClick={() => setRetry((value) => value + 1)} type="button">Retry</button>
        </div>
      )}

      {!loading && !loadError && allowedSections.length === 0 && (
        <div className="dashboard-no-access">
          <div className="dashboard-no-access-heading">
            <span aria-hidden="true">▤</span>
            <div>
              <span className="eyebrow">DASHBOARD ACCESS</span>
              <h2>No sections are assigned to your account</h2>
              <p>
                {profile?.role === "SUPER_ADMIN"
                  ? "Your dashboard access could not be loaded. Refresh the page or check the dashboard deployment."
                  : "A Super Admin needs to assign dashboard sections before metrics and reports can be shown."}
              </p>
            </div>
          </div>
          <div className="dashboard-access-cards">
            {SECTIONS.map(([key, label]) => (
              <div className="dashboard-access-card is-locked" key={key}>
                <span aria-hidden="true">◌</span>
                <strong>{label}</strong>
                <small>Access not assigned</small>
              </div>
            ))}
          </div>
          {profile?.role === "SUPER_ADMIN" && (
            <Link className="catalog-primary-button dashboard-manage-access" to="/users">
              Manage Admin dashboard access
            </Link>
          )}
        </div>
      )}

      {allSectionsFailed && (
        <div className="dashboard-error dashboard-api-error" role="alert">
          <div>
            <strong>Dashboard data is unavailable.</strong>
            <p>Check your connection and retry. If the problem continues, ask your administrator to verify the dashboard API deployment.</p>
          </div>
          <button onClick={() => setRetry((value) => value + 1)} type="button">Retry</button>
        </div>
      )}

      {!loadError && allowedSections.includes("sales_overview") && (
        <SalesSection data={sections.sales_overview} loading={loading} error={sectionErrors.sales_overview} />
      )}
      {!loadError && allowedSections.includes("payments") && (
        <PaymentsSection data={sections.payments} loading={loading} error={sectionErrors.payments} />
      )}
      {!loadError && allowedSections.includes("returns") && (
        <ReturnsSection data={sections.returns} loading={loading} error={sectionErrors.returns} />
      )}
      {!loadError && (allowedSections.includes("inventory") || allowedSections.includes("finished_goods")) && (
        <InventorySection
          inventory={sections.inventory}
          finished={sections.finished_goods}
          inventoryAllowed={allowedSections.includes("inventory")}
          finishedAllowed={allowedSections.includes("finished_goods")}
          loading={loading}
          errors={sectionErrors}
        />
      )}
      {!loadError && allowedSections.includes("production") && (
        <ProductionSection data={sections.production} loading={loading} error={sectionErrors.production} />
      )}

      {!loadError && allowedSections.length > 0 && (
        <section className="dashboard-report-section">
          <div className="dashboard-section-heading"><div><span className="eyebrow">DOWNLOADS</span><h2>Reports</h2></div></div>
          <div className="dashboard-report-cards">
            {REPORTS.filter(([, , section, role]) => (
              allowedSections.includes(section)
              && (!role || profile?.role === role)
            )).map(([name, title]) => (
              <article className="dashboard-report-card" key={name}>
                <div><span className="dashboard-report-icon" aria-hidden="true">⇩</span><h3>{title}</h3></div>
                <div className="dashboard-report-actions">
                  <button className="catalog-secondary-button" disabled={reportLoading || !dateRangeReady} onClick={() => openReport(name)} type="button">Preview</button>
                  {canExport ? (
                    <>
                      <button className="catalog-primary-button" disabled={Boolean(exporting) || !dateRangeReady} onClick={() => exportReport(name, "xlsx")} type="button">
                        {exporting === `${name}-xlsx` ? "Preparing…" : "Excel"}
                      </button>
                      <button className="catalog-secondary-button" disabled={Boolean(exporting) || !dateRangeReady} onClick={() => exportReport(name, "pdf")} type="button">
                        {exporting === `${name}-pdf` ? "Preparing…" : "PDF"}
                      </button>
                    </>
                  ) : <small className="dashboard-export-note">Export access not assigned</small>}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}

      {reportError && <div className="dashboard-error" role="alert">{reportError}</div>}
      {report && (
        <section className="dashboard-report-preview">
          <div className="dashboard-section-heading">
            <div><span className="eyebrow">REPORT PREVIEW</span><h2>{REPORTS.find(([name]) => name === report.report)?.[1]}</h2></div>
            <button aria-label="Close report preview" className="catalog-secondary-button" onClick={() => setReport(null)} type="button">Close</button>
          </div>
          <p className="dashboard-report-meta">{report.total.toLocaleString("en-IN")} rows · {formatRange(report.period)}</p>
          <div className="dashboard-table-wrap">
            <table className="dashboard-table dashboard-report-table">
              <thead><tr>{report.columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
              <tbody>
                {report.rows.map((row, rowIndex) => (
                <tr key={`${report.offset}-${rowIndex}`}>{row.map((cell, cellIndex) => <td data-label={report.columns[cellIndex]} key={cellIndex}>{formatReportCell(cell, report.columns[cellIndex])}</td>)}</tr>
                ))}
                {!report.rows.length && <tr><td className="dashboard-empty-cell" colSpan={report.columns.length}>No records in this range.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="dashboard-pagination">
            <button className="catalog-secondary-button" disabled={report.offset <= 0 || reportLoading} onClick={() => openReport(report.report, Math.max(0, report.offset - report.limit))} type="button">Previous</button>
            <span>{report.total ? `${report.offset + 1}–${Math.min(report.total, report.offset + report.rows.length)} of ${report.total}` : "0 rows"}</span>
            <button className="catalog-secondary-button" disabled={report.offset + report.limit >= report.total || reportLoading} onClick={() => openReport(report.report, report.offset + report.limit)} type="button">Next</button>
          </div>
        </section>
      )}
      {reportLoading && <div className="dashboard-loading-note" role="status">Loading report…</div>}
    </section>
  );
}

function dashboardErrorMessage(error, fallback) {
  if (error?.status >= 500) return "The dashboard service is temporarily unavailable. Please retry.";
  const message = String(error?.message || fallback).replace(/<[^>]*>/g, " ").trim();
  return message || fallback;
}

function formatReportCell(value, column) {
  if (value === null || value === undefined || value === "") return "—";
  if (/^(date|sale date)$/i.test(column) && /^\d{4}-\d{2}-\d{2}$/.test(String(value))) {
    return formatSaleDate(value);
  }
  if (/status since/i.test(column)) return formatSaleDateTime(value);
  if (/revenue|amount|^cgst$|^sgst$|^igst$|gst$|total incl\./i.test(column)) {
    return typeof value === "string" && /^-?\d+(?:\.\d{1,2})?$/.test(value)
      ? formatSaleMoney(value)
      : value;
  }
  return value;
}

function SectionFrame({ title, eyebrow, loading, error, children }) {
  return (
    <section className="dashboard-section">
      <div className="dashboard-section-heading">
        <div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div>
      </div>
      {error ? <div className="dashboard-section-error" role="alert">{error}</div>
        : loading ? <div className="dashboard-skeleton-grid" aria-label={`Loading ${title}`}><i /><i /><i /></div>
          : children}
    </section>
  );
}

function SalesSection({ data, loading, error }) {
  const sales = data?.units_sold ?? 0;
  return (
    <SectionFrame title="Sales overview" eyebrow="SALES" loading={loading} error={error}>
      <div className="dashboard-metric-grid">
        <MetricCard label="Units sold" value={sales.toLocaleString("en-IN")} detail="Sale units in selected period" />
        <MetricCard
          label="Revenue excl. GST"
          value={formatSaleMoney(data?.net_taxable_value ?? 0)}
          detail={`${data?.legacy_sales_count || 0} legacy sale(s) included at recorded price`}
          accent
        />
        <MetricCard
          label="GST collected"
          value={formatSaleMoney(data?.net_total_tax ?? 0)}
          detail={`CGST ${formatSaleMoney(data?.net_cgst_amount ?? 0)} · SGST ${formatSaleMoney(data?.net_sgst_amount ?? 0)} · IGST ${formatSaleMoney(data?.net_igst_amount ?? 0)}`}
        />
        <MetricCard
          label="Gross total incl. GST"
          value={formatSaleMoney(data?.net_gross_total ?? 0)}
          detail={`Credit notes issued: ${formatSaleMoney(data?.credit_note_total ?? 0)}`}
          accent
        />
        <MetricCard label="Platforms" value={data?.sales_by_platform?.length ?? 0} detail="Platforms with sales" />
        <MetricCard label="Top products" value={data?.top_products?.length ?? 0} detail="Ranked by units sold" />
      </div>
      <div className="dashboard-chart-grid">
        <div className="dashboard-chart-card">
          <h3>Sales trend</h3>
          <TrendChart rows={data?.sales_trend || []} />
        </div>
        <div className="dashboard-chart-card">
          <h3>Sales by platform</h3>
          <HorizontalBars rows={(data?.sales_by_platform || []).map((row) => ({ label: row.platform, value: row.units_sold, extra: formatSaleMoney(row.revenue) }))} />
        </div>
      </div>
      <div className="dashboard-lower-grid">
        <div className="dashboard-list-card">
          <h3>Top-selling products</h3>
          <RankedList rows={(data?.top_products || []).map((row) => ({ label: `${row.product_number} · ${row.product_name}`, value: `${row.units_sold} units` }))} empty="No products sold in this period." />
        </div>
        <div className="dashboard-list-card">
          <h3>Sale status counts</h3>
          <StatusCounts counts={data?.status_counts || {}} />
        </div>
      </div>
    </SectionFrame>
  );
}

function PaymentsSection({ data, loading, error }) {
  return (
    <SectionFrame title="Payments" eyebrow="COLLECTIONS" loading={loading} error={error}>
      <div className="dashboard-metric-grid dashboard-metric-grid--three">
        <MetricCard label="Outstanding" value={formatSaleMoney(data?.outstanding_amount ?? 0)} detail={`${data?.pending_sales ?? 0} delivered or pending sale${data?.pending_sales === 1 ? "" : "s"}`} accent />
        <MetricCard label="Collected" value={formatSaleMoney(data?.collected_amount ?? 0)} detail="Payment Received sales; updates on refresh" />
        <MetricCard label="Over 7 days" value={(data?.unpaid_ageing?.from_7_to_30_days ?? 0) + (data?.unpaid_ageing?.over_30_days ?? 0)} detail="Delivered or pending payment" />
      </div>
      <div className="dashboard-financial-breakdown">
        <span>Gross incl. GST, less credits: <strong>{formatSaleMoney(data?.net_gross_total ?? 0)}</strong></span>
        <span>GST collected: <strong>{formatSaleMoney(data?.net_total_tax ?? 0)}</strong> (CGST {formatSaleMoney(data?.net_cgst_amount ?? 0)} · SGST {formatSaleMoney(data?.net_sgst_amount ?? 0)} · IGST {formatSaleMoney(data?.net_igst_amount ?? 0)})</span>
        <span>Credit notes incl. GST: <strong>{formatSaleMoney(data?.credit_note_total ?? 0)}</strong></span>
      </div>
      <div className="dashboard-list-card">
        <h3>Unpaid sale ageing</h3>
        <div className="dashboard-ageing-grid">
          <MetricCard label="Under 7 days" value={data?.unpaid_ageing?.under_7_days ?? 0} />
          <MetricCard label="7–30 days" value={data?.unpaid_ageing?.from_7_to_30_days ?? 0} />
          <MetricCard label="Over 30 days" value={data?.unpaid_ageing?.over_30_days ?? 0} />
        </div>
      </div>
    </SectionFrame>
  );
}

function ReturnsSection({ data, loading, error }) {
  const rate = Number(data?.return_rate || 0);
  return (
    <SectionFrame title="Returns" eyebrow="QUALITY" loading={loading} error={error}>
      <div className="dashboard-metric-grid dashboard-metric-grid--three">
        <MetricCard label="Units returned" value={(data?.returned_units ?? 0).toLocaleString("en-IN")} detail={`of ${(data?.units_sold ?? 0).toLocaleString("en-IN")} units sold`} />
        <MetricCard label="Return rate" value={`${(rate * 100).toFixed(1)}%`} detail="Returned units / units sold" accent />
        <MetricCard label="Return decisions" value={(data?.decisions || []).reduce((sum, item) => sum + item.units, 0)} detail="Recorded return cases" />
      </div>
      <div className="dashboard-financial-breakdown">
        <span>Credit notes issued in range: <strong>{formatSaleMoney(data?.credit_note_total ?? 0)} incl. GST</strong></span>
        <span>GST reversed: <strong>{formatSaleMoney(data?.credit_note_total_tax ?? 0)}</strong> (CGST {formatSaleMoney(data?.credit_note_cgst_amount ?? 0)} · SGST {formatSaleMoney(data?.credit_note_sgst_amount ?? 0)} · IGST {formatSaleMoney(data?.credit_note_igst_amount ?? 0)})</span>
        <span>Net gross incl. GST: <strong>{formatSaleMoney(data?.net_gross_total ?? 0)}</strong></span>
      </div>
      <div className="dashboard-lower-grid">
        <div className="dashboard-list-card"><h3>Return decisions</h3><RankedList rows={(data?.decisions || []).map((row) => ({ label: row.decision || "Pending inspection", value: row.units }))} empty="No returns in this period." /></div>
        <div className="dashboard-list-card"><h3>Top return reasons</h3><RankedList rows={(data?.top_return_reasons || []).map((row) => ({ label: row.reason, value: row.units }))} empty="No return reasons recorded." /></div>
      </div>
    </SectionFrame>
  );
}

function InventorySection({ inventory, finished, inventoryAllowed, finishedAllowed, loading, errors }) {
  return (
    <section className="dashboard-section">
      <div className="dashboard-section-heading"><div><span className="eyebrow">STOCK</span><h2>Inventory</h2></div></div>
      <div className="dashboard-lower-grid">
        {inventoryAllowed && (
          <div className="dashboard-list-card">
            <h3>Low-stock components</h3>
            {errors.inventory ? <div className="dashboard-section-error">{errors.inventory}</div>
              : loading ? <div className="dashboard-skeleton-grid"><i /><i /></div>
                : <div className="dashboard-table-wrap"><table className="dashboard-table dashboard-inventory-table"><thead><tr><th>Component</th><th>Available</th><th>Reorder at</th></tr></thead><tbody>
                  {(inventory?.low_stock_components || []).map((item) => <tr key={item.component_id}><td data-label="Component"><strong>{item.sku}</strong><small>{item.name}</small></td><td data-label="Available">{item.available} {item.unit}</td><td data-label="Reorder at">{item.reorder_level} {item.unit}</td></tr>)}
                  {!inventory?.low_stock_components?.length && <tr><td className="dashboard-empty-cell" colSpan="3">No low-stock components.</td></tr>}
                </tbody></table></div>}
          </div>
        )}
        {finishedAllowed && (
          <div className="dashboard-list-card">
            <h3>Finished goods in stock</h3>
            {errors.finished_goods ? <div className="dashboard-section-error">{errors.finished_goods}</div>
              : loading ? <div className="dashboard-skeleton-grid"><i /><i /></div>
                : <div className="dashboard-table-wrap"><table className="dashboard-table dashboard-finished-table"><thead><tr><th>Product</th><th>Units</th></tr></thead><tbody>
                  {(finished?.products || []).map((item) => <tr key={item.product_id}><td data-label="Product"><strong>{item.product__product_number}</strong><small>{item.product__name}</small></td><td data-label="Units">{item.units_in_stock}</td></tr>)}
                  {!finished?.products?.length && <tr><td className="dashboard-empty-cell" colSpan="2">No finished units in stock.</td></tr>}
                </tbody></table></div>}
          </div>
        )}
      </div>
    </section>
  );
}

function ProductionSection({ data, loading, error }) {
  return (
    <SectionFrame title="Open work orders" eyebrow="PRODUCTION" loading={loading} error={error}>
      <div className="dashboard-priority-grid">
        {Object.entries(data?.open_by_priority || {}).map(([priority, count]) => (
          <MetricCard key={priority} label={`${priority[0]}${priority.slice(1).toLowerCase()} priority`} value={count} />
        ))}
        <MetricCard label="Due in 7 days" value={data?.due_in_7_days ?? 0} detail="Open work orders" accent />
      </div>
      <div className="dashboard-list-card">
        <h3>Upcoming work orders</h3>
        <div className="dashboard-table-wrap"><table className="dashboard-table dashboard-production-table"><thead><tr><th>Work order</th><th>Product</th><th>Priority</th><th>Status</th><th>Due</th><th>Progress</th></tr></thead><tbody>
          {(data?.open_work_orders || []).map((order) => <tr key={order.id}><td data-label="Work order">{order.work_order}</td><td data-label="Product"><strong>{order.product_number}</strong><small>{order.product_name}</small></td><td data-label="Priority">{order.priority}</td><td data-label="Status">{order.status.replaceAll("_", " ")}</td><td data-label="Due">{formatSaleDate(order.due_date)}</td><td data-label="Progress">{order.packed_quantity} / {order.quantity}</td></tr>)}
          {!data?.open_work_orders?.length && <tr><td className="dashboard-empty-cell" colSpan="6">No open work orders.</td></tr>}
        </tbody></table></div>
      </div>
    </SectionFrame>
  );
}

function MetricCard({ label, value, detail, accent = false }) {
  return <div className={`dashboard-metric-card ${accent ? "accent" : ""}`}><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>;
}

function StatusCounts({ counts }) {
  return <ul className="dashboard-status-list">{Object.entries(STATUS_LABELS).map(([key, label]) => <li key={key}><span className={`dashboard-status-dot status-${key.toLowerCase()}`} /><span>{label}</span><strong>{(counts[key] ?? 0).toLocaleString("en-IN")}</strong></li>)}</ul>;
}

function RankedList({ rows, empty }) {
  if (!rows.length) return <p className="dashboard-empty-message">{empty}</p>;
  return <ol className="dashboard-ranked-list">{rows.map((row, index) => <li key={`${row.label}-${index}`}><span className="dashboard-rank">{index + 1}</span><span className="dashboard-ranked-label">{row.label}<small>{row.detail}</small></span><strong>{row.value}</strong></li>)}</ol>;
}

function HorizontalBars({ rows }) {
  const max = Math.max(1, ...rows.map((row) => Number(row.value) || 0));
  if (!rows.length) return <p className="dashboard-empty-message">No platform sales in this period.</p>;
  return <div className="dashboard-bars">{rows.map((row) => <div className="dashboard-bar-row" key={row.label}><div className="dashboard-bar-label"><span>{row.label}</span><strong>{row.value}</strong></div><div className="dashboard-bar-track"><i style={{ width: `${Math.max(3, (Number(row.value) / max) * 100)}%` }} /></div><small>{row.extra}</small></div>)}</div>;
}

function TrendChart({ rows }) {
  if (!rows.length) return <p className="dashboard-empty-message">No sales trend in this period.</p>;
  const width = 620;
  const height = 190;
  const padding = 22;
  const max = Math.max(1, ...rows.map((row) => Number(row.units_sold) || 0));
  const points = rows.map((row, index) => ({
    x: rows.length === 1 ? width / 2 : padding + (index * (width - padding * 2)) / (rows.length - 1),
    y: height - padding - ((Number(row.units_sold) || 0) / max) * (height - padding * 2),
    ...row,
  }));
  return (
    <div className="dashboard-trend-wrap">
      <svg aria-label="Units sold over time" className="dashboard-trend" role="img" viewBox={`0 0 ${width} ${height}`}>
        {[0, 1, 2, 3].map((line) => <line key={line} x1={padding} x2={width - padding} y1={padding + line * ((height - padding * 2) / 3)} y2={padding + line * ((height - padding * 2) / 3)} />)}
        <polyline points={points.map((point) => `${point.x},${point.y}`).join(" ")} />
        {points.map((point) => <circle key={point.date} cx={point.x} cy={point.y} r="4"><title>{formatSaleDate(point.date)}: {point.units_sold} units</title></circle>)}
      </svg>
      <div className="dashboard-trend-axis"><span>{formatSaleDate(points[0].date)}</span><span>{formatSaleDate(points.at(-1).date)}</span></div>
      <div className="dashboard-trend-total">{rows.reduce((sum, row) => sum + Number(row.units_sold || 0), 0).toLocaleString("en-IN")} units · revenue {formatSaleMoney(rows.reduce((sum, row) => sum + Number(row.revenue || 0), 0))}</div>
    </div>
  );
}
