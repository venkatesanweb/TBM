import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import JsBarcode from "jsbarcode";

async function request(path, token, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      Authorization: `Token ${token}`,
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail || Object.values(body).flat().join(" ");
    throw new Error(typeof detail === "string" ? detail : "Request failed.");
  }
  return response.status === 204 ? null : response.json();
}

function newIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.()
    || `pack-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function useRequest(token, path) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    request(path, token)
      .then((result) => active && setData(result))
      .catch((failure) => active && setError(failure.message))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [path, token]);
  return { data, setData, error, setError, loading };
}

export function WorkOrderListPage({ token, user }) {
  const ordersState = useRequest(token, "/api/work-orders/");
  const productsState = useRequest(token, "/api/products/");
  const [admins, setAdmins] = useState([]);
  const [formError, setFormError] = useState("");
  const [saving, setSaving] = useState(false);
  const navigate = useNavigate();
  const isSuperAdmin = user.role === "SUPER_ADMIN";

  useEffect(() => {
    if (!isSuperAdmin) return undefined;
    let active = true;
    request("/api/work-orders/assignees/", token)
      .then((result) => active && setAdmins(result))
      .catch((failure) => active && setFormError(failure.message));
    return () => { active = false; };
  }, [isSuperAdmin, token]);

  async function createOrder(event) {
    event.preventDefault();
    setSaving(true);
    setFormError("");
    const form = new FormData(event.currentTarget);
    try {
      const order = await request("/api/work-orders/", token, {
        method: "POST",
        body: JSON.stringify({
          product: Number(form.get("product")),
          quantity: Number(form.get("quantity")),
          priority: form.get("priority"),
          due_date: form.get("due_date"),
          assigned_admin: Number(form.get("assigned_admin")),
        }),
      });
      navigate(`/work-orders/${order.id}`);
    } catch (failure) {
      setFormError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  const orders = ordersState.data || [];
  return (
    <section className="page-content">
      <div className="page-heading">
        <div><div className="eyebrow">PRODUCTION</div><h1>Work Orders</h1><p>Prioritised builds, reservations and packing.</p></div>
        <span className="count-pill">{orders.length} orders</span>
      </div>

      {isSuperAdmin && (
        <form className="panel work-order-create" onSubmit={createOrder}>
          <div className="panel-heading"><div><h2>Create work order</h2><p>The current BOM revision is frozen when the order is created.</p></div></div>
          {formError && <div className="form-error" role="alert">{formError}</div>}
          <div className="form-grid">
            <label>Product
              <select name="product" required defaultValue="">
                <option value="" disabled>Select product</option>
                {(productsState.data || []).map((product) => (
                  <option key={product.id} value={product.id} disabled={!product.current_revision}>
                    {product.product_number} · {product.name}{product.current_revision ? ` · Rev ${product.current_revision.revision_number}` : " · No BOM"}
                  </option>
                ))}
              </select>
            </label>
            <label>Quantity<input name="quantity" type="number" min="1" required defaultValue="1" /></label>
            <label>Priority
              <select name="priority" defaultValue="MEDIUM">
                <option value="HIGH">High</option><option value="MEDIUM">Medium</option><option value="LOW">Low</option>
              </select>
            </label>
            <label>Due date<input name="due_date" type="date" required /></label>
            <label>Assign to
              <select name="assigned_admin" required defaultValue="">
                <option value="" disabled>Select admin</option>
                {admins.map((admin) => <option key={admin.id} value={admin.id}>{admin.username}</option>)}
              </select>
            </label>
          </div>
          <button className="primary-button" disabled={saving || !admins.length || productsState.loading}>
            {saving ? "Creating…" : "Create and reserve"}
          </button>
        </form>
      )}

      <div className="panel">
        <div className="panel-heading"><div><h2>Production queue</h2><p>High priority first, then due date.</p></div></div>
        {ordersState.loading ? <p className="muted">Loading work orders…</p> : ordersState.error ? <div className="form-error">{ordersState.error}</div> : orders.length ? (
          <div className="work-order-list">
            {orders.map((order) => (
              <Link to={`/work-orders/${order.id}`} className={`work-order-row ${order.priority === "HIGH" ? "priority-high" : ""}`} key={order.id}>
                <div className="work-order-code">{order.work_order_number}<span>{order.product_number} · Rev {order.revision_number}</span></div>
                <div className="work-order-main"><strong>{order.product_name}</strong><span>{order.packed_quantity}/{order.quantity} packed · Due {order.due_date}</span></div>
                <div className="work-order-tags"><span className={`priority-tag priority-${order.priority.toLowerCase()}`}>{order.priority}</span><span className={`status-tag status-${order.status.toLowerCase().replace("_", "-")}`}>{order.status.replaceAll("_", " ")}</span></div>
                <span className="work-order-assignee">{order.assigned_admin_name}</span>
              </Link>
            ))}
          </div>
        ) : <div className="empty-state">No work orders yet.</div>}
      </div>
    </section>
  );
}

export function WorkOrderDetailPage({ token, user }) {
  const { workOrderId } = useParams();
  const { data: order, setData: setOrder, error, setError, loading } = useRequest(token, `/api/work-orders/${workOrderId}/`);
  const [admins, setAdmins] = useState([]);
  const [packing, setPacking] = useState(false);
  const [actionError, setActionError] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState(newIdempotencyKey);
  const [quantity, setQuantity] = useState(1);
  const isSuperAdmin = user.role === "SUPER_ADMIN";

  useEffect(() => {
    if (!isSuperAdmin) return undefined;
    let active = true;
    request("/api/work-orders/assignees/", token)
      .then((result) => active && setAdmins(result))
      .catch((failure) => active && setActionError(failure.message));
    return () => { active = false; };
  }, [isSuperAdmin, token]);

  async function refresh() {
    const updated = await request(`/api/work-orders/${workOrderId}/`, token);
    setOrder(updated);
  }

  async function pack(event) {
    event.preventDefault();
    setPacking(true);
    setActionError("");
    try {
      await request(`/api/work-orders/${workOrderId}/pack/`, token, {
        method: "POST",
        body: JSON.stringify({ quantity: Number(quantity), idempotency_key: idempotencyKey }),
      });
      await refresh();
      setIdempotencyKey(newIdempotencyKey());
    } catch (failure) {
      setActionError(failure.message);
    } finally {
      setPacking(false);
    }
  }

  async function updateOrder(event) {
    event.preventDefault();
    setActionError("");
    const form = new FormData(event.currentTarget);
    const update = form.get("assigned_admin")
      ? { assigned_admin: Number(form.get("assigned_admin")) }
      : { quantity: Number(form.get("quantity")) };
    try {
      setOrder(await request(`/api/work-orders/${workOrderId}/`, token, { method: "PATCH", body: JSON.stringify(update) }));
    } catch (failure) {
      setActionError(failure.message);
    }
  }

  async function cancel() {
    if (!window.confirm("Cancel this work order and release its remaining reservation?")) return;
    try {
      setOrder(await request(`/api/work-orders/${workOrderId}/cancel/`, token, { method: "POST" }));
    } catch (failure) {
      setActionError(failure.message);
    }
  }

  async function startWork() {
    setActionError("");
    try {
      setOrder(await request(`/api/work-orders/${workOrderId}/start/`, token, { method: "POST" }));
    } catch (failure) {
      setActionError(failure.message);
    }
  }

  if (loading) return <section className="page-content"><p className="muted">Loading work order…</p></section>;
  if (error || !order) return <section className="page-content"><div className="form-error">{error || "Work order not found."}</div><Link to="/work-orders">Back to work orders</Link></section>;
  const canPack = user.role === "ADMIN" && ["IN_PROGRESS", "PACKED"].includes(order.status);
  const canStart = user.role === "ADMIN" && order.status === "RESERVED";

  return (
    <section className="page-content">
      <div className="page-heading">
        <div><Link className="back-link" to="/work-orders">← Work Orders</Link><div className="eyebrow">{order.work_order_number} · REVISION {order.revision_number}</div><h1>{order.product_name}</h1><p>{order.product_number} · {order.product_sku}</p></div>
        <span className={`status-tag status-${order.status.toLowerCase().replace("_", "-")}`}>{order.status.replaceAll("_", " ")}</span>
      </div>
      {actionError && <div className="form-error" role="alert">{actionError}</div>}
      <div className="work-order-overview">
        <div><span>Quantity</span><strong>{order.packed_quantity} / {order.quantity}</strong></div>
        <div><span>Priority</span><strong className={`priority-text priority-${order.priority.toLowerCase()}`}>{order.priority}</strong></div>
        <div><span>Due date</span><strong>{order.due_date}</strong></div>
        <div><span>Assigned admin</span><strong>{order.assigned_admin_name}</strong></div>
      </div>
      {order.blocked_reason && <div className="blocked-notice"><strong>Stock needed</strong><span>{order.blocked_reason}</span></div>}
      <div className="panel">
        <div className="panel-heading"><div><h2>Frozen BOM and stock reservation</h2><p>Packing components per unit and inventory held for this order.</p></div></div>
        {order.bom_components.length ? (
          <div className="table-wrap"><table><thead><tr><th>Component</th><th>Per unit</th><th>Order total</th><th>Reserved remaining</th><th>Consumed</th><th>Released</th></tr></thead><tbody>
            {order.bom_components.map((item) => {
              const reservation = order.reservations.find((row) => row.component === item.component);
              return <tr key={item.component}><td><strong>{item.sku}</strong><span className="table-subtitle">{item.name}</span></td><td>{item.quantity_per_unit} {item.unit}</td><td>{item.total_required}</td><td>{reservation?.remaining || "0"}</td><td>{reservation?.consumed_quantity || "0"}</td><td>{reservation?.released_quantity || "0"}</td></tr>;
            })}
          </tbody></table></div>
        ) : <div className="empty-state">This revision has no BOM components.</div>}
      </div>
      {canPack && (
        <form className="panel packing-form" onSubmit={pack}>
          <div className="panel-heading"><div><h2>Pack units</h2><p>Confirming consumes reserved components and creates one traceable unit per product.</p></div></div>
          <label>Units to pack<input type="number" min="1" max={order.quantity - order.packed_quantity} value={quantity} onChange={(event) => setQuantity(event.target.value)} required /></label>
          <button className="primary-button" disabled={packing}>{packing ? "Confirming…" : "Confirm packing"}</button>
        </form>
      )}
      {canStart && <div className="panel packing-form"><div className="panel-heading"><div><h2>Ready to build</h2><p>Starting marks this assigned order as in progress.</p></div></div><button className="primary-button" onClick={startWork}>Start work order</button></div>}
      {isSuperAdmin && !["CANCELLED", "COMPLETED"].includes(order.status) && (
        <div className="panel">
          <div className="panel-heading"><div><h2>Manage order</h2><p>Reassign its reservation or reduce the unbuilt quantity.</p></div></div>
          <div className="work-order-manage">
            <form onSubmit={updateOrder}><label>Reassign to<select name="assigned_admin" defaultValue={order.assigned_admin}><option value="" disabled>Select admin</option>{admins.map((admin) => <option key={admin.id} value={admin.id}>{admin.username}</option>)}</select></label><button className="secondary-button">Reassign</button></form>
            <form onSubmit={updateOrder}><label>Reduce total quantity<input name="quantity" type="number" min={Math.max(1, order.packed_quantity)} max={order.quantity - 1} required /></label><button className="secondary-button">Reduce quantity</button></form>
            <button className="danger-button" onClick={cancel}>Cancel and release stock</button>
          </div>
        </div>
      )}
      <div className="panel">
        <div className="panel-heading"><div><h2>Finished units</h2><p>Each packed product receives a unique monthly serial number.</p></div></div>
        {order.finished_units.length ? <div className="finished-unit-list">{order.finished_units.map((unit) => <div className="finished-unit-row" key={unit.id}><div><strong>{unit.serial_number}</strong><span>{unit.status}</span></div><Link className="secondary-button" to={`/finished-units/${unit.id}/label`}>View / reprint label</Link></div>)}</div> : <div className="empty-state">Packed units will appear here.</div>}
      </div>
    </section>
  );
}

export function FinishedUnitLabelPage({ token }) {
  const { unitId } = useParams();
  const { data: unit, error, loading } = useRequest(token, `/api/finished-units/${unitId}/label/`);
  const [barcodeError, setBarcodeError] = useState("");
  const [printError, setPrintError] = useState("");
  useEffect(() => {
    if (!unit) return;
    try {
      JsBarcode("#finished-unit-barcode", unit.barcode_value, {
        format: "CODE128",
        displayValue: false,
        margin: 0,
        height: 42,
        width: 1.5,
      });
      setBarcodeError("");
    } catch {
      setBarcodeError("Could not render the Code128 barcode.");
    }
  }, [unit]);

  if (loading) return <section className="page-content"><p className="muted">Loading label…</p></section>;
  if (error || !unit) return <section className="page-content"><div className="form-error">{error || "Finished unit not found."}</div></section>;
  async function printLabel() {
    setPrintError("");
    try {
      await request(`/api/finished-units/${unit.id}/reprint/`, token, { method: "POST" });
      window.print();
    } catch (failure) {
      setPrintError(failure.message);
    }
  }
  return (
    <section className="page-content label-page">
      <div className="page-heading no-print"><div><div className="eyebrow">FINISHED UNIT · {unit.work_order_number}</div><h1>Product label</h1><p>Reprints are recorded in the audit log.</p></div><button className="primary-button" onClick={printLabel}>Print label</button></div>
      {(barcodeError || printError) && <div className="form-error no-print">{barcodeError || printError}</div>}
      <article className="thermal-label">
        <strong className="label-product">{unit.product_name}</strong>
        <span className="label-sku">SKU: {unit.product_sku}</span>
        <svg id="finished-unit-barcode" role="img" aria-label={`Code128 barcode for ${unit.serial_number}`} />
        <strong className="label-serial">{unit.serial_number}</strong>
      </article>
    </section>
  );
}
