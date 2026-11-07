import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { apiRequest, useNotify } from "./notifications.jsx";
import { formatMoney } from "./moneyFormat.js";
import { formatCatalogDateTime } from "./catalogFormat.js";
import {
  UnsavedChangesDialog,
  useUnsavedChanges,
} from "./useUnsavedChanges.jsx";

function costLabel(value, missingCount) {
  return value === null
    ? `Incomplete · ${missingCount} unpriced component${missingCount === 1 ? "" : "s"}`
    : formatMoney(value);
}

export default function ComponentPricingPage({ token }) {
  const { notify } = useNotify();
  const [components, setComponents] = useState([]);
  const [drafts, setDrafts] = useState({});
  const draftRef = useRef(drafts);
  const [priceNotSet, setPriceNotSet] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const formRef = useRef(null);
  draftRef.current = drafts;
  const unsaved = useUnsavedChanges(
    formRef,
    () => ({ drafts: draftRef.current }),
  );

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    const query = priceNotSet ? "?price_not_set=true" : "";
    apiRequest(`/api/components/pricing/${query}`, token)
      .then((result) => {
        if (!active) return;
        setComponents(result);
        setDrafts({});
        draftRef.current = {};
      })
      .catch((requestError) => {
        if (active) setError(requestError.message || "Pricing could not be loaded. Retry.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [priceNotSet, retry, token]);

  function updateDraft(component, field, value) {
    setDrafts((current) => {
      const next = {
        ...current,
        [component.id]: {
          cost_price: current[component.id]?.cost_price ?? component.cost_price ?? "0.00",
          reason: current[component.id]?.reason ?? "",
          ...current[component.id],
          [field]: value,
        },
      };
      draftRef.current = next;
      window.requestAnimationFrame(unsaved.updateDirty);
      return next;
    });
  }

  const changedComponents = components.filter((component) => {
    const draft = drafts[component.id];
    if (!draft) return false;
    const next = draft.cost_price === "" ? null : draft.cost_price;
    return next !== component.cost_price;
  });

  async function savePrices(event) {
    event?.preventDefault();
    if (!changedComponents.length || saving) return;
    setSaving(true);
    setError("");
    try {
      const result = await apiRequest("/api/components/pricing/", token, {
        method: "POST",
        body: JSON.stringify({
          items: changedComponents.map((component) => {
            const draft = drafts[component.id];
            return {
              component_id: component.id,
              cost_price: draft.cost_price === "" ? null : draft.cost_price,
              reason: draft.reason || "",
            };
          }),
        }),
      });
      const names = changedComponents.map((component) => `${component.sku} · ${component.name}`);
      notify(
        `${result.updated_count} component price${result.updated_count === 1 ? "" : "s"} updated: ${names.join(", ")}`,
        "success",
      );
      draftRef.current = {};
      setDrafts({});
      setRetry((value) => value + 1);
      window.requestAnimationFrame(unsaved.updateDirty);
    } catch (requestError) {
      setError(requestError.message || "Prices could not be saved. No prices were changed.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="page-content component-pricing-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow">TAMIRABOT / CATALOG</div>
          <h1>Component pricing</h1>
          <p className="page-subtitle">Purchase prices in INR, excluding GST. Only Super Admins can view or change these values.</p>
        </div>
        <Link className="catalog-secondary-button" to="/components">All components</Link>
      </div>
      <div className="catalog-toolbar">
        <label className="catalog-check price-not-set-filter">
          <input
            checked={priceNotSet}
            onChange={(event) => setPriceNotSet(event.target.checked)}
            type="checkbox"
          />
          Price not set
        </label>
        <span className="catalog-count">{components.length} components</span>
      </div>
      {error && (
        <div className="catalog-alert" role="alert">
          {error}
          <button className="catalog-text-link" onClick={() => setRetry((value) => value + 1)} type="button">Retry</button>
        </div>
      )}
      <form className="component-pricing-form" onSubmit={savePrices} ref={formRef}>
        <div className="catalog-table-wrap">
          <table className="catalog-table component-pricing-table">
            <thead>
              <tr><th>COMPONENT</th><th>PURCHASE PRICE</th><th>LAST CHANGED</th><th>PRODUCTS USING CURRENT BOM</th></tr>
            </thead>
            <tbody>
              {!loading && components.map((component) => {
                const draft = drafts[component.id] || {
                  cost_price: component.cost_price ?? "0.00",
                  reason: "",
                };
                const currentValue = draft.cost_price === "" ? null : draft.cost_price;
                const changed = currentValue !== component.cost_price;
                return (
                  <tr key={component.id}>
                    <td>
                      <Link className="catalog-product-cell" to={`/components/${component.id}`}>
                        <span><strong>{component.name}</strong><small>{component.sku} · {component.unit}</small></span>
                      </Link>
                    </td>
                    <td>
                      <label className="pricing-input-label">
                        <span className="sr-only">Purchase price for {component.name}</span>
                        <input
                          aria-label={`Purchase price for ${component.name}`}
                          inputMode="decimal"
                          name={`price-${component.id}`}
                          onChange={(event) => updateDraft(component, "cost_price", event.target.value)}
                          pattern="(?:0|[1-9]\d{0,9})(?:\.\d{1,2})?"
                          placeholder="Price not set"
                          value={draft.cost_price}
                        />
                      </label>
                      {changed && component.cost_price !== null && (
                        <label className="pricing-input-label">
                          <span className="sr-only">Reason for changing {component.name} price</span>
                          <input
                            aria-label={`Reason for changing ${component.name} price`}
                            maxLength="500"
                            name={`reason-${component.id}`}
                            onChange={(event) => updateDraft(component, "reason", event.target.value)}
                            placeholder="Reason required"
                            value={draft.reason}
                          />
                        </label>
                      )}
                    </td>
                    <td>{formatCatalogDateTime(component.last_changed)}</td>
                    <td>
                      {!component.products.length && <span className="pricing-muted">Not used in a current BOM</span>}
                      {component.products.map((product) => (
                        <div className="pricing-product-cost" key={product.product_id}>
                          <Link to={`/products/${product.product_id}`}>{product.product_number} · {product.product_name}</Link>
                          <span>{costLabel(product.computed_cost, product.missing_component_count)}</span>
                        </div>
                      ))}
                    </td>
                  </tr>
                );
              })}
              {loading && Array.from({ length: 5 }, (_, index) => (
                <tr key={`skeleton-${index}`}><td colSpan="4"><span className="pricing-skeleton" /></td></tr>
              ))}
              {!loading && !error && !components.length && (
                <tr><td className="catalog-empty-cell" colSpan="4">
                  {priceNotSet ? "Every component has a price." : "No components yet."}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="catalog-form-actions pricing-actions">
          <span>{changedComponents.length} unsaved price change{changedComponents.length === 1 ? "" : "s"}</span>
          <button className="catalog-primary-button" disabled={!changedComponents.length || saving} type="submit">
            {saving ? "Saving prices…" : `Save ${changedComponents.length || ""} price${changedComponents.length === 1 ? "" : "s"}`}
          </button>
        </div>
      </form>
      <UnsavedChangesDialog
        open={unsaved.confirming}
        onSave={() => unsaved.saveFromDialog(savePrices)}
        onDiscard={unsaved.discard}
        onKeepEditing={unsaved.keepEditing}
      />
    </section>
  );
}
