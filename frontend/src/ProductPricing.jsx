import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { apiRequest, useNotify } from "./notifications.jsx";
import { formatCatalogDateTime } from "./catalogFormat.js";
import { formatMoney } from "./moneyFormat.js";
import { UnsavedChangesDialog, useUnsavedChanges } from "./useUnsavedChanges.jsx";

async function request(path, token, options = {}) {
  return apiRequest(path, token, options);
}

export default function ProductPricingPage({ token }) {
  const { notify } = useNotify();
  const formRef = useRef(null);
  const pendingProductId = useRef("");
  const [products, setProducts] = useState([]);
  const [taxRates, setTaxRates] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState(null);
  const [margin, setMargin] = useState("0.00");
  const [taxRateId, setTaxRateId] = useState("");
  const [sellingPrice, setSellingPrice] = useState("");
  const [mrp, setMrp] = useState("");
  const [hsnCode, setHsnCode] = useState("");
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [rateError, setRateError] = useState("");
  const [newRate, setNewRate] = useState({ rate_percent: "", label: "" });
  const [rateSaving, setRateSaving] = useState(false);
  const [detailRefresh, setDetailRefresh] = useState(0);
  const unsaved = useUnsavedChanges(
    formRef,
    () => ({ margin, taxRateId, sellingPrice, mrp, hsnCode, reason }),
    () => {
      if (pendingProductId.current) {
        setSelectedId(pendingProductId.current);
        pendingProductId.current = "";
      }
    },
  );

  async function loadLists() {
    setLoading(true);
    setError("");
    try {
      const [productData, rates] = await Promise.all([
        request("/api/products/pricing/", token),
        request("/api/tax-rates/", token),
      ]);
      setProducts(productData.products);
      setTaxRates(rates);
      setTaxRateId((current) => current || rates.find((rate) => rate.active)?.id?.toString() || "");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadLists();
  }, [token]);

  useEffect(() => {
    if (detail) unsaved.resetClean();
  }, [detail?.id, detailRefresh]);

  useEffect(() => {
    unsaved.updateDirty();
  }, [margin, taxRateId, sellingPrice, mrp, hsnCode, reason]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let active = true;
    setDetail(null);
    request(`/api/products/${selectedId}/`, token)
      .then((result) => {
        if (!active) return;
        const price = result.product_price_summary;
        setDetail(result);
        if (price?.latest_price_id) {
          setMargin(price.margin_percent || "0.00");
          setSellingPrice(price.selling_price_incl_tax || "");
          setMrp(price.mrp || "");
          setHsnCode(price.hsn_code || "");
          const matchingRate = taxRates.find((rate) => rate.rate_percent === price.gst_rate_percent);
          if (matchingRate) setTaxRateId(matchingRate.id.toString());
          setReason("");
        } else {
          setMargin("0.00");
          setSellingPrice("");
          setMrp("");
          setHsnCode("");
          setReason("");
        }
      })
      .catch((requestError) => {
        if (active) setError(requestError.message);
      });
    return () => {
      active = false;
    };
  }, [selectedId, token, taxRates, detailRefresh]);

  useEffect(() => {
    if (!selectedId || !taxRateId || !/^\d+(?:\.\d{1,2})?$/.test(margin)) {
      setPreview(null);
      return undefined;
    }
    let active = true;
    const timer = window.setTimeout(() => {
      const query = new URLSearchParams({
        margin_percent: margin,
        tax_rate_id: taxRateId,
      });
      request(`/api/products/${selectedId}/pricing/preview/?${query}`, token)
        .then((result) => {
          if (active) setPreview(result);
        })
        .catch((requestError) => {
          if (active) {
            setPreview(null);
            setError(requestError.message);
          }
        });
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [selectedId, margin, taxRateId, token]);

  async function savePrice(event) {
    event.preventDefault();
    if (!selectedId || !preview?.complete) return;
    setSaving(true);
    setError("");
    const previous = detail.product_price_summary?.selling_price_incl_tax;
    try {
      await request(`/api/products/${selectedId}/prices/`, token, {
        method: "POST",
        body: JSON.stringify({
          margin_percent: margin,
          selling_price_incl_tax: sellingPrice,
          mrp,
          hsn_code: hsnCode,
          tax_rate_id: taxRateId,
          reason,
        }),
      });
      notify(
        `${detail.product_number} ${detail.name} selling price: ${previous ? formatMoney(previous) : "Not set"} → ${formatMoney(sellingPrice)}`,
        "success",
      );
      await loadLists();
      setDetailRefresh((count) => count + 1);
      if (pendingProductId.current) {
        setSelectedId(pendingProductId.current);
        pendingProductId.current = "";
      }
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  async function saveTaxRate(event, rate) {
    event.preventDefault();
    setRateError("");
    setRateSaving(true);
    const data = new FormData(event.currentTarget);
    try {
      await request(`/api/tax-rates/${rate.id}/`, token, {
        method: "PATCH",
        body: JSON.stringify({
          rate_percent: data.get("rate_percent"),
          label: data.get("label"),
          active: data.get("active") === "on",
        }),
      });
      notify(`Tax rate ${rate.label} updated to ${data.get("rate_percent")}%`, "success");
      await loadLists();
    } catch (requestError) {
      setRateError(requestError.message);
    } finally {
      setRateSaving(false);
    }
  }

  async function createTaxRate(event) {
    event.preventDefault();
    setRateError("");
    setRateSaving(true);
    try {
      await request("/api/tax-rates/", token, {
        method: "POST",
        body: JSON.stringify({ ...newRate, active: true }),
      });
      notify(`Tax rate ${newRate.label} added at ${newRate.rate_percent}%`, "success");
      setNewRate({ rate_percent: "", label: "" });
      await loadLists();
    } catch (requestError) {
      setRateError(requestError.message);
    } finally {
      setRateSaving(false);
    }
  }

  const selectedProduct = products.find((product) => product.id.toString() === selectedId);

  function selectProduct(productId) {
    const target = productId.toString();
    if (target === selectedId) return;
    pendingProductId.current = target;
    unsaved.requestClose();
  }

  return (
    <section className="page-content product-pricing-page">
      <div className="page-heading">
        <div><div className="eyebrow">TAMIRABOT / CATALOG</div><h1>Product Pricing</h1><p className="page-subtitle">Set tax-inclusive selling prices and MRP. Each save creates a new effective price record.</p></div>
      </div>
      {error && <div className="catalog-alert" role="alert">{error}</div>}
      {loading ? <div className="loading-screen">Loading product prices…</div> : (
        <div className="product-pricing-layout">
          <section className="panel product-pricing-list">
            <div className="panel-heading"><div><h2>Products</h2><p>Current BOM costs and active price status.</p></div></div>
            <div className="catalog-table-wrap">
              <table className="catalog-table product-pricing-table">
                <thead><tr><th>PRODUCT</th><th>COST</th><th>SELLING</th><th>MRP</th><th>GST</th><th>HSN</th><th>STATUS</th><th>LAST CHANGED</th></tr></thead>
                <tbody>
                  {products.map((product) => (
                    <tr className={selectedId === product.id.toString() ? "selected" : ""} key={product.id}>
                      <td><button className="pricing-product-select" onClick={() => selectProduct(product.id)} type="button">{product.product_number} · {product.name}</button></td>
                      <td>{product.computed_cost ? formatMoney(product.computed_cost) : "Incomplete"}</td>
                      <td>{product.selling_price_incl_tax ? formatMoney(product.selling_price_incl_tax) : "—"}</td>
                      <td>{product.mrp ? formatMoney(product.mrp) : "—"}</td>
                      <td>{product.gst_rate_percent !== null ? `${product.gst_rate_percent}%` : "—"}</td>
                      <td>{product.hsn_code || "—"}</td>
                      <td><span className={`status-pill ${product.status === "Complete" ? "is-active" : "is-inactive"}`}>{product.status}</span>{product.cost_changed_review && <span className="pricing-review-badge">Cost changed: review price</span>}</td>
                      <td>{formatCatalogDateTime(product.last_changed)}</td>
                    </tr>
                  ))}
                  {!products.length && <tr><td className="catalog-empty-cell" colSpan="8">No products yet. Create a product before setting a price.</td></tr>}
                </tbody>
              </table>
            </div>
          </section>
          {selectedId && (
            <section className="panel product-pricing-editor">
              {!detail ? <div className="loading-screen">Loading price history…</div> : (
                <>
                  <div className="panel-heading"><div><div className="eyebrow">PRICE EDITOR</div><h2>{selectedProduct?.product_number} · {selectedProduct?.name}</h2></div><Link to={`/products/${selectedId}`} className="catalog-text-link">Product detail</Link></div>
                  <form className="product-pricing-form" onChange={unsaved.updateDirty} onInput={unsaved.updateDirty} onSubmit={savePrice} ref={formRef}>
                    {preview?.missing_components?.length > 0 && <div className="catalog-alert" role="alert">Price cannot be saved. Set prices for: {preview.missing_components.join(", ")}</div>}
                    <div className="product-pricing-fields">
                      <label>Margin %<input inputMode="decimal" onChange={(event) => setMargin(event.target.value)} value={margin} /></label>
                      <label>GST rate<select onChange={(event) => setTaxRateId(event.target.value)} value={taxRateId}>{taxRates.filter((rate) => rate.active).map((rate) => <option key={rate.id} value={rate.id}>{rate.rate_percent}% · {rate.label}</option>)}</select></label>
                      <label>Selling price incl. tax<input inputMode="decimal" onChange={(event) => setSellingPrice(event.target.value)} required value={sellingPrice} /></label>
                      <label>MRP incl. tax<input inputMode="decimal" onChange={(event) => setMrp(event.target.value)} required value={mrp} /></label>
                      <label>HSN code<input inputMode="numeric" maxLength="8" onChange={(event) => setHsnCode(event.target.value)} pattern="(?:[0-9]{4}|[0-9]{6}|[0-9]{8})" required value={hsnCode} /></label>
                      <label>Reason {detail.product_price_summary?.latest_price_id ? "(required)" : ""}<input onChange={(event) => setReason(event.target.value)} required={Boolean(detail.product_price_summary?.latest_price_id)} value={reason} /></label>
                    </div>
                    <div className="product-pricing-breakdown">
                      <h3>Suggested price breakdown</h3>
                      {preview ? preview.complete ? (
                        <dl>
                          <div><dt>Cost</dt><dd>{formatMoney(preview.cost)}</dd></div>
                          <div><dt>Margin</dt><dd>{formatMoney(preview.margin_amount)} ({preview.margin_percent}%)</dd></div>
                          <div><dt>Taxable value</dt><dd>{formatMoney(preview.taxable_value)}</dd></div>
                          <div><dt>GST · {preview.gst_rate_percent}%</dt><dd>{formatMoney(preview.gst_amount)}</dd></div>
                          <div className="suggested-total"><dt>Suggested selling price</dt><dd>{formatMoney(preview.suggested_selling_price)}</dd></div>
                        </dl>
                      ) : <p>Computed cost is incomplete. {preview.missing_components?.join(", ")}</p> : <p>Enter a valid margin and select an active GST rate to preview.</p>}
                      <button className="catalog-secondary-button" disabled={!preview?.complete} onClick={() => setSellingPrice(preview.suggested_selling_price)} type="button">Apply suggested price</button>
                    </div>
                    <div className="catalog-form-actions"><button className="catalog-primary-button" disabled={saving || !preview?.complete}>{saving ? "Saving…" : "Save new price"}</button></div>
                  </form>
                  <div className="product-price-history">
                    <h3>Price history</h3>
                    {detail.product_price_summary?.history?.length ? <ol>{detail.product_price_summary.history.map((entry) => (
                      <li key={entry.id}><strong>{formatMoney(entry.selling_price_incl_tax)} · MRP {formatMoney(entry.mrp)}</strong><span>Cost {formatMoney(entry.cost_snapshot)} · GST {entry.gst_rate_percent}% · HSN {entry.hsn_code}</span><span>{formatCatalogDateTime(entry.effective_from)} · {entry.reason || "No reason supplied"}</span></li>
                    ))}</ol> : <p className="pricing-muted">No price has been set for this product.</p>}
                  </div>
                </>
              )}
            </section>
          )}
        </div>
      )}
      <section className="panel product-tax-rates">
        <div className="panel-heading"><div><h2>Tax rates</h2><p>Examples are editable. Confirm applicable GST rates with your accountant.</p></div></div>
        {rateError && <div className="catalog-alert" role="alert">{rateError}</div>}
        <div className="tax-rate-list">
          {taxRates.map((rate) => (
            <form className="tax-rate-row" key={rate.id} onSubmit={(event) => saveTaxRate(event, rate)}>
              <label>Rate %<input defaultValue={rate.rate_percent} inputMode="decimal" name="rate_percent" required /></label>
              <label>Label<input defaultValue={rate.label} name="label" required /></label>
              <label className="catalog-check"><input defaultChecked={rate.active} name="active" type="checkbox" /> Active</label>
              <button className="catalog-secondary-button" disabled={rateSaving}>Save rate</button>
            </form>
          ))}
          <form className="tax-rate-row" onSubmit={createTaxRate}>
            <label>Rate %<input inputMode="decimal" onChange={(event) => setNewRate((current) => ({ ...current, rate_percent: event.target.value }))} required value={newRate.rate_percent} /></label>
            <label>Label<input onChange={(event) => setNewRate((current) => ({ ...current, label: event.target.value }))} required value={newRate.label} /></label>
            <span className="pricing-muted">New active rate</span>
            <button className="catalog-primary-button" disabled={rateSaving}>{rateSaving ? "Saving…" : "Add rate"}</button>
          </form>
        </div>
      </section>
      <p className="pricing-accountant-note">HSN codes and GST rates must be confirmed with your accountant.</p>
      <UnsavedChangesDialog open={unsaved.confirming} onSave={unsaved.saveFromDialog} onDiscard={unsaved.discard} onKeepEditing={unsaved.keepEditing} />
    </section>
  );
}
