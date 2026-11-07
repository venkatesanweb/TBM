import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import JsBarcode from "jsbarcode";
import CameraScanner from "./CameraScanner.jsx";
import BarcodeScanInput from "./BarcodeScanInput.jsx";
import { apiRequest, useNotify } from "./notifications.jsx";
import { SaleAge, SaleProgress, StatusChip, formatSaleDate, formatSaleDateTime, formatSaleMoney, saleStatusLabels as uiSaleStatusLabels } from "./SalesUi.jsx";
import { saleNeedsAttention, salesTodayISODate } from "./salesFormat.js";
import { UnsavedChangesDialog, useUnsavedChanges } from "./useUnsavedChanges.jsx";

async function request(path, token, options = {}) {
  return apiRequest(path, token, options);
}

async function barcodeSvgToPng(svg) {
  const source = new XMLSerializer().serializeToString(svg);
  const url = URL.createObjectURL(
    new Blob([source], { type: "image/svg+xml;charset=utf-8" }),
  );
  try {
    const image = new Image();
    await new Promise((resolve, reject) => {
      image.onload = resolve;
      image.onerror = () => reject(new Error("A Code128 barcode could not be prepared for PDF."));
      image.src = url;
    });
    const canvas = document.createElement("canvas");
    canvas.width = image.naturalWidth;
    canvas.height = image.naturalHeight;
    const context = canvas.getContext("2d");
    if (!context || !canvas.width || !canvas.height) {
      throw new Error("A Code128 barcode could not be prepared for PDF.");
    }
    context.drawImage(image, 0, 0);
    return canvas.toDataURL("image/png");
  } finally {
    URL.revokeObjectURL(url);
  }
}

function localDate() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

const saleStatusLabels = uiSaleStatusLabels;

const nextSaleStatus = {
  READY_TO_SHIP: "DELIVERED",
  DELIVERED: "PAYMENT_PENDING",
  PAYMENT_PENDING: "PAYMENT_RECEIVED",
};

function SalePageError({ children }) {
  return children ? <div className="form-error" role="alert">{children}</div> : null;
}

function SaleMoneySummary({ sale }) {
  if (sale.price === undefined) return null;
  const pricedUnits = sale.units.filter((unit) => unit.total_amount !== null);
  return <section className="sales-money-summary" aria-label="Sale pricing and payment">
    <div className="sales-money-total">
      <div><span>Payment status</span><strong>{saleStatusLabels[sale.status]}</strong></div>
      <div><span>Amount due</span><strong>{formatSaleMoney(sale.price)}</strong></div>
      {sale.status === "PAYMENT_RECEIVED" && <div><span>Payment received on</span><strong>{formatSaleDateTime(sale.payment_received_at)}</strong></div>}
    </div>
    {pricedUnits.length > 0 ? <div className="table-wrap">
      <table>
        <thead><tr><th>Unit / product</th><th>HSN / GST</th><th>Price (incl. tax)</th><th>Taxable value</th><th>CGST</th><th>SGST</th><th>IGST</th><th>Total tax</th><th>Total</th><th>MRP</th></tr></thead>
        <tbody>{pricedUnits.map((unit) => <tr key={unit.id}>
          <td>{unit.serial_number}<small>{unit.product_number} · {unit.product_name}</small></td>
          <td>{unit.hsn_code_snapshot} / {unit.gst_rate_snapshot}%</td>
          <td>{formatSaleMoney(unit.unit_selling_price_incl_tax)}</td>
          <td>{formatSaleMoney(unit.taxable_value)}</td>
          <td>{formatSaleMoney(unit.cgst_amount)}</td>
          <td>{formatSaleMoney(unit.sgst_amount)}</td>
          <td>{formatSaleMoney(unit.igst_amount)}</td>
          <td>{formatSaleMoney(unit.total_tax)}</td>
          <td>{formatSaleMoney(unit.total_amount)}</td>
          <td>{formatSaleMoney(unit.mrp_snapshot)}</td>
        </tr>)}</tbody>
      </table>
    </div> : <p className="muted">Legacy sale pricing is preserved and was not recalculated.</p>}
  </section>;
}

function SaleCustomerSummary({ sale }) {
  if (!sale.customer_name && !sale.customer_phone && !sale.billing_address && !sale.delivery_address) {
    return null;
  }
  return <section className="sales-customer-summary" aria-label="Customer and delivery details">
    <h3>Customer and delivery details</h3>
    <div><span>Customer</span><strong>{sale.customer_name || "—"}</strong></div>
    <div><span>Phone</span><strong>{sale.customer_phone || "—"}</strong></div>
    <div><span>Delivery state</span><strong>{[sale.shipping_state, sale.customer_state_code].filter(Boolean).join(" · ") || "—"}</strong></div>
    <div><span>GSTIN</span><strong>{sale.customer_gstin || "—"}</strong></div>
    <div><span>Billing address</span><strong>{sale.billing_address || "—"}</strong></div>
    <div><span>Delivery address</span><strong>{sale.delivery_address || "—"}</strong></div>
  </section>;
}

export function SalesPage({ token, user }) {
  const [sales, setSales] = useState([]);
  const [platforms, setPlatforms] = useState([]);
  const [products, setProducts] = useState([]);
  const [searchParams, setSearchParams] = useSearchParams();
  const [filters, setFilters] = useState(() => Object.fromEntries(
    ["status", "platform", "product", "date_from", "date_to"]
      .map((key) => [key, searchParams.get(key) || ""]),
  ));
  const [quickFilter, setQuickFilter] = useState(() => searchParams.get("quick") || "");
  const [searchText, setSearchText] = useState(() => searchParams.get("q") || "");
  const [debouncedSearch, setDebouncedSearch] = useState(() => searchParams.get("q") || "");
  const [page, setPage] = useState(() => Math.max(1, Number(searchParams.get("page") || 1)));
  const [selectedSale, setSelectedSale] = useState(null);
  const [bulkMode, setBulkMode] = useState(false);
  const [scanValue, setScanValue] = useState("");
  const [bulkScanValue, setBulkScanValue] = useState("");
  const [bulkScans, setBulkScans] = useState([]);
  const [bulkStatus, setBulkStatus] = useState("DELIVERED");
  const [bulkNote, setBulkNote] = useState("");
  const [bulkConfirming, setBulkConfirming] = useState(false);
  const [bulkResults, setBulkResults] = useState([]);
  const bulkSerials = useRef(new Set());
  const [rowStatusSale, setRowStatusSale] = useState(null);
  const [rowStatusNote, setRowStatusNote] = useState("");
  const [rowStatusError, setRowStatusError] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const bulkRef = useRef(null);
  const selectedSaleId = useRef(null);
  const { notify } = useNotify();
  const bulkGuard = useUnsavedChanges(bulkRef, () => ({ bulkScans, bulkNote }), closeSelectedSale);

  const load = useCallback(() => {
    setLoading(true);
    setError("");
    const params = new URLSearchParams(
      Object.entries(filters).filter(([, value]) => value),
    );
    return Promise.all([
      request(`/api/sales/?${params}`, token),
      request("/api/platforms/", token),
      request("/api/products/", token),
    ])
      .then(([saleRows, platformRows, productRows]) => {
        setSales(saleRows);
        setPlatforms(platformRows);
        setProducts(productRows);
      })
      .catch(() => setError("Sales could not be loaded. Check your connection and retry."))
      .finally(() => setLoading(false));
  }, [filters, token]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const urlFilters = Object.fromEntries(
      ["status", "platform", "product", "date_from", "date_to"]
        .map((key) => [key, searchParams.get(key) || ""]),
    );
    setFilters((current) => Object.keys(urlFilters).every((key) => current[key] === urlFilters[key])
      ? current
      : urlFilters);
    const urlQuickFilter = searchParams.get("quick") || "";
    setQuickFilter((current) => current === urlQuickFilter ? current : urlQuickFilter);
    const urlSearchText = searchParams.get("q") || "";
    setSearchText((current) => current === urlSearchText ? current : urlSearchText);
    const urlPage = Math.max(1, Number(searchParams.get("page") || 1));
    setPage((current) => current === urlPage ? current : urlPage);
    const saleId = searchParams.get("sale");
    if (!saleId) {
      selectedSaleId.current = null;
      setSelectedSale(null);
      return;
    }
    if (selectedSaleId.current === saleId) return;
    request(`/api/sales/${encodeURIComponent(saleId)}/`, token)
      .then((sale) => {
        selectedSaleId.current = saleId;
        setSelectedSale(sale);
      })
      .catch(() => {
        selectedSaleId.current = null;
        setSelectedSale(null);
      });
  }, [searchParams, token]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(searchText.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [searchText]);

  useEffect(() => {
    const query = searchText.trim();
    if ((searchParams.get("q") || "") === query) return;
    const params = new URLSearchParams(searchParams);
    if (query) params.set("q", query);
    else params.delete("q");
    params.delete("page");
    setPage(1);
    setSearchParams(params, { replace: true });
  }, [searchText, searchParams, setSearchParams]);

  useEffect(() => {
    bulkGuard.updateDirty();
  }, [bulkGuard.updateDirty, bulkScans, bulkNote]);
  useEffect(() => {
    bulkSerials.current = new Set(bulkScans.map((item) => item.serial_number.toLowerCase()));
  }, [bulkScans]);

  function writeUrl(nextFilters, nextQuick = quickFilter, nextPage = 1, saleId = searchParams.get("sale"), replace = false, nextSearch = searchText) {
    const params = new URLSearchParams();
    Object.entries(nextFilters).forEach(([key, value]) => { if (value) params.set(key, value); });
    if (nextQuick) params.set("quick", nextQuick);
    if (nextPage > 1) params.set("page", String(nextPage));
    if (nextSearch.trim()) params.set("q", nextSearch.trim());
    if (saleId) params.set("sale", String(saleId));
    setSearchParams(params, { replace });
  }

  function updateFilters(key, value) {
    const next = { ...filters, [key]: value };
    setFilters(next);
    setPage(1);
    setQuickFilter("");
    writeUrl(next, "", 1);
  }

  function selectQuickFilter(name) {
    const next = { ...filters };
    if (name === "pending") next.status = "PAYMENT_PENDING";
    else if (name === "returned") next.status = "RETURNED";
    else if (name === "today") {
      next.date_from = salesTodayISODate();
      next.date_to = salesTodayISODate();
    } else if (name === "week") {
      const today = salesTodayISODate();
      const [year, month, dayOfMonth] = today.split("-").map(Number);
      const kolkataDay = new Date(`${today}T12:00:00+05:30`).getUTCDay();
      const daysSinceMonday = (kolkataDay + 6) % 7;
      const monday = new Date(Date.UTC(year, month - 1, dayOfMonth - daysSinceMonday));
      next.date_from = `${monday.getUTCFullYear()}-${String(monday.getUTCMonth() + 1).padStart(2, "0")}-${String(monday.getUTCDate()).padStart(2, "0")}`;
      next.date_to = today;
    } else if (name === "attention") {
      next.status = "";
      next.date_from = "";
      next.date_to = "";
    }
    setFilters(next);
    setQuickFilter(name);
    setPage(1);
    writeUrl(next, name, 1);
  }

  function showSale(sale) {
    selectedSaleId.current = String(sale.id);
    setSelectedSale(sale);
    writeUrl(filters, quickFilter, page, sale.id);
  }

  function closeSelectedSale() {
    selectedSaleId.current = null;
    setSelectedSale(null);
    writeUrl(filters, quickFilter, page, null, true);
  }

  const openScannedSale = useCallback(async (value) => {
    const serialNumber = value.trim();
    if (!serialNumber) return false;
    setError("");
    try {
      const sale = await request(
        `/api/sales/scan/?serial_number=${encodeURIComponent(serialNumber)}`,
        token,
      );
      showSale(sale);
      setScanValue("");
      return true;
    } catch (failure) {
      if (!failure.status) {
        setError("Network connection failed. Check your connection and retry the scan.");
        return false;
      }
      if (failure.status !== 404) {
        setError(failure.message || "The sale lookup failed. Retry the scan.");
        return false;
      }
      try {
        const unit = await request(
          `/api/finished-units/lookup/?serial_number=${encodeURIComponent(serialNumber)}`,
          token,
        );
        if (unit.status === "IN_STOCK") {
          setError(<span>This unit has not been sold yet. <StatusChip status={unit.status} /> <Link to={`/finished-goods/${unit.id}`}>Open unit</Link></span>);
        } else {
          setError(<span>This unit has not been sold yet. <StatusChip status={unit.status} /></span>);
        }
      } catch (unitFailure) {
        if (!unitFailure.status) {
          setError("Network connection failed. Check your connection and retry the scan.");
        } else if (unitFailure.status === 404) {
          setError("No sale found for this serial.");
        } else {
          setError(unitFailure.message || "The unit lookup failed. Retry the scan.");
        }
      }
      return false;
    }
  }, [token, filters, quickFilter, page, searchParams]);

  const addBulkScan = useCallback(async (value) => {
    const serialNumber = value.trim();
    if (!serialNumber) return;
    if (bulkSerials.current.has(serialNumber.toLowerCase())) {
      notify(`${serialNumber} is already in the list.`, "error");
      setBulkScanValue("");
      return false;
    }
    bulkSerials.current.add(serialNumber.toLowerCase());
    setError("");
    try {
      const sale = await request(
        `/api/sales/scan/?serial_number=${encodeURIComponent(serialNumber)}`,
        token,
      );
      const unit = sale.units.find((item) => item.serial_number.toLowerCase() === serialNumber.toLowerCase());
      if (!unit) {
        throw new Error("The scanned serial was not found on its sale.");
      }
      setBulkScans((current) => [...current, {
        serial_number: unit.serial_number,
        sale_id: sale.id,
        sale_status: sale.status,
        platform_name: sale.platform_name,
        product_name: `${unit.product_number} · ${unit.product_name}`,
      }]);
      setBulkScanValue("");
      return true;
    } catch (failure) {
      setBulkScans((current) => [...current, {
        serial_number: serialNumber,
        sale_id: null,
        sale_status: "",
        platform_name: "",
        product_name: "",
        failure: failure.message || "No sale found for this serial.",
      }]);
      setBulkScanValue("");
      return false;
    }
  }, [bulkScans, token, notify]);

  async function updateBulkStatus() {
    setBusy(true);
    const results = [];
    const validRows = bulkScans.filter((item) => item.sale_id);
    const groups = new Map();
    validRows.forEach((item) => {
      const group = groups.get(item.sale_id) || [];
      group.push(item);
      groups.set(item.sale_id, group);
    });
    try {
      bulkScans.filter((item) => item.failure).forEach((item) => {
        results.push({ ...item, success: false, reason: item.failure });
      });
      for (const [saleId, items] of groups) {
        const previous = items[0].sale_status;
        try {
          await request(`/api/sales/${saleId}/status/`, token, {
            method: "POST",
            body: JSON.stringify({ status: bulkStatus, note: bulkNote }),
          });
          items.forEach((item) => {
            results.push({ ...item, success: true, reason: "" });
            notify(`Sale SALE-${saleId} (${item.product_name}, ${item.serial_number}): ${saleStatusLabels[previous]} -> ${saleStatusLabels[bulkStatus]}`, "success");
          });
        } catch (failure) {
          items.forEach((item) => results.push({ ...item, success: false, reason: failure.message }));
        }
      }
      const successfulSerials = new Set(results.filter((item) => item.success).map((item) => item.serial_number));
      setBulkScans((current) => current.filter((item) => !successfulSerials.has(item.serial_number)));
      setBulkResults(results);
      setBulkConfirming(false);
      setBulkNote("");
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function applyRowStatus() {
    if (!rowStatusSale) return;
    const target = nextSaleStatus[rowStatusSale.status];
    if (!target) return;
    try {
      await request(`/api/sales/${rowStatusSale.id}/status/`, token, {
        method: "POST",
        body: JSON.stringify({ status: target, note: rowStatusNote }),
      });
      notify(`Sale SALE-${rowStatusSale.id} (${rowStatusSale.units[0]?.product_name || "product"}, ${rowStatusSale.units[0]?.serial_number || "unit"}): ${saleStatusLabels[rowStatusSale.status]} -> ${saleStatusLabels[target]}`, "success");
      setRowStatusSale(null);
      setRowStatusNote("");
      setRowStatusError("");
      await load();
    } catch (failure) {
      setRowStatusError(failure.message);
    }
  }

  const visibleSales = useMemo(() => {
    const query = debouncedSearch.toLowerCase();
    const rows = quickFilter === "attention"
      ? sales.filter((sale) => saleNeedsAttention(sale))
      : sales;
    return query ? rows.filter((sale) => [
      sale.order_reference,
      sale.platform_name,
      ...sale.units.flatMap((unit) => [unit.serial_number, unit.product_number, unit.product_name, unit.product_sku]),
    ].some((value) => String(value || "").toLowerCase().includes(query))) : rows;
  }, [debouncedSearch, quickFilter, sales]);
  const hasActiveFilters = Boolean(searchText.trim() || quickFilter || Object.values(filters).some(Boolean));
  const pageSize = 25;
  const pageCount = Math.max(1, Math.ceil(visibleSales.length / pageSize));
  const pageSales = visibleSales.slice((page - 1) * pageSize, page * pageSize);
  const canViewMoney = user?.role === "SUPER_ADMIN"
    || (user?.dashboard_sections || []).some((section) => ["sales_overview", "payments", "returns"].includes(section));

  const refreshSelectedSale = useCallback(async (saleId) => {
    await load();
    const updated = await request(`/api/sales/${saleId}/`, token);
    setSelectedSale(updated);
    return updated;
  }, [load, token]);

  return (
    <section className="page-content sales-page">
      <div className="page-heading">
        <div><div className="eyebrow">TAMIRABOT / SALES TRACKING</div><h1>Sales</h1><p className="page-subtitle">Create sales from finished goods and prepare orders to ship.</p></div>
        <div className="sales-heading-actions">
          {user.role === "SUPER_ADMIN" && <Link className="secondary-button" to="/sales/platforms">Selling platforms</Link>}
          <Link className="primary-button" to="/sales/new">New sale</Link>
        </div>
      </div>
      <SalePageError>{error}</SalePageError>
      {error && !loading && <button className="catalog-secondary-button sales-retry-button" onClick={load} type="button">Retry loading sales</button>}
      <section className="panel sales-scan-panel">
        <div className="panel-heading"><div><h2>{bulkMode ? "Bulk status scan" : "Scan-first sales lookup"}</h2><p>{bulkMode ? "Scan serials here to add them to the bulk update list below." : "Scan or type a finished-unit serial to open its sale in this page."}</p></div><button className="catalog-secondary-button" onClick={() => setBulkMode((current) => !current)} type="button">{bulkMode ? "Exit bulk mode" : "Bulk update mode"}</button></div>
        <BarcodeScanInput value={bulkMode ? bulkScanValue : scanValue} onChange={(event) => (bulkMode ? setBulkScanValue(event.target.value) : setScanValue(event.target.value))} onScan={bulkMode ? addBulkScan : openScannedSale} continuousCamera={bulkMode} placeholder={bulkMode ? "Scan serial for bulk update" : "Scan or type serial number"} autoFocus flashOnSuccess />
      </section>
      <section className="panel sales-bulk-panel" ref={bulkRef}>
        <div className="panel-heading"><div><h2>Bulk status update</h2><p>{bulkMode ? "Scan serials using the box above." : "Choose Bulk update mode above, then scan several sold-unit serials."}</p></div></div>
        {bulkScans.length > 0 && <>
          <div className="sales-bulk-counter">{bulkScans.length} unit{bulkScans.length === 1 ? "" : "s"} scanned</div>
          <div className="sales-selected-units">{bulkScans.map((item) => <div className="sales-selected-row" key={item.serial_number}>
            <span><strong>{item.serial_number}</strong><small>{item.product_name || item.failure || `SALE-${item.sale_id}`} · {item.sale_status ? saleStatusLabels[item.sale_status] : "No sale found"}</small></span>
            {item.sale_status && <StatusChip status={item.sale_status} />}
            <button className="catalog-text-link" onClick={() => { bulkSerials.current.delete(item.serial_number.toLowerCase()); setBulkScans((current) => current.filter((entry) => entry.serial_number !== item.serial_number)); }} type="button">Remove</button>
          </div>)}</div>
          <div className="sales-bulk-controls">
            <label>Move selected sales to<select value={bulkStatus} onChange={(event) => setBulkStatus(event.target.value)}><option value="DELIVERED">Delivered</option><option value="PAYMENT_PENDING">Payment Pending</option><option value="PAYMENT_RECEIVED">Payment Received</option></select></label>
            <label>Note<input value={bulkNote} onChange={(event) => setBulkNote(event.target.value)} placeholder="Optional status note" /></label>
            <button className="catalog-primary-button" disabled={busy} onClick={() => setBulkConfirming(true)} type="button">Apply to {bulkScans.length} units</button>
          </div>
        </>}
        {bulkResults.length > 0 && <div className="table-wrap sales-bulk-results"><table><thead><tr><th>Serial</th><th>Result</th><th>Reason</th></tr></thead><tbody>{bulkResults.map((item) => <tr key={`${item.serial_number}-${item.success}`}><td>{item.serial_number}</td><td>{item.success ? "Succeeded" : "Failed"}</td><td>{item.reason || "Status updated."}</td></tr>)}</tbody></table></div>}
      </section>
      <div className="sales-overview-grid">
        <section className="panel sales-overview-card"><span>Sales</span><strong>{sales.length}</strong></section>
        <section className="panel sales-overview-card"><span>Ready to Ship</span><strong>{sales.filter((sale) => sale.status === "READY_TO_SHIP").length}</strong></section>
        <section className="panel sales-overview-card"><span>Active platforms</span><strong>{platforms.filter((platform) => platform.active).length}</strong></section>
      </div>
      <section className="panel sales-register">
        <div className="panel-heading"><div><h2>Sale register</h2><p>Filter and review each sale, its finished units, and current status.</p></div></div>
        <div className="sales-list-filters">
          <label className="sales-search-label">Search<input type="search" value={searchText} onChange={(event) => setSearchText(event.target.value)} placeholder="Serial, order, platform, product" /></label>
          <label>Status<select value={filters.status} onChange={(event) => updateFilters("status", event.target.value)}><option value="">All statuses</option>{Object.entries(saleStatusLabels).filter(([key]) => ["READY_TO_SHIP", "DELIVERED", "PAYMENT_PENDING", "PAYMENT_RECEIVED", "RETURNED"].includes(key)).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
          <label>Platform<select value={filters.platform} onChange={(event) => updateFilters("platform", event.target.value)}><option value="">All platforms</option>{platforms.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
          <label>Product<select value={filters.product} onChange={(event) => updateFilters("product", event.target.value)}><option value="">All products</option>{products.map((item) => <option value={item.id} key={item.id}>{item.product_number} · {item.name}</option>)}</select></label>
          <label>From<input type="date" value={filters.date_from} onChange={(event) => updateFilters("date_from", event.target.value)} /></label>
          <label>To<input type="date" value={filters.date_to} onChange={(event) => updateFilters("date_to", event.target.value)} /></label>
          <button className="catalog-secondary-button" onClick={() => { const clean = { status: "", platform: "", product: "", date_from: "", date_to: "" }; setFilters(clean); setQuickFilter(""); setSearchText(""); setPage(1); writeUrl(clean, "", 1, searchParams.get("sale"), false, ""); }} type="button">Clear all</button>
        </div>
        <div className="sales-quick-filters">{[["today", "Today"], ["week", "This week"], ["pending", "Pending payment"], ["returned", "Returned"], ["attention", "Needs attention"]].map(([key, label]) => <button className={quickFilter === key ? "active" : ""} key={key} onClick={() => selectQuickFilter(key)} type="button">{label}{key === "attention" ? ` (${sales.filter((sale) => saleNeedsAttention(sale)).length})` : ""}</button>)}</div>
        <p className="sales-results-count">{visibleSales.length} sale{visibleSales.length === 1 ? "" : "s"}</p>
        {loading ? <div className="sales-skeleton-list">{[1, 2, 3].map((row) => <div key={row} />)}</div> : visibleSales.length === 0 ? <div className="sales-empty-state"><h3>{hasActiveFilters ? "No sales match these filters" : "No sales yet"}</h3>{hasActiveFilters && <button className="catalog-secondary-button" onClick={() => { const clean = { status: "", platform: "", product: "", date_from: "", date_to: "" }; setFilters(clean); setQuickFilter(""); setSearchText(""); setPage(1); writeUrl(clean, "", 1, searchParams.get("sale"), false, ""); }} type="button">Clear filters</button>}{error && <button className="catalog-secondary-button" onClick={load} type="button">Retry</button>}</div> : <>
          <div className="table-wrap sales-table-wrap"><table><thead><tr><th>Sale / product / serial</th><th>Platform</th><th>Order reference</th>{canViewMoney && <th>Price</th>}<th>Date</th><th>Status / age</th></tr></thead><tbody>
            {pageSales.map((sale) => <tr className={saleNeedsAttention(sale) ? "sales-attention-row" : ""} key={sale.id} onClick={() => showSale(sale)} tabIndex={0} onKeyDown={(event) => { if (event.key === "Enter") showSale(sale); }}>
              <td data-label="Sale"><strong>SALE-{sale.id}</strong><small>{sale.units.map((unit) => `${unit.product_number} · ${unit.product_name} · ${unit.serial_number}`).join(", ")}</small></td><td data-label="Platform">{sale.platform_name}</td><td data-label="Order">{sale.order_reference || "—"}</td>{canViewMoney && <td data-label="Price">{formatSaleMoney(sale.price)}</td>}<td data-label="Date">{formatSaleDate(sale.sale_date)}</td><td data-label="Status"><StatusChip status={sale.status} /><SaleAge sale={sale} /><SaleProgress sale={sale}/>{nextSaleStatus[sale.status] && <button className="sales-row-next-action" onClick={(event) => { event.stopPropagation(); setRowStatusSale(sale); }} type="button">Mark {saleStatusLabels[nextSaleStatus[sale.status]]}</button>}</td>
            </tr>)}
          </tbody></table></div>
          <div className="sales-pagination"><button className="catalog-secondary-button" disabled={page <= 1} onClick={() => { const next = page - 1; setPage(next); writeUrl(filters, quickFilter, next); }} type="button">Previous</button><span>Page {page} of {pageCount}</span><button className="catalog-secondary-button" disabled={page >= pageCount} onClick={() => { const next = page + 1; setPage(next); writeUrl(filters, quickFilter, next); }} type="button">Next</button></div>
        </>}
      </section>
      {selectedSale && <SaleDetailPanel key={selectedSale.id} sale={selectedSale} token={token} user={user} onClose={bulkGuard.requestClose} onRefresh={() => refreshSelectedSale(selectedSale.id)} notify={notify} />}
      <UnsavedChangesDialog open={bulkGuard.confirming} onSave={() => bulkGuard.saveFromDialog(updateBulkStatus)} onDiscard={bulkGuard.discard} onKeepEditing={bulkGuard.keepEditing} />
      {bulkConfirming && <div className="sales-dialog-backdrop"><section className="sales-dialog" role="dialog" aria-modal="true" aria-labelledby="bulk-confirm-title"><h2 id="bulk-confirm-title">Confirm bulk status update</h2><p>{bulkScans.length} units → {saleStatusLabels[bulkStatus]}</p><div className="catalog-form-actions"><button className="catalog-secondary-button" onClick={() => setBulkConfirming(false)} type="button">Cancel</button><button className="catalog-primary-button" disabled={busy} onClick={updateBulkStatus} type="button">{busy ? "Applying…" : `Apply to ${bulkScans.length} units`}</button></div></section></div>}
      {rowStatusSale && <div className="sales-dialog-backdrop"><form className="sales-dialog" role="dialog" aria-modal="true" aria-labelledby="row-status-title" onSubmit={(event) => { event.preventDefault(); applyRowStatus(); }}><h2 id="row-status-title">Mark {saleStatusLabels[nextSaleStatus[rowStatusSale.status]]}</h2><p>SALE-{rowStatusSale.id} · {rowStatusSale.units[0]?.product_name} · {rowStatusSale.units[0]?.serial_number}</p><label className="sales-dialog-note">Optional note<input value={rowStatusNote} onChange={(event) => setRowStatusNote(event.target.value)} /></label><SalePageError>{rowStatusError}</SalePageError><div className="catalog-form-actions"><button className="catalog-secondary-button" onClick={() => setRowStatusSale(null)} type="button">Cancel</button><button className="catalog-primary-button">Confirm status change</button></div></form></div>}
    </section>
  );
}

function SaleDetailPanel({ sale, token, user, onClose, onRefresh, notify }) {
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [returnOpen, setReturnOpen] = useState(false);
  const [returnChoice, setReturnChoice] = useState("");
  const [returnOther, setReturnOther] = useState("");
  const [returnNote, setReturnNote] = useState("");
  const [returnSerials, setReturnSerials] = useState([]);
  const [returnError, setReturnError] = useState("");
  const [platforms, setPlatforms] = useState([]);
  const [editForm, setEditForm] = useState(() => ({
    platform: String(sale.platform),
    order_reference: sale.order_reference || "",
    sale_date: sale.sale_date,
    notes: sale.notes || "",
    shipping_name: sale.shipping_name || "",
    shipping_address: sale.shipping_address || "",
    shipping_city: sale.shipping_city || "",
    shipping_state: sale.shipping_state || "",
    shipping_postal_code: sale.shipping_postal_code || "",
    shipping_phone: sale.shipping_phone || "",
    seller_label_reference: sale.seller_label_reference || "",
  }));
  const navigate = useNavigate();
  const target = nextSaleStatus[sale.status];

  useEffect(() => {
    request("/api/platforms/", token).then(setPlatforms)
      .catch(() => setError("Platforms could not be loaded. Retry the edit."));
  }, [token]);

  function updateEdit(event) {
    const { name, value } = event.target;
    setEditForm((current) => ({ ...current, [name]: value }));
  }

  function resetEdit() {
    setEditForm({
      platform: String(sale.platform),
      order_reference: sale.order_reference || "",
      sale_date: sale.sale_date,
      notes: sale.notes || "",
      shipping_name: sale.shipping_name || "",
      shipping_address: sale.shipping_address || "",
      shipping_city: sale.shipping_city || "",
      shipping_state: sale.shipping_state || "",
      shipping_postal_code: sale.shipping_postal_code || "",
      shipping_phone: sale.shipping_phone || "",
      seller_label_reference: sale.seller_label_reference || "",
    });
    setEditing(false);
  }

  async function savePanelEdit(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await request(`/api/sales/${sale.id}/`, token, {
        method: "PATCH",
        body: JSON.stringify({ ...editForm, platform: Number(editForm.platform) }),
      });
      notify(`Sale SALE-${sale.id} details updated.`, "success");
      const updated = await onRefresh();
      setEditForm({
        platform: String(updated.platform),
        order_reference: updated.order_reference || "",
        sale_date: updated.sale_date,
        notes: updated.notes || "",
        shipping_name: updated.shipping_name || "",
        shipping_address: updated.shipping_address || "",
        shipping_city: updated.shipping_city || "",
        shipping_state: updated.shipping_state || "",
        shipping_postal_code: updated.shipping_postal_code || "",
        shipping_phone: updated.shipping_phone || "",
        seller_label_reference: updated.seller_label_reference || "",
      });
      setEditing(false);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  async function submitPanelReturn(event) {
    event.preventDefault();
    const reason = returnChoice === "Other" ? returnOther.trim() : returnChoice;
    if (!reason || !returnSerials.length) return;
    setSaving(true);
    setReturnError("");
    try {
      await request(`/api/sales/${sale.id}/return/`, token, {
        method: "POST",
        body: JSON.stringify({
          reason: `${reason}${returnNote.trim() ? ` — Note: ${returnNote.trim()}` : ""}`,
          serial_numbers: returnSerials,
        }),
      });
      returnSerials.forEach((serialNumber) => notify(`Sale SALE-${sale.id} (${serialNumber}): Return requested — Under inspection.`, "success"));
      setReturnOpen(false);
      setReturnChoice("");
      setReturnOther("");
      setReturnNote("");
      setReturnSerials([]);
      await onRefresh();
    } catch (failure) {
      setReturnError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  async function submitStatus(event) {
    event.preventDefault();
    if (!target) return;
    setSaving(true);
    setError("");
    try {
      await request(`/api/sales/${sale.id}/status/`, token, {
        method: "POST",
        body: JSON.stringify({ status: target, note }),
      });
      notify(`Sale SALE-${sale.id} (${sale.units[0]?.product_name || "product"}, ${sale.units[0]?.serial_number || "unit"}): ${saleStatusLabels[sale.status]} -> ${saleStatusLabels[target]}`, "success");
      setTargetStatus("");
      setNote("");
      await onRefresh();
      onClose();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  return <div className="sales-panel-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <aside className="sales-detail-sheet" aria-label={`Sale SALE-${sale.id}`} role="dialog" aria-modal="true">
      <div className="sales-sheet-heading"><div><span className="eyebrow">{sale.platform_name} · SALE-{sale.id}</span><h2>{sale.order_reference || "Sale details"}</h2></div><button className="catalog-secondary-button" onClick={onClose} type="button">Close</button></div>
      {editing ? <SaleEditForm sale={sale} editForm={editForm} platforms={platforms} isGenerated={sale.label.mode === "generated"} onChange={updateEdit} onSave={savePanelEdit} onClose={resetEdit} saving={saving} /> : <>
        <SaleProgress sale={sale} />
        <SaleMoneySummary sale={sale} />
        <SaleCustomerSummary sale={sale} />
        <div className="sales-sheet-grid"><div><span>Product</span><strong>{sale.units.map((unit) => `${unit.product_number} · ${unit.product_name}`).join(", ")}</strong></div><div><span>Serial</span><strong>{sale.units.map((unit) => unit.serial_number).join(", ")}</strong></div><div><span>Platform</span><strong>{sale.platform_name}</strong></div><div><span>Order reference</span><strong>{sale.order_reference || "—"}</strong></div><div><span>Sale date</span><strong>{formatSaleDate(sale.sale_date)}</strong></div></div>
        {sale.notes && <p className="sales-sheet-notes">{sale.notes}</p>}
        <div className="sales-sheet-attention"><StatusChip status={sale.status} /><SaleAge sale={sale} /></div>
        {nextSaleStatus[sale.status] && <form className="sales-sheet-action" onSubmit={submitStatus}>
        <label>Status change note (optional)<input value={note} onChange={(event) => setNote(event.target.value)} /></label>
        <button className="catalog-primary-button" disabled={saving}>{saving ? "Updating…" : `Mark ${saleStatusLabels[nextSaleStatus[sale.status]]}`}</button>
      </form>}
      <div className="sales-sheet-secondary-actions">
        {["DELIVERED", "PAYMENT_PENDING", "PAYMENT_RECEIVED"].includes(sale.status) && <button className="catalog-secondary-button" onClick={() => { setReturnSerials(sale.units.filter((unit) => unit.status === "SOLD" && !sale.returns.some((record) => record.serial_number === unit.serial_number)).map((unit) => unit.serial_number)); setReturnOpen(true); }} type="button">Return</button>}
        <button className="catalog-secondary-button" onClick={() => setEditing(true)} type="button">Edit details</button>
        <button className="catalog-secondary-button" onClick={() => navigate(`/sales/${sale.id}`)} type="button">Open sale and documents</button>
      </div>
      <section className="sales-sheet-timeline"><h3>Status timeline</h3>{sale.status_history.map((item) => <article key={item.id}><strong>{item.from_status_label || "Sale created"} → {item.to_status_label}</strong><span>{item.actor_name || "System"} · {formatSaleDateTime(item.timestamp)}</span>{item.note && <p>{item.note}</p>}</article>)}</section>
      {sale.returns.length > 0 && <section className="sales-sheet-returns"><h3>Returns and inspection</h3>{sale.returns.map((item) => <article key={item.id}><strong>{item.serial_number} · {item.decision === "PENDING" ? "Awaiting inspection" : item.decision}</strong><span>{item.reason}</span></article>)}{user?.role === "SUPER_ADMIN" && sale.returns.some((item) => item.decision === "PENDING") && <button className="catalog-secondary-button" onClick={() => navigate(`/sales/${sale.id}`)} type="button">Open return disposition</button>}</section>}
      </>}
      <SalePageError>{error}</SalePageError>
      {returnOpen && <div className="sales-dialog-backdrop"><form aria-labelledby="panel-return-title" aria-modal="true" className="sales-dialog sales-panel-return-dialog" onSubmit={submitPanelReturn} role="dialog">
        <h2 id="panel-return-title">Return units for inspection</h2>
        <p>Choose the affected sold units and provide a reason.</p>
        {sale.units.filter((unit) => unit.status === "SOLD" && !sale.returns.some((record) => record.serial_number === unit.serial_number)).map((unit) => <label className="sales-return-choice" key={unit.id}><input type="checkbox" checked={returnSerials.includes(unit.serial_number)} onChange={(event) => setReturnSerials((current) => event.target.checked ? [...current, unit.serial_number] : current.filter((value) => value !== unit.serial_number))} /><span>{unit.serial_number} · {unit.product_number} {unit.product_name}</span></label>)}
        <label className="sales-dialog-note">Return reason<select required value={returnChoice} onChange={(event) => setReturnChoice(event.target.value)}><option value="">Choose a reason</option><option>Damaged</option><option>Wrong item</option><option>Customer changed mind</option><option>Delivery issue</option><option>Other</option></select></label>
        {returnChoice === "Other" && <label className="sales-dialog-note">Other reason<input required maxLength="900" value={returnOther} onChange={(event) => setReturnOther(event.target.value)} /></label>}
        <label className="sales-dialog-note">Optional note<input maxLength="900" value={returnNote} onChange={(event) => setReturnNote(event.target.value)} /></label>
        <p>Affected: {returnSerials.length ? returnSerials.join(", ") : "No units selected."}</p>
        <SalePageError>{returnError}</SalePageError>
        <div className="catalog-form-actions"><button className="catalog-secondary-button" onClick={() => setReturnOpen(false)} type="button">Cancel</button><button className="catalog-primary-button" disabled={saving || !returnSerials.length}>{saving ? "Recording return…" : "Confirm return"}</button></div>
      </form></div>}
    </aside>
  </div>;
}

export function SaleCreatePage({ token }) {
  const [platforms, setPlatforms] = useState([]);
  const [states, setStates] = useState([]);
  const [products, setProducts] = useState([]);
  const [platformId, setPlatformId] = useState("");
  const [selectedUnits, setSelectedUnits] = useState([]);
  const [allocations, setAllocations] = useState([]);
  const [serial, setSerial] = useState("");
  const [form, setForm] = useState({
    order_reference: "",
    sale_date: localDate(),
    customer_state_code: "",
    billing_address: "",
    customer_gstin: "",
    notes: "",
    shipping_name: "",
    shipping_address: "",
    shipping_city: "",
    shipping_state: "",
    shipping_postal_code: "",
    shipping_phone: "",
    seller_label_reference: "",
  });
  const [productToAdd, setProductToAdd] = useState("");
  const [quantityToAdd, setQuantityToAdd] = useState("1");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const navigate = useNavigate();
  const { notify } = useNotify();
  const platform = platforms.find((entry) => String(entry.id) === platformId);
  const generatedLabel = platform?.label_template_type === "TAMIRABOT";

  useEffect(() => {
    let active = true;
    Promise.all([
      request("/api/platforms/", token),
      request("/api/finished-units/summary/", token),
      request("/api/states/", token),
    ])
      .then(([platformRows, productSummary, stateRows]) => {
        if (!active) return;
        setPlatforms(platformRows.filter((item) => item.active));
        setProducts(productSummary.filter((item) => item.in_stock > 0));
        setStates(stateRows);
        setPlatformId(String(platformRows.find((item) => item.active)?.id || ""));
      })
      .catch((failure) => active && setError(failure.message));
    return () => { active = false; };
  }, [token]);

  const addScannedUnit = useCallback(async (value) => {
    const code = value.trim();
    if (!code) return;
    setError("");
    try {
      const unit = await request(
        `/api/finished-units/lookup/?serial_number=${encodeURIComponent(code)}`,
        token,
      );
      if (unit.status !== "IN_STOCK") {
        setError(`${unit.serial_number} is not In Stock.`);
        return;
      }
      if (selectedUnits.some((entry) => entry.id === unit.id)) {
        setError(`${unit.serial_number} is already selected.`);
        return;
      }
      setSelectedUnits((current) => [...current, unit]);
      setSerial("");
    } catch (failure) {
      setError(failure.message);
    }
  }, [selectedUnits, token]);

  function updateForm(event) {
    const { name, value } = event.target;
    setForm((current) => ({ ...current, [name]: value }));
  }

  function addProductQuantity(event) {
    event.preventDefault();
    if (!productToAdd) return;
    const productId = Number(productToAdd);
    const quantity = Number(quantityToAdd);
    if (!Number.isInteger(quantity) || quantity < 1) {
      setError("Enter a quantity of at least one.");
      return;
    }
    setError("");
    setAllocations((current) => {
      const existing = current.find((item) => item.product === productId);
      if (existing) {
        return current.map((item) => item.product === productId
          ? { ...item, quantity: item.quantity + quantity }
          : item);
      }
      return [...current, { product: productId, quantity }];
    });
    setProductToAdd("");
    setQuantityToAdd("1");
  }

  function changeAllocation(productId, quantity) {
    if (quantity < 1) {
      setAllocations((current) => current.filter((entry) => entry.product !== productId));
      return;
    }
    setAllocations((current) => current.map((entry) => entry.product === productId
      ? { ...entry, quantity }
      : entry));
  }

  async function submit(event) {
    event.preventDefault();
    setError("");
    setSaving(true);
    try {
      const result = await request("/api/sales/", token, {
        method: "POST",
        body: JSON.stringify({
          platform: Number(platformId),
          unit_ids: selectedUnits.map((unit) => unit.id),
          product_quantities: allocations,
          customer_name: form.shipping_name,
          customer_phone: form.shipping_phone,
          delivery_address: form.shipping_address,
          ...form,
        }),
      });
      navigate(`/sales/${result.id}`);
    } catch (failure) {
      setError(failure.message);
      if (failure.message.includes("Price not set for ")) notify(failure.message, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="page-content sales-page">
      <Link className="catalog-back-link" to="/sales">← Sales register</Link>
      <div className="page-heading sales-form-heading"><div><div className="eyebrow">SALES TRACKING</div><h1>New sale</h1><p className="page-subtitle">Prices are taken from the product price list. Only finished units currently In Stock can be sold.</p></div></div>
      <SalePageError>{error}</SalePageError>
      <form className="panel sales-form" onSubmit={submit}>
        <div className="sales-form-section">
          <div className="panel-heading"><div><h2>Order details</h2><p>Choose the selling platform and enter the order information.</p></div></div>
          <div className="sales-form-grid">
            <label>Platform<select required value={platformId} onChange={(event) => setPlatformId(event.target.value)}><option value="">Select a platform</option>{platforms.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
            <label>Customer / order reference<input name="order_reference" required={!form.shipping_name} value={form.order_reference} onChange={updateForm} /></label>
            <label>Sale date<input name="sale_date" type="date" required value={form.sale_date} onChange={updateForm} /></label>
            <label>Delivery state / UT<select name="customer_state_code" required value={form.customer_state_code} onChange={updateForm}><option value="">Select state / UT</option>{states.map((state) => <option key={state.state_code} value={state.state_code}>{state.name} ({state.state_code})</option>)}</select></label>
            {generatedLabel ? (
              <>
                <label>Recipient name<input name="shipping_name" required value={form.shipping_name} onChange={updateForm} /></label>
                <label className="sales-form-wide">Address<input name="shipping_address" required value={form.shipping_address} onChange={updateForm} /></label>
                <label className="sales-form-wide">Billing address<textarea name="billing_address" required rows="2" value={form.billing_address} onChange={updateForm} /></label>
                <label>City<input name="shipping_city" value={form.shipping_city} onChange={updateForm} /></label>
                <label>GSTIN (optional)<input name="customer_gstin" maxLength="15" value={form.customer_gstin} onChange={updateForm} /></label>
                <label>Postal code<input name="shipping_postal_code" value={form.shipping_postal_code} onChange={updateForm} /></label>
                <label>Phone<input name="shipping_phone" required value={form.shipping_phone} onChange={updateForm} /></label>
              </>
            ) : platform ? (
              <label className="sales-form-wide">Seller-provided label / reference<input name="seller_label_reference" required value={form.seller_label_reference} onChange={updateForm} placeholder="Paste a label URL or seller reference" /></label>
            ) : null}
            <label className="sales-form-wide">Notes<textarea name="notes" rows="3" value={form.notes} onChange={updateForm} /></label>
          </div>
        </div>

        <div className="sales-form-section">
          <div className="panel-heading"><div><h2>Finished units</h2><p>Scan serials individually, or allocate a quantity from a product’s in-stock units.</p></div><strong className="count-pill">{selectedUnits.length + allocations.reduce((sum, item) => sum + item.quantity, 0)} selected</strong></div>
          <div className="serial-lookup-form sales-serial-form">
            <input aria-label="Scan or enter finished-unit serial" autoComplete="off" autoFocus value={serial} onChange={(event) => setSerial(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addScannedUnit(serial); } }} placeholder="Scan or enter finished-unit serial" />
            <button className="catalog-secondary-button" onClick={() => addScannedUnit(serial)} type="button">Add serial</button>
          </div>
          <CameraScanner onScan={addScannedUnit} />
          {selectedUnits.length > 0 && <div className="sales-selected-units">
            {selectedUnits.map((unit) => <div className="sales-selected-row" key={unit.id}>
              <span><strong>{unit.serial_number}</strong><small>{unit.product_number} · {unit.product_name} · Rev {unit.revision_number}</small></span>
              <button className="catalog-text-link" onClick={() => setSelectedUnits((current) => current.filter((item) => item.id !== unit.id))} type="button">Remove</button>
            </div>)}
          </div>}
          <div className="sales-product-add">
            <label>Product<select value={productToAdd} onChange={(event) => setProductToAdd(event.target.value)}><option value="">Select product</option>{products.map((item) => <option key={item.id} value={item.id}>{item.product_number} · {item.name}</option>)}</select></label>
            <label>Quantity<input type="number" min="1" value={quantityToAdd} onChange={(event) => setQuantityToAdd(event.target.value)} /></label>
            <button className="catalog-secondary-button" onClick={addProductQuantity} type="button">Add quantity</button>
          </div>
          {allocations.length > 0 && <div className="sales-selected-units">
            {allocations.map((item) => {
              const product = products.find((entry) => entry.id === item.product);
              return <div className="sales-selected-row" key={item.product}>
                <span><strong>{product?.product_number} · {product?.name}</strong><small>Server will select {item.quantity} available finished unit{item.quantity === 1 ? "" : "s"}.</small></span>
                <input aria-label={`Quantity for ${product?.name}`} type="number" min="1" value={item.quantity} onChange={(event) => changeAllocation(item.product, Number(event.target.value))} />
                <button className="catalog-text-link" onClick={() => changeAllocation(item.product, 0)} type="button">Remove</button>
              </div>;
            })}
          </div>}
        </div>
        <div className="catalog-form-actions">
          <Link className="catalog-secondary-button" to="/sales">Cancel</Link>
          <button className="catalog-primary-button" disabled={saving}>{saving ? "Creating sale…" : "Create sale · Ready to Ship"}</button>
        </div>
      </form>
    </section>
  );
}

export function SaleDetailPage({ token, user }) {
  const { saleId } = useParams();
  const [sale, setSale] = useState(null);
  const [invoiceStatus, setInvoiceStatus] = useState(null);
  const [labelPreview, setLabelPreview] = useState(null);
  const [labelOverflow, setLabelOverflow] = useState(false);
  const [creditNoteReason, setCreditNoteReason] = useState("");
  const [creditNoteSerials, setCreditNoteSerials] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [barcodeError, setBarcodeError] = useState("");
  const [editForm, setEditForm] = useState({});
  const [platforms, setPlatforms] = useState([]);
  const [admins, setAdmins] = useState([]);
  const [statusNote, setStatusNote] = useState("");
  const [returnPanel, setReturnPanel] = useState(false);
  const [returnReasonChoice, setReturnReasonChoice] = useState("");
  const [returnOtherReason, setReturnOtherReason] = useState("");
  const [returnNote, setReturnNote] = useState("");
  const [returnScan, setReturnScan] = useState("");
  const [returnSerials, setReturnSerials] = useState([]);
  const [returnDecision, setReturnDecision] = useState("RESTOCK");
  const [decisionNote, setDecisionNote] = useState("");
  const [decisionAdmin, setDecisionAdmin] = useState("");
  const [decisionSerials, setDecisionSerials] = useState([]);
  const [decisionPanel, setDecisionPanel] = useState(false);
  const [scrapConfirmation, setScrapConfirmation] = useState("");
  const notifyContext = useNotify();
  const notify = notifyContext.notify;
  const navigate = useNavigate();

  const canAccessInvoice = user?.role === "SUPER_ADMIN"
    || user?.dashboard_sections?.includes("sales_overview");
  const load = useCallback(() => request(`/api/sales/${saleId}/`, token)
    .then(async (result) => {
      setSale(result);
      setDecisionSerials(
        result.returns
          .filter((item) => item.decision === "PENDING")
          .map((item) => item.serial_number),
      );
      setEditForm({
        platform: String(result.platform),
        order_reference: result.order_reference || "",
        sale_date: result.sale_date,
        notes: result.notes || "",
        shipping_name: result.shipping_name || "",
        shipping_address: result.shipping_address || "",
        shipping_city: result.shipping_city || "",
        shipping_state: result.shipping_state || "",
        shipping_postal_code: result.shipping_postal_code || "",
        shipping_phone: result.shipping_phone || "",
        seller_label_reference: result.seller_label_reference || "",
      });
      if (canAccessInvoice) {
        const [documentStatus, labelStatus] = await Promise.all([
          request(`/api/sales/${saleId}/invoice/`, token),
          request(`/api/sales/${saleId}/labels/`, token),
        ]);
        setInvoiceStatus(documentStatus);
        setCreditNoteSerials(documentStatus.invoice?.available_return_serials || []);
        setLabelPreview(labelStatus);
      } else {
        setInvoiceStatus(null);
        setLabelPreview(null);
      }
    })
    .catch(() => setError("Sale details could not be loaded. Check your connection and retry."))
    .finally(() => setLoading(false)), [canAccessInvoice, saleId, token]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    let active = true;
    const requests = [request("/api/platforms/", token)];
    if (user?.role === "SUPER_ADMIN") {
      requests.push(request("/api/work-orders/assignees/", token));
    }
    Promise.all(requests)
      .then(([platformRows, adminRows = []]) => {
        if (!active) return;
        setPlatforms(platformRows);
        setAdmins(adminRows);
      })
      .catch((failure) => active && setError(failure.message));
    return () => { active = false; };
  }, [token, user?.role]);
  useEffect(() => {
    if (!sale || sale.label.mode !== "generated") return;
    try {
      document.querySelectorAll("[data-sale-barcode], [data-tamirabot-label-barcode]").forEach((barcode) => {
        JsBarcode(
          barcode,
          barcode.dataset.saleBarcode || barcode.dataset.tamirabotLabelBarcode,
          {
          format: "CODE128",
          displayValue: false,
          margin: 0,
          height: barcode.dataset.tamirabotLabelBarcode ? 34 : 40,
          width: barcode.dataset.tamirabotLabelBarcode ? 1.15 : 1.4,
          },
        );
      });
      setBarcodeError("");
    } catch {
      setBarcodeError("One or more packing-label barcodes could not be rendered.");
    }
  }, [labelPreview, sale]);
  useEffect(() => {
    if (!labelPreview?.labels?.length) {
      setLabelOverflow(false);
      return undefined;
    }
    const frame = window.requestAnimationFrame(() => {
      const cards = [...document.querySelectorAll(".tamirabot-legal-label")];
      cards.forEach((card) => {
        const addresses = [...card.querySelectorAll("[data-fit-label-address]")];
        const baseSizes = addresses.map((address) => (
          Number.parseFloat(window.getComputedStyle(address).fontSize) * 0.75
        ));
        const maxShrink = Math.max(0, ...baseSizes.map((size) => size - 6));
        for (let shrink = 0; shrink <= maxShrink; shrink += 0.25) {
          addresses.forEach((address, index) => {
            address.style.fontSize = `${Math.max(6, baseSizes[index] - shrink)}pt`;
          });
          if (card.scrollHeight <= card.clientHeight + 2
            && card.scrollWidth <= card.clientWidth + 2) break;
        }
      });
      setLabelOverflow(cards.some(
        (card) => card.scrollHeight > card.clientHeight + 2
          || card.scrollWidth > card.clientWidth + 2,
      ));
    });
    return () => window.cancelAnimationFrame(frame);
  }, [labelPreview, sale]);

  function updateEdit(event) {
    const { name, value } = event.target;
    setEditForm((current) => ({ ...current, [name]: value }));
  }

  async function changeStatus(targetStatus) {
    setSaving(true);
    setError("");
    try {
      await request(`/api/sales/${sale.id}/status/`, token, {
        method: "POST",
        body: JSON.stringify({ status: targetStatus, note: statusNote }),
      });
      notify(`Sale SALE-${sale.id} (${sale.units[0]?.product_name || "product"}, ${sale.units[0]?.serial_number || "unit"}): ${saleStatusLabels[sale.status]} -> ${saleStatusLabels[targetStatus]}`, "success");
      setStatusNote("");
      await load();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  const addReturnSerial = useCallback((value) => {
    const serialNumber = value.trim();
    if (!serialNumber) return;
    const candidates = sale.units.filter((unit) => unit.status === "SOLD");
    const unit = candidates.find((item) => item.serial_number.toLowerCase() === serialNumber.toLowerCase());
    if (!unit) {
      setError("This serial is not a sold unit on this sale.");
      return;
    }
    if (sale.returns.some((item) => item.serial_number === unit.serial_number)) {
      setError(`${unit.serial_number} already has a return record.`);
      return;
    }
    setError("");
    setReturnSerials((current) => current.includes(unit.serial_number) ? current : [...current, unit.serial_number]);
    setReturnScan("");
  }, [sale]);

  async function submitReturn(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await request(`/api/sales/${sale.id}/return/`, token, {
        method: "POST",
        body: JSON.stringify({
          reason: `${returnReasonChoice === "Other" ? returnOtherReason.trim() : returnReasonChoice}${returnNote.trim() ? ` — Note: ${returnNote.trim()}` : ""}`,
          serial_numbers: returnSerials,
        }),
      });
      returnSerials.forEach((serialNumber) => notify(`Sale SALE-${sale.id} (${serialNumber}): Return requested — Under inspection.`, "success"));
      setReturnPanel(false);
      setReturnReasonChoice("");
      setReturnOtherReason("");
      setReturnNote("");
      setReturnSerials([]);
      await load();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  async function decideReturn(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const result = await request(`/api/sales/${sale.id}/return-decision/`, token, {
        method: "POST",
        body: JSON.stringify({
          decision: returnDecision,
          serial_numbers: decisionSerials,
          note: decisionNote,
          ...(returnDecision === "REWORK" ? { assigned_admin: Number(decisionAdmin) } : {}),
        }),
      });
      decisionSerials.forEach((serialNumber) => {
        const returnItem = result.returns.find((item) => item.serial_number === serialNumber);
        const workOrderId = returnItem?.rework_order;
        const outcome = returnDecision === "RESTOCK"
          ? "Restocked as finished goods."
          : returnDecision === "SCRAP"
            ? "Scrapped; removed from stock."
            : `Rework started${returnItem?.rework_order_number ? ` in ${returnItem.rework_order_number}` : ""}.`;
        notify(`Sale SALE-${sale.id} (${serialNumber}): ${outcome}`, "success", workOrderId ? {
          label: "Open work order",
          onClick: () => navigate(`/work-orders/${workOrderId}`),
        } : null);
      });
      setDecisionNote("");
      setScrapConfirmation("");
      setDecisionPanel(false);
      await load();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  async function saveEdit(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await request(`/api/sales/${sale.id}/`, token, {
        method: "PATCH",
        body: JSON.stringify({
          ...editForm,
          platform: Number(editForm.platform),
        }),
      });
      setEditing(false);
      notify(`Sale SALE-${sale.id} details updated.`, "success");
      await load();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  async function issueInvoice() {
    setSaving(true);
    setError("");
    try {
      const result = await request(`/api/sales/${sale.id}/invoice/`, token, {
        method: "POST",
      });
      setInvoiceStatus(result);
      setCreditNoteSerials(result.invoice?.available_return_serials || []);
      setLabelPreview(await request(`/api/sales/${sale.id}/labels/`, token));
      if (result.invoice) {
        notify(
          `Tax invoice ${result.invoice.number} issued for sale SALE-${sale.id}.`,
          "success",
        );
      } else {
        notify(result.message, "info");
      }
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  async function printSaleLabels() {
    if (!isGenerated || !canAccessInvoice) {
      window.print();
      return;
    }
    if (!labelPreview?.can_print) {
      const missing = [
        ...(labelPreview?.missing_fields || []),
        ...(labelPreview?.labels || []).flatMap((label) => label.missing_fields),
      ];
      setError(
        missing.length
          ? `Label printing is blocked. Missing: ${[...new Set(missing)].join(", ")}.`
          : "Configure a default label template before printing.",
      );
      return;
    }
    if (labelOverflow) {
      setError("Label content does not fit at the minimum 6 pt text size. Choose A6 or A4, or shorten the source address before printing.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const results = await Promise.all(labelPreview.labels.map((label) => request(
        `/api/sales/${sale.id}/labels/${label.sale_unit_id}/print/`,
        token,
        { method: "POST" },
      )));
      results.forEach((result) => {
        const label = result.label;
        notify(
          `Label for ${label.serial_number} ${result.is_reprint ? "reprinted" : "printed"}.`,
          "success",
        );
      });
      window.print();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  async function downloadSaleLabelsPdf() {
    if (!labelPreview?.can_print || labelOverflow) {
      setError(
        labelOverflow
          ? "Label content does not fit at the minimum 6 pt text size. Choose A6 or A4, or shorten the source address before downloading."
          : "Label download is blocked until all required label fields are complete.",
      );
      return;
    }
    setSaving(true);
    setError("");
    try {
      const barcodeSvgs = [...document.querySelectorAll("[data-tamirabot-label-barcode]")];
      const barcodes = await Promise.all(labelPreview.labels.map(async (label) => {
        const svg = barcodeSvgs.find(
          (element) => element.dataset.tamirabotLabelBarcode === label.barcode_value,
        );
        if (!svg) throw new Error(`The Code128 barcode for ${label.serial_number} is not ready.`);
        return {
          sale_unit_id: label.sale_unit_id,
          barcode_value: label.barcode_value,
          image: await barcodeSvgToPng(svg),
        };
      }));
      const response = await fetch(`/api/sales/${sale.id}/labels/pdf/`, {
        method: "POST",
        headers: {
          Authorization: `Token ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ barcodes }),
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        const missing = result.missing_fields || [];
        throw new Error(
          result.detail
            || (missing.length ? `Missing: ${missing.join(", ")}` : "The label PDF could not be downloaded."),
        );
      }
      const pdfUrl = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = pdfUrl;
      link.download = `SALE-${sale.id}-labels.pdf`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(pdfUrl), 1000);
      const printResults = JSON.parse(response.headers.get("X-Label-Print-Results") || "[]");
      printResults.forEach((result) => {
        notify(
          `Label for ${result.serial_number} ${result.is_reprint ? "reprinted" : "downloaded"}.`,
          "success",
        );
      });
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  async function downloadDocument(path, filename) {
    setError("");
    try {
      const response = await fetch(path, {
        headers: { Authorization: `Token ${token}` },
      });
      if (!response.ok) {
        const result = await response.json().catch(() => ({}));
        throw new Error(result.detail || "The document could not be downloaded.");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (failure) {
      setError(failure.message);
    }
  }

  async function issueCreditNote(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const result = await request(`/api/sales/${sale.id}/credit-note/`, token, {
        method: "POST",
        body: JSON.stringify({
          serial_numbers: creditNoteSerials,
          reason: creditNoteReason,
        }),
      });
      notify(
        `Credit note ${result.number} issued for ${creditNoteSerials.join(", ")}.`,
        "success",
      );
      setCreditNoteReason("");
      await load();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <section className="page-content"><div className="sales-skeleton-list"><div /><div /><div /></div></section>;
  if (error && !sale) return <section className="page-content"><SalePageError>{error}</SalePageError><button className="catalog-secondary-button" onClick={load} type="button">Retry</button></section>;
  if (!sale) return null;

  const isGenerated = sale.label.mode === "generated";
  return (
    <section className="page-content sales-page sale-detail-page">
      <Link className="catalog-back-link no-print" to="/sales">← Sales register</Link>
      <div className="page-heading no-print">
        <div><div className="eyebrow">{sale.platform_name.toUpperCase()} · SALE-{sale.id}</div><h1>{sale.order_reference || `Sale ${sale.id}`}</h1><p className="page-subtitle">{formatSaleDate(sale.sale_date)} · {sale.units.length} finished unit{sale.units.length === 1 ? "" : "s"} · <StatusChip status={sale.status} /></p></div>
        <div className="sales-heading-actions">
          <button className="secondary-button" onClick={() => setEditing((current) => !current)}>{editing ? "Cancel edit" : "Edit details"}</button>
          {nextSaleStatus[sale.status] && <button className="catalog-primary-button" disabled={saving} onClick={() => changeStatus(nextSaleStatus[sale.status])}>Mark {saleStatusLabels[nextSaleStatus[sale.status]]}</button>}
          {["DELIVERED", "PAYMENT_PENDING", "PAYMENT_RECEIVED"].includes(sale.status) && <button className="secondary-button" onClick={() => setReturnPanel((current) => !current)} type="button">Return</button>}
          {isGenerated && <button className="primary-button" disabled={saving || (canAccessInvoice && labelPreview?.generated && (!labelPreview.can_print || labelOverflow))} onClick={printSaleLabels}>{saving ? "Preparing labels…" : "Print / Save PDF"}</button>}
          {isGenerated && canAccessInvoice && labelPreview?.generated && <button className="catalog-secondary-button" disabled={saving || !labelPreview.can_print || labelOverflow} onClick={downloadSaleLabelsPdf} type="button">Download labels PDF</button>}
          {canAccessInvoice && invoiceStatus?.invoice && <button className="catalog-secondary-button" onClick={() => downloadDocument(`/api/invoices/${invoiceStatus.invoice.id}/pdf/`, `${invoiceStatus.invoice.number.replaceAll("/", "-")}.pdf`)} type="button">Download invoice PDF</button>}
        </div>
      </div>
      <SaleProgress sale={sale} />
      <SalePageError>{error}</SalePageError>
      {canAccessInvoice && <section className="panel sales-invoice-panel">
        <div className="panel-heading"><div><h2>Tax invoice & credit notes</h2><p>Issued documents preserve the company, buyer, product and tax details from issue time.</p></div></div>
        <div className="sales-invoice-payment">
          <span>Payment status <strong>{saleStatusLabels[sale.status]}</strong></span>
          <span>Amount due <strong>{formatSaleMoney(invoiceStatus?.invoice?.total_amount ?? sale.price)}</strong></span>
          {sale.status === "PAYMENT_RECEIVED" && sale.payment_received_at
            && <span>Payment received on <strong>{formatSaleDateTime(sale.payment_received_at)}</strong></span>}
        </div>
        {invoiceStatus?.marketplace_invoice ? <p className="sales-invoice-marketplace">The marketplace issues the invoice.</p> : invoiceStatus?.invoice ? <>
          <div className="sales-invoice-summary">
            <strong>{invoiceStatus.invoice.number}</strong>
            <span>Issued {formatSaleDate(invoiceStatus.invoice.issue_date)}</span>
            <span>{formatSaleMoney(invoiceStatus.invoice.total_amount)}</span>
            <button className="catalog-secondary-button" onClick={() => downloadDocument(`/api/invoices/${invoiceStatus.invoice.id}/pdf/`, `${invoiceStatus.invoice.number.replaceAll("/", "-")}.pdf`)} type="button">Download / reprint A4 PDF</button>
          </div>
          {invoiceStatus.invoice.credit_notes.map((note) => <div className="sales-invoice-summary" key={note.id}>
            <strong>{note.number}</strong><span>{formatSaleDate(note.date)} · {note.reason}</span>
            <span>Credit {formatSaleMoney(note.amounts.total_amount)}</span>
            <button className="catalog-secondary-button" onClick={() => downloadDocument(`/api/credit-notes/${note.id}/pdf/`, `${note.number.replaceAll("/", "-")}.pdf`)} type="button">Download credit note</button>
          </div>)}
          {user?.role === "SUPER_ADMIN" && invoiceStatus.invoice.available_return_serials.length > 0 && <form className="sales-credit-note-form" onSubmit={issueCreditNote}>
            <h3>Create credit note for returned unit(s)</h3>
            {invoiceStatus.invoice.available_return_serials.map((serialNumber) => <label className="sales-return-choice" key={serialNumber}>
              <input checked={creditNoteSerials.includes(serialNumber)} onChange={(event) => setCreditNoteSerials((current) => event.target.checked ? [...current, serialNumber] : current.filter((value) => value !== serialNumber))} type="checkbox" />
              <span>{serialNumber}</span>
            </label>)}
            <label className="sales-form-label">Credit note reason<textarea required maxLength="1000" onChange={(event) => setCreditNoteReason(event.target.value)} value={creditNoteReason} /></label>
            <button className="catalog-primary-button" disabled={saving || !creditNoteSerials.length} type="submit">{saving ? "Issuing credit note…" : "Issue credit note"}</button>
          </form>}
        </> : user?.role === "SUPER_ADMIN" && sale.issue_tamirabot_invoice ? <div className="sales-invoice-issue">
          <p>No invoice has been issued for this sale yet.</p>
          <button className="catalog-primary-button" disabled={saving} onClick={issueInvoice} type="button">{saving ? "Checking readiness…" : "Issue tax invoice"}</button>
        </div> : null}
      </section>}
      {nextSaleStatus[sale.status] && <div className="sales-status-note no-print">
        <label>Status change note<input value={statusNote} onChange={(event) => setStatusNote(event.target.value)} placeholder="Optional delivery or payment note" /></label>
      </div>}
      {returnPanel && <div className="sales-dialog-backdrop"><form aria-labelledby="sales-return-title" aria-modal="true" className="panel sales-return-form sales-return-dialog no-print" onSubmit={submitReturn} role="dialog">
        <div className="panel-heading"><div><h2 id="sales-return-title">Return to production for inspection</h2><p>Choose the returned serial(s), enter a reason, and send them to Under inspection.</p></div><button className="catalog-text-link" onClick={() => setReturnPanel(false)} type="button">Close</button></div>
        <div className="serial-lookup-form sales-serial-form">
          <input aria-label="Scan returned unit serial" autoComplete="off" value={returnScan} onChange={(event) => setReturnScan(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addReturnSerial(returnScan); } }} placeholder="Scan or enter returned unit serial" />
          <button className="catalog-secondary-button" onClick={() => addReturnSerial(returnScan)} type="button">Add unit</button>
        </div>
        <CameraScanner continuous onScan={addReturnSerial} />
        {returnSerials.map((serialNumber) => <div className="sales-selected-row" key={serialNumber}>
          <span><strong>{serialNumber}</strong><small>Will be marked Under inspection</small></span>
          <button className="catalog-text-link" onClick={() => setReturnSerials((current) => current.filter((value) => value !== serialNumber))} type="button">Remove</button>
        </div>)}
        <label className="sales-form-label">Return reason<select required value={returnReasonChoice} onChange={(event) => setReturnReasonChoice(event.target.value)}><option value="">Choose a reason</option><option>Damaged</option><option>Wrong item</option><option>Customer changed mind</option><option>Delivery issue</option><option>Other</option></select></label>
        {returnReasonChoice === "Other" && <label className="sales-form-label">Other reason<textarea required maxLength="1000" rows="2" value={returnOtherReason} onChange={(event) => setReturnOtherReason(event.target.value)} /></label>}
        <label className="sales-form-label">Optional note<textarea maxLength="1000" rows="2" value={returnNote} onChange={(event) => setReturnNote(event.target.value)} /></label>
        <p className="sales-return-affected">Affected units: {returnSerials.length ? returnSerials.join(", ") : "Scan or add one or more units above."}</p>
        <div className="catalog-form-actions"><button className="catalog-primary-button" disabled={saving || !returnSerials.length}>{saving ? "Recording return…" : `Send ${returnSerials.length} unit${returnSerials.length === 1 ? "" : "s"} for inspection`}</button></div>
      </form></div>}
      {editing && <SaleEditForm sale={sale} editForm={editForm} platforms={platforms} isGenerated={isGenerated} onChange={updateEdit} onSave={saveEdit} onClose={() => { setEditForm({
        platform: String(sale.platform),
        order_reference: sale.order_reference || "",
        sale_date: sale.sale_date,
        notes: sale.notes || "",
        shipping_name: sale.shipping_name || "",
        shipping_address: sale.shipping_address || "",
        shipping_city: sale.shipping_city || "",
        shipping_state: sale.shipping_state || "",
        shipping_postal_code: sale.shipping_postal_code || "",
        shipping_phone: sale.shipping_phone || "",
        seller_label_reference: sale.seller_label_reference || "",
      }); setEditing(false); }} saving={saving} />}
      <section className="panel sales-order-summary no-print">
        <div className="panel-heading"><div><h2>Order summary</h2><p>{sale.platform_name} · {sale.created_by_name} · {saleStatusLabels[sale.status]}</p></div></div>
        <SaleMoneySummary sale={sale} />
        <SaleCustomerSummary sale={sale} />
        {sale.notes && <p>{sale.notes}</p>}
        <SaleAge sale={sale} />
        <div className="table-wrap"><table><thead><tr><th>Serial</th><th>Product</th><th>SKU / revision</th><th>Unit status</th></tr></thead><tbody>
          {sale.units.map((unit) => <tr key={unit.id}><td>{unit.serial_number}</td><td>{unit.product_number} · {unit.product_name}</td><td>{unit.product_sku} · Rev {unit.revision_number}</td><td><StatusChip status={unit.status} /></td></tr>)}
        </tbody></table></div>
      </section>
      <section className="panel sales-history-panel no-print">
        <div className="panel-heading"><div><h2>Status history</h2><p>Every status transition is recorded with its actor and time.</p></div></div>
        <div className="sales-history-list">{sale.status_history.map((item) => <article className="sales-history-item" key={item.id}>
          <strong>{item.from_status_label || "Sale created"} → {item.to_status_label}</strong>
          <span>{item.actor_name || "System"} · {formatSaleDateTime(item.timestamp)}</span>
          {item.note && <p>{item.note}</p>}
        </article>)}</div>
      </section>
      {sale.returns.length > 0 && <section className="panel sales-return-list no-print">
        <div className="panel-heading"><div><h2>Returns and inspection</h2><p>Returned finished goods are held out of stock until a Super Admin decides their disposition.</p></div></div>
        <div className="table-wrap"><table><thead><tr><th>Serial</th><th>Reason</th><th>Unit status</th><th>Decision</th><th>Work order</th></tr></thead><tbody>
          {sale.returns.map((item) => <tr key={item.id}><td>{item.serial_number}</td><td>{item.reason}</td><td><StatusChip status={item.unit_status} /></td><td>{item.decision === "PENDING" ? "Awaiting inspection" : `${item.decision} · ${item.decision_note || item.decided_by_name}`}</td><td>{item.rework_order_number ? <Link to={`/work-orders/${item.rework_order}`}>{item.rework_order_number}</Link> : "—"}</td></tr>)}
        </tbody></table></div>
        {user?.role === "SUPER_ADMIN" && sale.returns.some((item) => item.decision === "PENDING") && <button className="catalog-primary-button" onClick={() => setDecisionPanel(true)} type="button">Decide return disposition</button>}
        {decisionPanel && user?.role === "SUPER_ADMIN" && <div className="sales-dialog-backdrop"><form aria-labelledby="sales-disposition-title" aria-modal="true" className="sales-return-decision sales-dialog sales-disposition-dialog" onSubmit={decideReturn} role="dialog">
          <div className="panel-heading"><div><h2 id="sales-disposition-title">Inspection decision</h2><p>Select returned serials and review what each decision does before confirming.</p></div><button className="catalog-text-link" onClick={() => setDecisionPanel(false)} type="button">Close</button></div>
          {sale.returns.filter((item) => item.decision === "PENDING").map((item) => <label className="sales-return-choice" key={item.id}><input type="checkbox" checked={decisionSerials.includes(item.serial_number)} onChange={(event) => setDecisionSerials((current) => event.target.checked ? [...current, item.serial_number] : current.filter((serialNumber) => serialNumber !== item.serial_number))} /><span>{item.serial_number}</span></label>)}
          <div className="sales-disposition-explanation" role="status">
            {returnDecision === "RESTOCK" && <p><strong>Restock:</strong> adds 1 unit back to finished goods stock.</p>}
            {returnDecision === "REWORK" && <p><strong>Rework:</strong> creates a rework work order. The server will report any component shortage before it can be created.</p>}
            {returnDecision === "SCRAP" && <p><strong>Scrap:</strong> removes the unit permanently from stock; this cannot be undone.</p>}
          </div>
          <div className="sales-bulk-controls">
            <label>Decision<select value={returnDecision} onChange={(event) => setReturnDecision(event.target.value)}><option value="RESTOCK">Restock as finished good</option><option value="REWORK">Create rework work order</option><option value="SCRAP">Scrap</option></select></label>
            {returnDecision === "REWORK" && <label>Assign Admin<select required value={decisionAdmin} onChange={(event) => setDecisionAdmin(event.target.value)}><option value="">Select Admin</option>{admins.map((admin) => <option value={admin.id} key={admin.id}>{admin.username}</option>)}</select></label>}
            <label>Decision note<input value={decisionNote} onChange={(event) => setDecisionNote(event.target.value)} placeholder="Inspection details" /></label>
            {returnDecision === "SCRAP" && <label>Type a serial number or SCRAP<input required value={scrapConfirmation} onChange={(event) => setScrapConfirmation(event.target.value)} placeholder="Serial number or SCRAP" /></label>}
            <button className="catalog-secondary-button" onClick={() => setDecisionPanel(false)} type="button">Cancel</button>
            <button className="catalog-primary-button" disabled={saving || !decisionSerials.length || (returnDecision === "SCRAP" && !decisionSerials.some((serialNumber) => serialNumber === scrapConfirmation) && scrapConfirmation.trim().toUpperCase() !== "SCRAP")}>{saving ? "Saving decision…" : "Confirm decision"}</button>
          </div>
        </form></div>}
      </section>}
      {isGenerated && canAccessInvoice ? <section className="sale-labels">
        <div className="sale-label-title no-print">
          <span>TAMIRABOT LEGAL LABEL · {labelPreview?.labels?.length || sale.units.length} UNIT{sale.units.length === 1 ? "" : "S"}</span>
          <small>{labelPreview?.template ? `${labelPreview.template.width_mm} × ${labelPreview.template.height_mm} mm · Preview before printing; use the browser dialog to print or save as PDF.` : "A default label template must be configured before printing."}</small>
          {user?.role === "SUPER_ADMIN" && !labelPreview?.template && <Link className="catalog-text-link" to="/label-templates">Configure label template</Link>}
        </div>
        {barcodeError && <SalePageError>{barcodeError}</SalePageError>}
        {labelOverflow && <SalePageError>Some label content exceeds this paper size. Nothing will be truncated; choose A6 or A4, or shorten the address in the sale details.</SalePageError>}
        {labelPreview?.missing_fields?.length > 0 && <div className="form-error" role="alert">Label setup incomplete: {labelPreview.missing_fields.join(", ")}</div>}
        {labelPreview?.labels?.map((label) => {
          const sizeClass = labelPreview.template
            ? `label-size-${labelPreview.template.width_mm}x${labelPreview.template.height_mm}`
            : "";
          const toggles = label.field_toggles || {};
          return <article className={`tamirabot-legal-label ${sizeClass}`} key={label.serial_number}>
            <header className="legal-label-header">
              <div>{toggles.show_company_logo && label.company_logo && <img alt="" src={label.company_logo} />}
                {toggles.show_company_trade_name && label.company_trade_name && <strong>{label.company_trade_name}</strong>}
              </div>
              <span>SHIP TO</span>
            </header>
            <section className="legal-label-block legal-label-ship">
              <strong>{label.customer_name}</strong>
              <p data-fit-label-address>{label.delivery_address}</p>
              <p>Phone: {label.customer_phone}</p>
              <p><b>{label.display_number_label}:</b> {label.display_number}</p>
              <p><b>Date:</b> {formatSaleDate(label.sale_date)}</p>
              <p><b>Return address:</b> <span data-fit-label-address>{label.company_return_address}</span></p>
            </section>
            <section className="legal-label-block legal-label-product">
              <span>PRODUCT</span>
              <strong>{label.product_name}</strong>
              {toggles.show_product_generic_name && label.product_generic_name && <p>{label.product_generic_name}</p>}
              <p>SKU: {label.product_sku} · Revision: {label.revision_number}</p>
              <svg aria-label={`Code128 barcode for ${label.serial_number}`} data-tamirabot-label-barcode={label.barcode_value} />
              <strong className="legal-label-serial">{label.serial_number}</strong>
            </section>
            <section className="legal-label-block legal-label-declarations">
              <span>LEGAL METROLOGY DECLARATIONS</span>
              <p><b>MRP:</b> Rs {label.mrp} incl. of all taxes</p>
              <p><b>Net quantity:</b> {label.net_quantity_text}</p>
              <p><b>Manufactured / packed:</b> {label.manufactured_month_year}</p>
              <p><b>Mfd/Pkd by:</b> <span data-fit-label-address>{label.packer_manufacturer_name}, {label.packer_address}</span></p>
              <p><b>Country of origin:</b> {label.country_of_origin}</p>
              <p><b>Consumer care:</b> <span data-fit-label-address>{label.consumer_care_name}, {label.consumer_care_address}, {label.consumer_care_phone}, {label.consumer_care_email}</span></p>
            </section>
            {label.missing_fields.length > 0 && <p className="legal-label-missing">Cannot print. Missing: {label.missing_fields.join(", ")}</p>}
          </article>;
        })}
        {!labelPreview?.labels?.length && !labelPreview?.missing_fields?.length && <div className="empty-state">No finished units are available for this sale.</div>}
      </section> : isGenerated ? <section className="sale-labels">
        <div className="sale-label-title no-print"><span>PACKING LABEL · {sale.units.length} UNIT{sale.units.length === 1 ? "" : "S"}</span><small>Legal label details are available to users with Sales overview access.</small></div>
        {sale.label.units.map((unit) => <article className="sale-label-card" key={unit.serial_number}>
          <div className="sale-label-brand">TAMIRABOT <span>{saleStatusLabels[sale.status].toUpperCase()}</span></div>
          <div className="sale-label-main">
            <div className="sale-label-product"><strong>{unit.product_name}</strong><span>{unit.product_number} · {unit.product_sku} · Rev {unit.revision_number}</span><span>Finished unit: {unit.serial_number}</span></div>
            <div className="sale-label-recipient"><strong>SHIP TO</strong><b>{sale.label.recipient.name}</b><span>{sale.label.recipient.address}</span><span>{[sale.label.recipient.city, sale.label.recipient.state, sale.label.recipient.postal_code].filter(Boolean).join(", ")}</span>{sale.label.recipient.phone && <span>Phone: {sale.label.recipient.phone}</span>}</div>
          </div>
          <div className="sale-label-order"><span>ORDER REFERENCE</span><strong>{sale.label.order_reference || `SALE-${sale.id}`}</strong></div>
          <svg aria-label={`Barcode ${unit.serial_number}`} data-sale-barcode={unit.barcode_value} />
          <div className="sale-label-serial">{unit.serial_number}</div>
        </article>)}
      </section> : <section className="panel seller-label-reference no-print">
        <div className="panel-heading"><div><h2>Seller-provided label</h2><p>This platform’s label is supplied by the seller and is not generated by Tamirabot.</p></div></div>
        {/^(https?:\/\/)/i.test(sale.label.reference) ? <a href={sale.label.reference} target="_blank" rel="noreferrer">{sale.label.reference}</a> : <p>{sale.label.reference}</p>}
      </section>}
    </section>
  );
}

function SaleEditForm({ sale, editForm, platforms, isGenerated, onChange, onSave, onClose, saving }) {
  const formRef = useRef(null);
  const unsaved = useUnsavedChanges(formRef, undefined, onClose);
  function handleChange(event) {
    onChange(event);
    window.requestAnimationFrame(unsaved.updateDirty);
  }
  return <>
    <form className="panel sales-edit-form no-print" onSubmit={onSave} ref={formRef}>
      <div className="panel-heading"><div><h2>Edit sale details</h2><p>Prices are fixed from the product price list. Platform, customer/order reference, and notes are recorded in the audit log.</p></div><button className="catalog-text-link" onClick={unsaved.requestClose} type="button">Cancel edit</button></div>
      <div className="sales-form-grid">
        <label>Platform<select name="platform" value={editForm.platform} onChange={handleChange} required>{platforms.map((item) => <option value={item.id} key={item.id}>{item.name}{item.active ? "" : " (Inactive)"}</option>)}{!platforms.some((item) => String(item.id) === editForm.platform) && <option value={editForm.platform}>{sale.platform_name}</option>}</select></label>
        <label>Customer / order reference<input name="order_reference" value={editForm.order_reference} onChange={handleChange} /></label>
        <label>Sale date<input name="sale_date" type="date" value={editForm.sale_date} onChange={handleChange} required /></label>
        {isGenerated ? <>
          <label>Recipient name<input name="shipping_name" value={editForm.shipping_name} onChange={handleChange} required /></label>
          <label className="sales-form-wide">Address<input name="shipping_address" value={editForm.shipping_address} onChange={handleChange} required /></label>
          <label>City<input name="shipping_city" value={editForm.shipping_city} onChange={handleChange} /></label>
          <label>State<input name="shipping_state" value={editForm.shipping_state} onChange={handleChange} readOnly={sale.pricing_source === "price_list"} /></label>
          <label>Postal code<input name="shipping_postal_code" value={editForm.shipping_postal_code} onChange={handleChange} /></label>
          <label>Phone<input name="shipping_phone" value={editForm.shipping_phone} onChange={handleChange} /></label>
        </> : <label className="sales-form-wide">Seller label / reference<input name="seller_label_reference" value={editForm.seller_label_reference} onChange={handleChange} required /></label>}
        <label className="sales-form-wide">Notes<textarea name="notes" rows="3" value={editForm.notes} onChange={handleChange} /></label>
      </div>
      <div className="catalog-form-actions"><button className="catalog-primary-button" disabled={saving}>{saving ? "Saving…" : "Save changes"}</button></div>
    </form>
    <UnsavedChangesDialog open={unsaved.confirming} onSave={() => unsaved.saveFromDialog()} onDiscard={unsaved.discard} onKeepEditing={unsaved.keepEditing} />
  </>;
}

export function SalesPlatformsPage({ token }) {
  const [platforms, setPlatforms] = useState([]);
  const [form, setForm] = useState({ name: "", label_template_type: "SELLER_PROVIDED", issue_tamirabot_invoice: false });
  const [editingId, setEditingId] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => request("/api/platforms/", token)
    .then(setPlatforms)
    .catch((failure) => setError(failure.message))
    .finally(() => setLoading(false)), [token]);
  useEffect(() => { load(); }, [load]);

  async function savePlatform(event) {
    event.preventDefault();
    setError("");
    try {
      const path = editingId ? `/api/platforms/${editingId}/` : "/api/platforms/";
      await request(path, token, {
        method: editingId ? "PATCH" : "POST",
        body: JSON.stringify(form),
      });
      setForm({ name: "", label_template_type: "SELLER_PROVIDED", issue_tamirabot_invoice: false });
      setEditingId(null);
      await load();
    } catch (failure) {
      setError(failure.message);
    }
  }

  function beginEdit(platform) {
    setEditingId(platform.id);
    setForm({
      name: platform.name,
      label_template_type: platform.label_template_type,
      issue_tamirabot_invoice: platform.issue_tamirabot_invoice,
    });
  }

  async function toggleActive(platform) {
    setError("");
    try {
      await request(`/api/platforms/${platform.id}/`, token, {
        method: "PATCH",
        body: JSON.stringify({ active: !platform.active }),
      });
      await load();
    } catch (failure) {
      setError(failure.message);
    }
  }

  return (
    <section className="page-content sales-page">
      <Link className="catalog-back-link" to="/sales">← Sales register</Link>
      <div className="page-heading"><div><div className="eyebrow">SALES TRACKING / SETTINGS</div><h1>Selling platforms</h1><p className="page-subtitle">Choose label handling and whether Tamirabot or the marketplace issues the invoice.</p></div></div>
      <SalePageError>{error}</SalePageError>
      <form className="panel sales-platform-form" onSubmit={savePlatform}>
        <div className="panel-heading"><div><h2>{editingId ? "Edit platform" : "Add platform"}</h2><p>Tamirabot creates a print-ready packing label. Other platforms use a seller-provided label or reference.</p></div></div>
        <div className="sales-platform-fields">
          <label>Platform name<input required maxLength="120" value={form.name} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder="Amazon, Flipkart, or custom name" /></label>
          <label>Label template<select value={form.label_template_type} onChange={(event) => setForm((current) => ({ ...current, label_template_type: event.target.value }))}><option value="TAMIRABOT">Tamirabot generated packing label</option><option value="SELLER_PROVIDED">Seller-provided label / reference</option></select></label>
          <label className="sales-invoice-toggle"><input checked={form.issue_tamirabot_invoice} onChange={(event) => setForm((current) => ({ ...current, issue_tamirabot_invoice: event.target.checked }))} type="checkbox" /> Tamirabot issues invoices for this platform</label>
          <div className="sales-platform-actions"><button className="catalog-primary-button">{editingId ? "Save platform" : "Add platform"}</button>{editingId && <button className="catalog-secondary-button" onClick={() => { setEditingId(null); setForm({ name: "", label_template_type: "SELLER_PROVIDED", issue_tamirabot_invoice: false }); }} type="button">Cancel</button>}</div>
        </div>
      </form>
      <section className="panel sales-register">
        <div className="panel-heading"><div><h2>Platform register</h2><p>Inactive platforms remain on past sales but cannot be selected for new ones.</p></div></div>
        {loading ? <p className="muted">Loading platforms…</p> : <div className="table-wrap"><table><thead><tr><th>Name</th><th>Label type</th><th>Invoice issuer</th><th>Status</th><th /></tr></thead><tbody>
          {platforms.map((platform) => <tr key={platform.id}>
            <td><strong>{platform.name}</strong></td>
            <td>{platform.label_template_type === "TAMIRABOT" ? "Tamirabot generated label" : "Seller-provided"}</td>
            <td>{platform.issue_tamirabot_invoice ? "Tamirabot" : "Marketplace"}</td>
            <td><span className={`status-tag ${platform.active ? "status-completed" : "status-blocked"}`}>{platform.active ? "Active" : "Inactive"}</span></td>
            <td className="sales-row-actions"><button className="catalog-text-link" onClick={() => beginEdit(platform)} type="button">Edit</button><button className="catalog-text-link" onClick={() => toggleActive(platform)} type="button">{platform.active ? "Deactivate" : "Activate"}</button></td>
          </tr>)}
          {!platforms.length && <tr><td colSpan="5">No selling platforms configured.</td></tr>}
        </tbody></table></div>}
      </section>
    </section>
  );
}
