import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { apiRequest } from "./notifications.jsx";

async function request(path, token) {
  return apiRequest(path, token);
}

const statusLabels = {
  IN_STOCK: "In Stock",
  RESERVED_FOR_SALE: "Reserved for sale",
  SOLD: "Sold",
  RETURNED: "Returned",
  UNDER_INSPECTION: "Under inspection",
};

function CameraScanner({ onScan }) {
  const videoRef = useRef(null);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!scanning) return undefined;
    let cancelled = false;
    let controls;
    let reader;
    if (!window.isSecureContext) {
      setError("Camera scanning requires HTTPS or localhost. You can still scan with a USB barcode scanner.");
      setScanning(false);
      return undefined;
    }
    async function startCamera() {
      try {
        const { BrowserMultiFormatReader } = await import("@zxing/browser");
        if (cancelled) return;
        reader = new BrowserMultiFormatReader();
        controls = await reader.decodeFromVideoDevice(undefined, videoRef.current, (result) => {
          if (cancelled || !result) return;
          setScanning(false);
          onScan(result.getText());
        });
        if (cancelled) controls.stop();
        else setError("");
      } catch (failure) {
        if (cancelled) return;
        setError(failure.name === "NotAllowedError"
          ? "Camera permission was denied. Allow camera access or use a USB barcode scanner."
          : "Could not start the camera. Use a USB barcode scanner instead.");
        setScanning(false);
      }
    }
    startCamera();

    return () => {
      cancelled = true;
      controls?.stop();
      reader?.reset();
    };
  }, [scanning, onScan]);

  return (
    <div className="scanner-camera">
      <div className="scanner-actions">
        <button className="catalog-secondary-button" type="button" onClick={() => { setError(""); setScanning((value) => !value); }}>
          {scanning ? "Stop camera" : "Scan with camera"}
        </button>
        <span>Camera scanning needs browser camera support and a secure origin (HTTPS).</span>
      </div>
      {scanning && <video className="scanner-video" ref={videoRef} muted playsInline />}
      {error && <div className="catalog-alert" role="alert">{error}</div>}
    </div>
  );
}

export function FinishedGoodsPage({ token, user }) {
  const [products, setProducts] = useState([]);
  const [summary, setSummary] = useState([]);
  const [units, setUnits] = useState([]);
  const [filters, setFilters] = useState({
    search: "",
    product: "",
    revision: "",
    status: "",
    work_order: "",
    date_from: "",
    date_to: "",
  });
  const [searchSerial, setSearchSerial] = useState("");
  const [reconciliation, setReconciliation] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();
  const isSuperAdmin = user.role === "SUPER_ADMIN";

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      Object.entries(filters).forEach(([key, value]) => {
        if (value.trim()) params.set(key, value.trim());
      });
      const [unitData, summaryData, productData, reconciliationData] = await Promise.all([
        request(`/api/finished-units/?${params}`, token),
        request("/api/finished-units/summary/", token),
        request("/api/products/", token),
        isSuperAdmin ? request("/api/finished-units/reconciliation/", token) : Promise.resolve(null),
      ]);
      setUnits(unitData);
      setSummary(summaryData);
      setProducts(productData);
      if (reconciliationData) setReconciliation(reconciliationData);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setLoading(false);
    }
  }, [filters, isSuperAdmin, token]);

  useEffect(() => { load(); }, [load]);

  const lookup = useCallback(async (serial) => {
    const value = serial.trim();
    if (!value) return;
    setError("");
    try {
      const unit = await request(`/api/finished-units/lookup/?serial_number=${encodeURIComponent(value)}`, token);
      navigate(`/finished-goods/${unit.id}`);
    } catch (failure) {
      setError(failure.message);
    }
  }, [navigate, token]);

  function updateFilter(event) {
    const { name, value } = event.target;
    setFilters((current) => ({ ...current, [name]: value }));
  }

  return (
    <section className="page-content">
      <div className="page-heading">
        <div><div className="eyebrow">TAMIRABOT / INVENTORY</div><h1>Finished goods</h1><p className="page-subtitle">Trace every packed unit by serial number, production order, and status.</p></div>
        <span className="count-pill">{units.length} units</span>
      </div>
      {error && <div className="catalog-alert" role="alert">{error}</div>}

      <section className="finished-summary panel">
        <div className="panel-heading"><div><h2>Product summary</h2><p>Counts reflect each unit's current status.</p></div></div>
        <div className="table-wrap"><table><thead><tr><th>Product</th><th>In Stock</th><th>Sold</th><th>Returned</th></tr></thead><tbody>
          {summary.map((product) => <tr key={product.product_id}><td><strong>{product.product_number}</strong><span className="table-subtitle">{product.product_name}</span></td><td>{product.in_stock}</td><td>{product.sold}</td><td>{product.returned}</td></tr>)}
          {!summary.length && <tr><td colSpan="4">No finished units have been packed.</td></tr>}
        </tbody></table></div>
      </section>

      <section className="finished-scanner panel">
        <div className="panel-heading"><div><h2>Find a unit</h2><p>Scan a Code128 label with a USB scanner, or type its serial number.</p></div></div>
        <form className="serial-lookup-form" onSubmit={(event) => { event.preventDefault(); lookup(searchSerial); }}>
          <input aria-label="Serial number" autoComplete="off" value={searchSerial} onChange={(event) => setSearchSerial(event.target.value)} placeholder="Scan or enter serial number" />
          <button className="catalog-primary-button">Open unit</button>
        </form>
        <CameraScanner onScan={lookup} />
      </section>

      {isSuperAdmin && (
        <section className="finished-reconciliation panel">
          <div className="panel-heading"><div><h2>Production reconciliation</h2><p>Compares consumed stock with BOM quantities for units packed.</p></div><span className="count-pill">{reconciliation.filter((row) => !row.reconciled).length} flagged</span></div>
          <div className="table-wrap"><table><thead><tr><th>Work order</th><th>Product</th><th>Units</th><th>Result</th></tr></thead><tbody>
            {reconciliation.map((row) => <tr key={row.work_order_id}><td><Link to={`/work-orders/${row.work_order_id}`}>{row.work_order_number}</Link></td><td><strong>{row.product_number}</strong><span className="table-subtitle">{row.product_name} · Rev {row.product_revision}</span></td><td>{row.packed_quantity}</td><td><span className={`status-tag ${row.reconciled ? "status-completed" : "status-blocked"}`}>{row.reconciled ? "Reconciled" : "Difference found"}</span>{row.discrepancies.map((item) => <div className="reconciliation-difference" key={item.component_id}>{item.component_sku}: expected {item.expected}, consumed {item.actual} (difference {item.difference})</div>)}</td></tr>)}
            {!reconciliation.length && <tr><td colSpan="4">No packed work orders to reconcile.</td></tr>}
          </tbody></table></div>
        </section>
      )}

      <section className="finished-unit-browser panel">
        <div className="panel-heading"><div><h2>Finished-unit register</h2><p>Filter by product, revision, status, work order, or packed date.</p></div></div>
        <div className="finished-filters">
          <label>Serial search<input name="search" value={filters.search} onChange={updateFilter} placeholder="Search serial" /></label>
          <label>Product<select name="product" value={filters.product} onChange={updateFilter}><option value="">All products</option>{products.map((product) => <option value={product.id} key={product.id}>{product.product_number} · {product.name}</option>)}</select></label>
          <label>Revision<select name="revision" value={filters.revision} onChange={updateFilter}><option value="">All revisions</option>{products.flatMap((product) => (product.revisions || []).map((revision) => <option value={revision.id} key={revision.id}>{product.product_number} · Rev {revision.revision_number}</option>))}</select></label>
          <label>Status<select name="status" value={filters.status} onChange={updateFilter}><option value="">All statuses</option>{Object.entries(statusLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
          <label>Work order<input name="work_order" type="number" min="1" value={filters.work_order} onChange={updateFilter} placeholder="Order ID" /></label>
          <label>Packed from<input name="date_from" type="date" value={filters.date_from} onChange={updateFilter} /></label>
          <label>Packed to<input name="date_to" type="date" value={filters.date_to} onChange={updateFilter} /></label>
          <button className="catalog-secondary-button" onClick={() => setFilters({ search: "", product: "", revision: "", status: "", work_order: "", date_from: "", date_to: "" })}>Clear filters</button>
        </div>
        {loading ? <p className="muted">Loading finished units…</p> : (
          <div className="table-wrap"><table><thead><tr><th>Serial</th><th>Product / revision</th><th>Work order</th><th>Status</th><th>Packed at</th><th /></tr></thead><tbody>
            {units.map((unit) => <tr key={unit.id}><td><strong className="finished-serial">{unit.serial_number}</strong></td><td>{unit.product_number}<span className="table-subtitle">{unit.product_name} · Rev {unit.revision_number}</span></td><td><Link to={`/work-orders/${unit.work_order}`}>{unit.work_order_number}</Link></td><td><span className={`status-tag status-${unit.status.toLowerCase().replaceAll("_", "-")}`}>{statusLabels[unit.status]}</span></td><td>{new Date(unit.packed_at).toLocaleString()}</td><td><Link className="secondary-button" to={`/finished-goods/${unit.id}`}>Details</Link></td></tr>)}
            {!units.length && <tr><td colSpan="6">No finished units match these filters.</td></tr>}
          </tbody></table></div>
        )}
      </section>
    </section>
  );
}

export function FinishedUnitDetailPage({ token }) {
  const { unitId } = useParams();
  const [unit, setUnit] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    request(`/api/finished-units/${unitId}/`, token)
      .then((data) => active && setUnit(data))
      .catch((failure) => active && setError(failure.message));
    return () => { active = false; };
  }, [token, unitId]);

  if (error) return <section className="page-content"><div className="catalog-alert" role="alert">{error}</div><Link to="/finished-goods">Back to finished goods</Link></section>;
  if (!unit) return <section className="page-content"><p className="muted">Loading unit history…</p></section>;

  return (
    <section className="page-content">
      <div className="page-heading">
        <div><Link className="back-link" to="/finished-goods">← Finished goods</Link><div className="eyebrow">FINISHED UNIT · {unit.work_order_number}</div><h1>{unit.serial_number}</h1><p>{unit.product_number} · {unit.product_name} · SKU {unit.product_sku}</p></div>
        <span className={`status-tag status-${unit.status.toLowerCase().replaceAll("_", "-")}`}>{statusLabels[unit.status]}</span>
      </div>
      <div className="work-order-overview">
        <div><span>Product revision</span><strong>Rev {unit.revision_number}</strong></div>
        <div><span>Work order</span><strong><Link to={`/work-orders/${unit.work_order}`}>{unit.work_order_number}</Link></strong></div>
        <div><span>Packed by</span><strong>{unit.packed_by || "Unknown"}</strong></div>
        <div><span>Packed at</span><strong>{new Date(unit.packed_at).toLocaleString()}</strong></div>
      </div>
      <div className="panel">
        <div className="panel-heading"><div><h2>Unit history</h2><p>Production, sale, return, and inspection events.</p></div><Link className="secondary-button" to={`/finished-units/${unit.id}/label`}>Barcode label</Link></div>
        <div className="unit-history">
          {unit.history.map((item) => <article className="unit-history-item" key={item.id}><span className="unit-history-marker" /><div><div className="unit-history-heading"><strong>{item.event_type.replaceAll("_", " ")}</strong><time>{new Date(item.timestamp).toLocaleString()}</time></div><p>{item.from_status ? `${statusLabels[item.from_status]} → ${statusLabels[item.to_status]}` : statusLabels[item.to_status]}</p>{item.actor_name && <small>By {item.actor_name}</small>}{item.sale_reference && <small>Sale reference: {item.sale_reference}</small>}{item.note && <small>{item.note}</small>}</div></article>)}
          {!unit.history.length && <p className="muted">No recorded history.</p>}
        </div>
      </div>
    </section>
  );
}
