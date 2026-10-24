import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { apiRequest, newlyReservedWorkOrders, useNotify } from "./notifications.jsx";

async function request(path, token, options = {}) {
  return apiRequest(path, token, options);
}

function Alert({ message, success = false }) {
  if (!message) return null;
  return <div className={`catalog-alert ${success ? "success" : ""}`} role={success ? "status" : "alert"}>{message}</div>;
}

function Quantity({ value, unit }) {
  return <>{value} <small>{unit}</small></>;
}

export function InventoryPage({ token, user }) {
  const [items, setItems] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [adjustments, setAdjustments] = useState([]);
  const [search, setSearch] = useState("");
  const [showReceive, setShowReceive] = useState(false);
  const [showAdjustment, setShowAdjustment] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0);
  const { changed, notify, stockDialog } = useNotify();
  const warnedLowStock = useRef(new Set());
  const canManage = user.role === "SUPER_ADMIN";

  const load = useCallback(async () => {
    try {
      const query = new URLSearchParams();
      if (search.trim()) query.set("search", search.trim());
      const stock = await request(`/api/inventory/?${query}`, token);
      setItems(stock);
      stock.forEach((item) => {
        const id = item.component.id;
        if (item.low_stock && !warnedLowStock.current.has(id)) {
          notify(`${item.component.name} (${item.component.sku}) is at or below its reorder level.`, "warning");
          warnedLowStock.current.add(id);
        } else if (!item.low_stock) {
          warnedLowStock.current.delete(id);
        }
      });
      if (canManage) {
        const [lowStock, pending] = await Promise.all([
          request("/api/inventory/low-stock/", token),
          request("/api/inventory/adjustments/?status=pending", token),
        ]);
        setAlerts(lowStock);
        setAdjustments(pending);
      }
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    }
  }, [token, search, canManage, notify]);

  useEffect(() => {
    load();
  }, [load, refresh]);

  async function receiveStock(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setError("");
    try {
      const componentId = Number(form.get("component_id"));
      const quantity = form.get("quantity");
      const beforeOrders = canManage
        ? await request("/api/work-orders/", token).catch(() => null)
        : null;
      const movement = await request("/api/inventory/receive/", token, {
        method: "POST",
        body: JSON.stringify({
          component_id: componentId,
          quantity,
          note: form.get("note"),
        }),
      });
      const [detail, afterOrders] = await Promise.all([
        request(`/api/inventory/${componentId}/`, token).catch(() => null),
        beforeOrders ? request("/api/work-orders/", token).catch(() => null) : Promise.resolve(null),
      ]);
      const previous = items.find((item) => item.component.id === componentId);
      const component = detail?.component || previous?.component;
      const received = Number(movement.quantity || quantity);
      const onHand = detail?.balance.on_hand ?? Number(previous?.on_hand || 0) + received;
      const available = detail?.balance.available ?? Number(previous?.available || 0) + received;
      changed("stockReceived", {
        name: component.name,
        sku: component.sku,
        quantity: movement.quantity || quantity,
        onHand,
        available,
      });
      if (Number(available) <= Number(component.reorder_level)) {
        notify(`${component.name} (${component.sku}) is at or below its reorder level.`, "warning");
        warnedLowStock.current.add(component.id);
      } else {
        warnedLowStock.current.delete(component.id);
      }
      const newlyReserved = newlyReservedWorkOrders(beforeOrders, afterOrders);
      if (newlyReserved.length) {
        changed("workOrdersReserved", {
          quantity: newlyReserved.length,
          name: newlyReserved.map((order) => order.work_order_number).join(", "),
        });
      }
      setNotice("Stock received and recorded in the ledger.");
      setShowReceive(false);
      setRefresh((value) => value + 1);
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function submitAdjustment(event) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setError("");
    try {
      const result = await request("/api/inventory/adjustments/", token, {
        method: "POST",
        body: JSON.stringify({
          component_id: Number(form.get("component_id")),
          delta: form.get("delta"),
          reason: form.get("reason"),
        }),
      });
      changed("adjustmentRequested", {
        name: result.component.name,
        sku: result.component.sku,
        quantity: result.delta,
      });
      setNotice("Adjustment submitted for approval. Stock counts are unchanged until approved.");
      setShowAdjustment(false);
      setRefresh((value) => value + 1);
    } catch (requestError) {
      setError(requestError.message);
      if (/stock.{0,30}negative|insufficient|short by/i.test(requestError.message)) {
        const componentId = Number(form.get("component_id"));
        const item = items.find((row) => row.component.id === componentId);
        const required = Math.abs(Number(form.get("delta")));
        const available = Number(item?.available || 0);
        stockDialog(requestError.message, [{
          name: item?.component.name,
          sku: item?.component.sku,
          required,
          available,
          shortBy: Math.max(0, required - available),
        }]);
      }
    }
  }

  async function reviewAdjustment(adjustment, decision) {
    setError("");
    try {
      const beforeOrders = decision === "approve"
        ? await request("/api/work-orders/", token).catch(() => null)
        : null;
      const result = await request(`/api/inventory/adjustments/${adjustment.id}/${decision}/`, token, {
        method: "POST",
        body: JSON.stringify({}),
      });
      const afterOrders = beforeOrders
        ? await request("/api/work-orders/", token).catch(() => null)
        : null;
      changed(decision === "approve" ? "adjustmentApproved" : "adjustmentRejected", {
        name: result.component.name,
        sku: result.component.sku,
        quantity: result.delta,
      });
      const newlyReserved = newlyReservedWorkOrders(beforeOrders, afterOrders);
      if (newlyReserved.length) {
        changed("workOrdersReserved", {
          quantity: newlyReserved.length,
          name: newlyReserved.map((order) => order.work_order_number).join(", "),
        });
      }
      setNotice(`Adjustment ${decision === "approve" ? "approved" : "rejected"}.`);
      setRefresh((value) => value + 1);
    } catch (requestError) {
      setError(requestError.message);
      if (/stock.{0,30}negative|insufficient|short by/i.test(requestError.message)) {
        const item = items.find((row) => row.component.id === adjustment.component.id);
        const latest = await request(`/api/inventory/${adjustment.component.id}/`, token).catch(() => null);
        const required = Math.abs(Number(adjustment.delta));
        const available = Number(latest?.balance.available ?? item?.available ?? 0);
        stockDialog(requestError.message, [{
          name: latest?.component.name || adjustment.component.name,
          sku: latest?.component.sku || adjustment.component.sku,
          required,
          available,
          shortBy: Math.max(0, required - available),
        }]);
      }
    }
  }

  return (
    <section className="page-content">
      <div className="page-heading">
        <div><div className="eyebrow">TAMIRABOT / INVENTORY</div><h1>Raw material stock</h1><p className="page-subtitle">Ledger-backed component stock and movement history.</p></div>
        {canManage && <div className="inventory-header-actions">
          <button className="catalog-secondary-button" onClick={() => { setShowAdjustment((shown) => !shown); setShowReceive(false); }} type="button">＋ Stock adjustment</button>
          <button className="catalog-primary-button" onClick={() => { setShowReceive((shown) => !shown); setShowAdjustment(false); }} type="button">＋ Receive stock</button>
        </div>}
      </div>
      <Alert message={error} />
      <Alert message={notice} success />

      {showReceive && canManage && (
        <form className="catalog-form inventory-entry-form" onSubmit={receiveStock}>
          <div className="catalog-form-heading"><div><span className="eyebrow">INVENTORY MOVEMENT</span><h2>Receive stock</h2></div></div>
          <div className="catalog-form-grid">
            <label>Component<select name="component_id" required defaultValue=""><option disabled value="">Choose component</option>{items.filter(({ component }) => component.active).map(({ component }) => <option key={component.id} value={component.id}>{component.sku} · {component.name}</option>)}</select></label>
            <label>Quantity<input min="0.001" name="quantity" required step="0.001" type="number" /></label>
            <label className="catalog-wide">Note<input name="note" placeholder="Supplier, purchase reference, or delivery note" /></label>
          </div>
          <div className="catalog-form-actions"><button className="catalog-secondary-button" onClick={() => setShowReceive(false)} type="button">Cancel</button><button className="catalog-primary-button">Record receipt</button></div>
        </form>
      )}

      {showAdjustment && canManage && (
        <form className="catalog-form inventory-entry-form" onSubmit={submitAdjustment}>
          <div className="catalog-form-heading"><div><span className="eyebrow">APPROVAL REQUIRED</span><h2>Request stock adjustment</h2></div></div>
          <div className="catalog-form-grid">
            <label>Component<select name="component_id" required defaultValue=""><option disabled value="">Choose component</option>{items.map(({ component }) => <option key={component.id} value={component.id}>{component.sku} · {component.name}</option>)}</select></label>
            <label>Change in stock<input name="delta" placeholder="Use negative for damage or loss" required step="0.001" type="number" /></label>
            <label className="catalog-wide">Reason<input name="reason" placeholder="Required: damage, recount, correction…" required /></label>
          </div>
          <p className="inventory-helper">This request will not change stock until a Super Admin approves it.</p>
          <div className="catalog-form-actions"><button className="catalog-secondary-button" onClick={() => setShowAdjustment(false)} type="button">Cancel</button><button className="catalog-primary-button">Submit for approval</button></div>
        </form>
      )}

      {canManage && <div className="inventory-admin-panels">
        <section className="inventory-panel">
          <div className="inventory-panel-heading"><div><span className="eyebrow">REORDER LEVEL</span><h2>Low-stock alerts</h2></div><span className="inventory-panel-count">{alerts.length}</span></div>
          {alerts.length ? <div className="inventory-alert-list">{alerts.map((item) => <Link key={item.component.id} to={`/inventory/${item.component.id}`}><span><strong>{item.component.sku}</strong><small>{item.component.name}</small></span><b>{item.available} available</b></Link>)}</div> : <p className="inventory-empty">No components are below their reorder level.</p>}
        </section>
        <section className="inventory-panel">
          <div className="inventory-panel-heading"><div><span className="eyebrow">SUPER ADMIN REVIEW</span><h2>Pending adjustments</h2></div><span className="inventory-panel-count">{adjustments.length}</span></div>
          {adjustments.length ? <div className="adjustment-list">{adjustments.map((adjustment) => <div className="adjustment-item" key={adjustment.id}><span className="adjustment-description"><strong>{adjustment.component.sku} · {adjustment.delta > 0 ? "+" : ""}{adjustment.delta}</strong><small>{adjustment.reason}</small></span><div><button className="catalog-text-link" onClick={() => reviewAdjustment(adjustment, "approve")} type="button">Approve</button><button className="catalog-text-link muted" onClick={() => reviewAdjustment(adjustment, "reject")} type="button">Reject</button></div></div>)}</div> : <p className="inventory-empty">No adjustments awaiting approval.</p>}
        </section>
      </div>}

      <div className="catalog-toolbar inventory-toolbar">
        <label className="catalog-search"><span>⌕</span><input aria-label="Search inventory" onChange={(event) => setSearch(event.target.value)} placeholder="Search component SKU, name or category…" value={search} /></label>
        <span className="catalog-count">{items.length} components</span>
      </div>
      <div className="catalog-table-wrap">
        <table className="catalog-table inventory-table">
          <thead><tr><th>COMPONENT</th><th>ON HAND</th><th>RESERVED</th><th>AVAILABLE</th><th>REORDER LEVEL</th><th>STATUS</th><th /></tr></thead>
          <tbody>{items.map((item) => <tr className={item.low_stock ? "low-stock-row" : ""} key={item.component.id}>
            <td><Link className="inventory-component-link" to={`/inventory/${item.component.id}`}><strong>{item.component.name}</strong><small>{item.component.sku} · {item.component.category || "Uncategorized"}</small></Link></td>
            <td><Quantity value={item.on_hand} unit={item.component.unit} /></td>
            <td><Quantity value={item.reserved} unit={item.component.unit} /></td>
            <td><strong className="inventory-available"><Quantity value={item.available} unit={item.component.unit} /></strong></td>
            <td><Quantity value={item.component.reorder_level} unit={item.component.unit} /></td>
            <td>{item.low_stock ? <span className="status-pill low-stock-pill">Low stock</span> : <span className="status-pill is-active">In range</span>}</td>
            <td><Link className="catalog-text-link" to={`/inventory/${item.component.id}`}>History →</Link></td>
          </tr>)}{!items.length && !error && <tr><td className="catalog-empty-cell" colSpan="7">No components found.</td></tr>}</tbody>
        </table>
      </div>
      <div className="foundation-note"><span>i</span><p>Stock counts are maintained by immutable movements. Available = on hand − reserved; pending adjustments are excluded.</p></div>
    </section>
  );
}

export function ComponentStockDetailPage({ token }) {
  const { notify } = useNotify();
  const { componentId } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    request(`/api/inventory/${componentId}/`, token)
      .then((result) => {
        setData(result);
        setError("");
        if (Number(result.balance.available) <= Number(result.component.reorder_level)) {
          notify(`${result.component.name} (${result.component.sku}) is at or below its reorder level.`, "warning");
        }
      })
      .catch((requestError) => setError(requestError.message));
  }, [componentId, token, notify]);

  if (!data) return <section className="page-content"><Alert message={error} /><div className="loading-screen">Loading stock history…</div></section>;
  const { component, balance, movements } = data;
  return (
    <section className="page-content">
      <Link className="catalog-back-link" to="/inventory">← All inventory</Link>
      <div className="inventory-detail-heading"><div><div className="eyebrow">STOCK HISTORY / {component.sku}</div><h1>{component.name}</h1><p>{component.category || "Uncategorized"} · {component.unit}</p></div><span className="revision-pill">Reorder at {component.reorder_level} {component.unit}</span></div>
      {error && <Alert message={error} />}
      <div className="inventory-balance-cards">
        <div><span>ON HAND</span><strong><Quantity value={balance.on_hand} unit={component.unit} /></strong></div>
        <div><span>RESERVED</span><strong><Quantity value={balance.reserved} unit={component.unit} /></strong></div>
        <div><span>AVAILABLE</span><strong><Quantity value={balance.available} unit={component.unit} /></strong></div>
      </div>
      <div className="catalog-toolbar inventory-toolbar"><div><span className="eyebrow">IMMUTABLE STOCK LEDGER</span><h2>Movement history</h2></div><span className="catalog-count">{movements.length} movements</span></div>
      <div className="catalog-table-wrap">
        <table className="catalog-table movement-table">
          <thead><tr><th>DATE & TIME</th><th>MOVEMENT</th><th>QUANTITY</th><th>ON-HAND CHANGE</th><th>RESERVED CHANGE</th><th>WORK ORDER</th><th>USER / NOTE</th></tr></thead>
          <tbody>{movements.map((movement) => <tr key={movement.id}>
            <td>{new Date(movement.timestamp).toLocaleString()}</td>
            <td><span className={`movement-type movement-${movement.movement_type.toLowerCase()}`}>{movement.movement_type}</span></td>
            <td><Quantity value={movement.quantity} unit={component.unit} /></td>
            <td>{movement.delta_on_hand}</td><td>{movement.delta_reserved}</td><td>{movement.work_order_reference || "—"}</td>
            <td><strong>{movement.username || "System"}</strong><small className="movement-note">{movement.note || "—"}</small></td>
          </tr>)}{!movements.length && <tr><td className="catalog-empty-cell" colSpan="7">No stock movements recorded yet.</td></tr>}</tbody>
        </table>
      </div>
    </section>
  );
}
