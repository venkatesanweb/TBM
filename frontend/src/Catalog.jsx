import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { apiRequest, useNotify } from "./notifications.jsx";

async function request(path, token, options = {}) {
  return apiRequest(path, token, options);
}

function photoUrl(photo) {
  if (!photo) return null;
  return photo.startsWith("http") ? photo : `${window.location.origin}${photo}`;
}

function Alert({ message, success = false }) {
  if (!message) return null;
  return <div className={`catalog-alert ${success ? "success" : ""}`} role="status">{message}</div>;
}

function Photo({ src, label }) {
  return src
    ? <img className="catalog-photo" src={photoUrl(src)} alt={label} />
    : <div className="catalog-photo placeholder" aria-label="No photo">▧</div>;
}

function ProductForm({ token, product, onSaved, onCancel }) {
  const { changed } = useNotify();
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setError("");
    setSaving(true);
    const data = new FormData(event.currentTarget);
    const body = {
      product_number: data.get("product_number"),
      name: data.get("name"),
      sku: data.get("sku"),
      description: data.get("description"),
      active: data.get("active") === "on",
    };
    const photo = data.get("photo");
    if (photo?.size) body.photo = photo;
    try {
      if (product) {
        const multipart = new FormData();
        Object.entries(body).forEach(([key, value]) => multipart.append(key, value));
        await request(`/api/products/${product.id}/`, token, { method: "PATCH", body: multipart });
        changed(body.active ? "productUpdated" : "productDeactivated", {
          name: `${body.product_number} · ${body.name}`,
        });
      } else {
        const multipart = new FormData();
        Object.entries(body).forEach(([key, value]) => multipart.append(key, value));
        await request("/api/products/", token, { method: "POST", body: multipart });
        changed("productCreated", { name: `${body.product_number} · ${body.name}` });
      }
      onSaved();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="catalog-form" onSubmit={submit}>
      <div className="catalog-form-heading">
        <div><span className="eyebrow">PRODUCT RECORD</span><h2>{product ? "Edit product" : "New product"}</h2></div>
        <button className="catalog-icon-button" type="button" onClick={onCancel} aria-label="Close form">×</button>
      </div>
      {error && <Alert message={error} />}
      <div className="catalog-form-grid">
        <label>Product number<input name="product_number" required defaultValue={product?.product_number} /></label>
        <label>Product name<input name="name" required defaultValue={product?.name} /></label>
        <label>SKU<input name="sku" required defaultValue={product?.sku} /></label>
        <label className="catalog-file-field">Product photo<input accept="image/jpeg,image/png,image/webp" name="photo" type="file" /></label>
        <label className="catalog-wide">Description<textarea name="description" rows="3" defaultValue={product?.description} /></label>
        <label className="catalog-check"><input name="active" type="checkbox" defaultChecked={product?.active ?? true} /> Active product</label>
      </div>
      <div className="catalog-form-actions">
        <button className="catalog-secondary-button" type="button" onClick={onCancel}>Cancel</button>
        <button className="catalog-primary-button" disabled={saving}>{saving ? "Saving…" : "Save product"}</button>
      </div>
    </form>
  );
}

function ProductWizard({ token, onSaved, onCancel }) {
  const { changed } = useNotify();
  const [step, setStep] = useState(1);
  const [details, setDetails] = useState(null);
  const [components, setComponents] = useState([]);
  const [componentSearch, setComponentSearch] = useState("");
  const [lines, setLines] = useState([]);
  const [quickCreate, setQuickCreate] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const query = new URLSearchParams({ active: "true" });
    if (componentSearch.trim()) query.set("search", componentSearch.trim());
    request(`/api/components/?${query}`, token)
      .then(setComponents)
      .catch((requestError) => setError(requestError.message));
  }, [token, componentSearch]);

  function saveProductDetails(event) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setDetails({
      product_number: data.get("product_number"),
      name: data.get("name"),
      description: data.get("description"),
      sku: data.get("sku"),
      active: data.get("active") === "on",
      photo: data.get("photo"),
    });
    setStep(2);
    setError("");
  }

  function addComponent(component, quantity = "1") {
    setLines((current) => current.some((line) => line.component === component.id)
      ? current
      : [...current, { component: component.id, componentData: component, quantity_per_unit: quantity }]);
  }

  async function createProduct() {
    setError("");
    setSaving(true);
    const body = new FormData();
    Object.entries(details).forEach(([key, value]) => {
      if (key !== "photo") body.append(key, value);
    });
    if (details.photo?.size) body.append("photo", details.photo);
    body.append("bom_lines", JSON.stringify(lines.map(({ component, quantity_per_unit }) => ({
      component,
      quantity_per_unit,
    }))));
    try {
      await request("/api/products/", token, { method: "POST", body });
      changed("productCreated", { name: `${details.product_number} · ${details.name}` });
      onSaved();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="catalog-form product-wizard">
      <div className="catalog-form-heading">
        <div><span className="eyebrow">NEW PRODUCT · STEP {step} OF 2</span><h2>{step === 1 ? "Product details" : "Build the BOM"}</h2></div>
        <button className="catalog-icon-button" type="button" onClick={onCancel} aria-label="Close form">×</button>
      </div>
      <div className="wizard-progress"><span className={step >= 1 ? "complete" : ""} /><span className={step >= 2 ? "complete" : ""} /></div>
      {error && <Alert message={error} />}
      {step === 1 ? (
        <form onSubmit={saveProductDetails}>
          <div className="catalog-form-grid">
            <label>Product number<input name="product_number" required /></label>
            <label>Product name<input name="name" required /></label>
            <label>SKU<input name="sku" required /></label>
            <label className="catalog-file-field">Product photo<input accept="image/jpeg,image/png,image/webp" name="photo" type="file" /></label>
            <label className="catalog-wide">Description<textarea name="description" rows="3" /></label>
            <label className="catalog-check"><input defaultChecked name="active" type="checkbox" /> Active product</label>
          </div>
          <div className="catalog-form-actions"><button className="catalog-secondary-button" onClick={onCancel} type="button">Cancel</button><button className="catalog-primary-button">Continue to BOM →</button></div>
        </form>
      ) : (
        <div className="wizard-bom">
          <div className="wizard-product-summary"><strong>{details.name}</strong><span>{details.product_number} · {details.sku}</span></div>
          <label className="catalog-search wizard-component-search"><span>⌕</span><input onChange={(event) => setComponentSearch(event.target.value)} placeholder="Search component SKU or name…" value={componentSearch} /></label>
          <div className="wizard-component-results">
            {components.map((component) => (
              <div className="wizard-component-option" key={component.id}>
                <Photo src={component.photo} label={component.name} />
                <span><strong>{component.name}</strong><small>{component.sku} · {component.unit} · Rev {component.revision_number || "—"}</small></span>
                <button className="catalog-secondary-button" disabled={lines.some((line) => line.component === component.id)} onClick={() => addComponent(component)} type="button">{lines.some((line) => line.component === component.id) ? "Added" : "＋ Add"}</button>
              </div>
            ))}
            {!components.length && <div className="wizard-no-components">No matching active components. Create one first to add it to this BOM.</div>}
          </div>
          <button className="catalog-text-link" onClick={() => setQuickCreate(true)} type="button">＋ Create new component</button>
          {lines.length > 0 && (
            <div className="wizard-bom-lines">
              <div className="wizard-lines-heading"><strong>Components in BOM</strong><span>{lines.length} lines</span></div>
              {lines.map((line) => (
                <div className="wizard-bom-line" key={line.component}>
                  <span><strong>{line.componentData.sku}</strong><small>{line.componentData.name} · {line.componentData.unit}</small></span>
                  <label>Qty / unit<input min="0.001" onChange={(event) => setLines((current) => current.map((item) => item.component === line.component ? { ...item, quantity_per_unit: event.target.value } : item))} step="0.001" type="number" value={line.quantity_per_unit} /></label>
                  <button className="catalog-icon-button delete" onClick={() => setLines((current) => current.filter((item) => item.component !== line.component))} type="button" aria-label={`Remove ${line.componentData.name}`}>×</button>
                </div>
              ))}
            </div>
          )}
          {quickCreate && <div className="catalog-modal-backdrop"><div className="catalog-modal"><ComponentForm token={token} quickCreate onSaved={(component) => { addComponent(component); setComponentSearch(""); setQuickCreate(false); }} onCancel={() => setQuickCreate(false)} /></div></div>}
          <div className="catalog-form-actions"><button className="catalog-secondary-button" onClick={() => setStep(1)} type="button">← Back</button><button className="catalog-primary-button" disabled={!lines.length || saving || lines.some((line) => !Number(line.quantity_per_unit) || Number(line.quantity_per_unit) <= 0)} onClick={createProduct} type="button">{saving ? "Saving product and BOM…" : `Create product · ${lines.length} BOM ${lines.length === 1 ? "line" : "lines"}`}</button></div>
          {!lines.length && <div className="wizard-required-note">Add at least one active component before creating this product.</div>}
        </div>
      )}
    </div>
  );
}

export function ProductListPage({ token, user }) {
  const [products, setProducts] = useState([]);
  const [term, setTerm] = useState("");
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState(null);
  const [filters, setFilters] = useState({ product_number: "", name: "", sku: "", revision_number: "", active: "" });
  const [refresh, setRefresh] = useState(0);
  const canEdit = user.role === "SUPER_ADMIN";

  useEffect(() => {
    const query = new URLSearchParams();
    if (term.trim()) query.set("search", term.trim());
    Object.entries(filters).forEach(([key, value]) => {
      if (value) query.set(key, value);
    });
    request(`/api/products/?${query}`, token)
      .then((result) => {
        setProducts(result);
        setError("");
      })
      .catch((requestError) => setError(requestError.message));
  }, [token, term, filters, refresh]);

  function saved() {
    setShowForm(false);
    setEditing(null);
    setRefresh((count) => count + 1);
  }

  return (
    <section className="page-content">
      <div className="page-heading">
        <div><div className="eyebrow">TAMIRABOT / CATALOG</div><h1>Products</h1><p className="page-subtitle">Product records and revision-controlled bills of material.</p></div>
        {canEdit && <button className="catalog-primary-button" onClick={() => { setEditing(null); setShowForm(true); }}>＋ New product</button>}
      </div>
      <div className="catalog-toolbar">
        <label className="catalog-search"><span>⌕</span><input aria-label="Search products" onChange={(event) => setTerm(event.target.value)} placeholder="Search number, SKU, name or revision…" value={term} /></label>
        <span className="catalog-count">{products.length} {products.length === 1 ? "product" : "products"}</span>
      </div>
      <div className="catalog-filter-row">
        <input aria-label="Filter product number" onChange={(event) => setFilters((current) => ({ ...current, product_number: event.target.value }))} placeholder="Product number" value={filters.product_number} />
        <input aria-label="Filter product name" onChange={(event) => setFilters((current) => ({ ...current, name: event.target.value }))} placeholder="Name" value={filters.name} />
        <input aria-label="Filter SKU" onChange={(event) => setFilters((current) => ({ ...current, sku: event.target.value }))} placeholder="SKU" value={filters.sku} />
        <input aria-label="Filter revision" onChange={(event) => setFilters((current) => ({ ...current, revision_number: event.target.value }))} placeholder="Revision" value={filters.revision_number} />
        <select aria-label="Filter active products" onChange={(event) => setFilters((current) => ({ ...current, active: event.target.value }))} value={filters.active}><option value="">All status</option><option value="true">Active</option><option value="false">Inactive</option></select>
      </div>
      <Alert message={error} />
      {showForm && (editing
        ? <ProductForm token={token} product={editing} onSaved={saved} onCancel={() => { setShowForm(false); setEditing(null); }} />
        : <ProductWizard token={token} onSaved={saved} onCancel={() => setShowForm(false)} />)}
      <div className="catalog-table-wrap">
        <table className="catalog-table">
          <thead><tr><th>PRODUCT</th><th>PRODUCT NUMBER</th><th>SKU</th><th>CURRENT REVISION</th><th>STATUS</th><th /></tr></thead>
          <tbody>
            {products.map((product) => (
              <tr key={product.id}>
                <td><Link className="catalog-product-cell" to={`/products/${product.id}`}><Photo src={product.photo} label={product.name} /><span><strong>{product.name}</strong><small>{product.description || "No description"}</small></span></Link></td>
                <td><span className="catalog-mono">{product.product_number}</span></td>
                <td><span className="catalog-mono">{product.sku}</span></td>
                <td>{product.current_revision ? <span className="revision-pill">REV {product.current_revision.revision_number}</span> : "—"}</td>
                <td><span className={`status-pill ${product.active ? "is-active" : "is-inactive"}`}>{product.active ? "Active" : "Inactive"}</span></td>
                <td className="catalog-row-actions">
                  <Link className="catalog-text-link" to={`/products/${product.id}`}>View BOM →</Link>
                  {canEdit && <button className="catalog-icon-button" onClick={() => { setEditing(product); setShowForm(true); }} aria-label={`Edit ${product.name}`}>✎</button>}
                </td>
              </tr>
            ))}
            {!products.length && !error && <tr><td className="catalog-empty-cell" colSpan="6">No products found. Create a product to begin its revision history.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="foundation-note"><span>i</span><p>Each product starts at revision 1. A new revision can copy the previous BOM without changing the previous revision.</p></div>
    </section>
  );
}

function BOMForm({ token, revisionId, components, onSaved, onCancel }) {
  const { changed } = useNotify();
  const [error, setError] = useState("");
  async function submit(event) {
    event.preventDefault();
    setError("");
    const data = new FormData(event.currentTarget);
    try {
      await request(`/api/revisions/${revisionId}/bom/`, token, {
        method: "POST",
        body: JSON.stringify({
          component: Number(data.get("component")),
          quantity_per_unit: data.get("quantity_per_unit"),
        }),
      });
      const component = components.find((item) => item.id === Number(data.get("component")));
      changed("bomAdded", {
        name: component?.name || "Component",
        quantity: data.get("quantity_per_unit"),
      });
      onSaved();
    } catch (requestError) {
      setError(requestError.message);
    }
  }
  return (
    <form className="bom-add-form" onSubmit={submit}>
      {error && <Alert message={error} />}
      <label>Component<select name="component" required defaultValue=""><option disabled value="">Choose a component</option>{components.filter((component) => component.active).map((component) => <option key={component.id} value={component.id}>{component.sku} · {component.name} (rev {component.revision_number || "—"})</option>)}</select></label>
      <label>Quantity per product<input min="0.001" name="quantity_per_unit" required step="0.001" type="number" /></label>
      <div className="catalog-form-actions"><button className="catalog-secondary-button" onClick={onCancel} type="button">Cancel</button><button className="catalog-primary-button">Add component</button></div>
    </form>
  );
}

export function ProductDetailPage({ token, user }) {
  const { changed } = useNotify();
  const { productId } = useParams();
  const navigate = useNavigate();
  const [product, setProduct] = useState(null);
  const [components, setComponents] = useState([]);
  const [bom, setBom] = useState([]);
  const [revisionId, setRevisionId] = useState("");
  const [units, setUnits] = useState("1");
  const [showBomForm, setShowBomForm] = useState(false);
  const [showRevisionForm, setShowRevisionForm] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0);
  const canEdit = user.role === "SUPER_ADMIN";

  const loadBom = useCallback(async (id, count) => {
    if (!id) return;
    try {
      const result = await request(`/api/revisions/${id}/bom/?units=${encodeURIComponent(count || 1)}`, token);
      setBom(result.results);
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    }
  }, [token]);

  useEffect(() => {
    setError("");
    Promise.all([
      request(`/api/products/${productId}/`, token),
      request("/api/components/?active=true", token),
    ]).then(([productResult, componentResult]) => {
      setProduct(productResult);
      setComponents(componentResult);
      const selected = productResult.current_revision?.id || "";
      setRevisionId(String(selected));
      if (selected) loadBom(selected, units);
      else setBom([]);
    }).catch((requestError) => setError(requestError.message));
  }, [productId, token, refresh, loadBom]);

  async function createRevision(event) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      const revision = await request(`/api/products/${productId}/revisions/`, token, {
        method: "POST",
        body: JSON.stringify({
          notes: data.get("notes"),
        }),
      });
      changed("revisionCreated", {
        name: `${product.product_number} · ${product.name}`,
        quantity: revision.revision_number,
      });
      setShowRevisionForm(false);
      setNotice(`Revision ${revision.revision_number} created with the previous BOM copied.`);
      setRevisionId(String(revision.id));
      setRefresh((count) => count + 1);
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function updateLine(line, quantity) {
    setError("");
    try {
      await request(`/api/bom-lines/${line.id}/`, token, {
        method: "PATCH",
        body: JSON.stringify({ quantity_per_unit: quantity }),
      });
      changed("bomUpdated", { name: line.component_detail.name, quantity });
      setNotice("BOM quantity updated.");
      await loadBom(revisionId, units);
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function deleteLine(line) {
    if (!window.confirm(`Remove ${line.component_detail.sku} from this revision BOM?`)) return;
    try {
      await request(`/api/bom-lines/${line.id}/`, token, { method: "DELETE" });
      changed("bomRemoved", { name: line.component_detail.name });
      setNotice("Component removed from BOM.");
      await loadBom(revisionId, units);
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function deleteProduct() {
    if (!window.confirm(`Delete ${product.name} and its product revisions?`)) return;
    try {
      await request(`/api/products/${product.id}/`, token, { method: "DELETE" });
      changed("productDeleted", { name: `${product.product_number} · ${product.name}` });
      navigate("/products");
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  if (!product) return <section className="page-content"><Alert message={error} /><div className="loading-screen">Loading product…</div></section>;
  const selectedRevision = product.revisions.find((revision) => String(revision.id) === revisionId);
  const readOnly = !canEdit || selectedRevision?.is_frozen;

  return (
    <section className="page-content">
      <Link className="catalog-back-link" to="/products">← All products</Link>
      <div className="product-detail-heading">
        <Photo src={product.photo} label={product.name} />
        <div className="product-detail-title"><div className="eyebrow">PRODUCT / {product.product_number}</div><h1>{product.name}</h1><p>{product.sku} <span>·</span> {product.active ? "Active" : "Inactive"}</p></div>
        {canEdit && <button className="catalog-danger-button" onClick={deleteProduct}>Delete product</button>}
      </div>
      {error && <Alert message={error} />}
      {notice && <Alert message={notice} success />}
      <div className="catalog-toolbar bom-toolbar">
        <div><div className="eyebrow">REVISION-CONTROLLED BILL OF MATERIALS</div><h2>Product BOM</h2></div>
        <div className="bom-controls">
          <label>Revision<select aria-label="Select revision" onChange={(event) => { setRevisionId(event.target.value); setNotice(""); loadBom(event.target.value, units); }} value={revisionId}>{product.revisions.map((revision) => <option key={revision.id} value={revision.id}>Rev {revision.revision_number}{revision.is_current ? " · Current" : ""}{revision.is_frozen ? " · Frozen" : ""}</option>)}</select></label>
          <label>Build quantity<input aria-label="Build quantity" min="1" onChange={(event) => { const value = event.target.value; setUnits(value); if (/^[1-9]\d*$/.test(value) && Number(value) <= 1000000) loadBom(revisionId, value); }} step="1" type="number" value={units} /></label>
        </div>
      </div>
      <div className="bom-summary"><span>REVISION NOTES</span><strong>{selectedRevision?.notes || "No revision notes"}</strong>{selectedRevision?.is_frozen && <span className="status-pill is-inactive">Used by work order · Read only</span>}</div>
      {canEdit && (
        <div className="catalog-action-row">
          {!selectedRevision?.is_frozen && <button className="catalog-secondary-button" onClick={() => setShowBomForm((shown) => !shown)}>{showBomForm ? "Close" : "＋ Add BOM component"}</button>}
          <button className="catalog-secondary-button" onClick={() => setShowRevisionForm((shown) => !shown)}>＋ New revision</button>
        </div>
      )}
      {showRevisionForm && <form className="revision-create-form" onSubmit={createRevision}><label>Revision notes<textarea name="notes" rows="2" /></label><span className="revision-copy-note">The current BOM will be copied automatically, then can be edited in the new revision.</span><div className="catalog-form-actions"><button className="catalog-secondary-button" onClick={() => setShowRevisionForm(false)} type="button">Cancel</button><button className="catalog-primary-button">Create revision</button></div></form>}
      {showBomForm && !readOnly && <BOMForm token={token} revisionId={revisionId} components={components} onSaved={() => { setShowBomForm(false); setNotice("Component added to BOM."); loadBom(revisionId, units); }} onCancel={() => setShowBomForm(false)} />}
      <div className="catalog-table-wrap bom-table-wrap">
        <table className="catalog-table bom-table">
          <thead><tr><th>COMPONENT</th><th>SKU</th><th>REVISION</th><th>PER UNIT</th><th>TOTAL FOR {units || "0"}</th><th /></tr></thead>
          <tbody>{bom.map((line) => <BOMRow key={line.id} line={line} readOnly={readOnly} onUpdate={updateLine} onDelete={deleteLine} />)}
            {!bom.length && <tr><td className="catalog-empty-cell" colSpan="6">{selectedRevision?.is_frozen ? "No components were recorded in this frozen BOM." : "This revision has no BOM components yet."}</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="bom-total-note">Total required = quantity per unit × requested build quantity. Totals use the component’s unit of measure.</div>
    </section>
  );
}

function BOMRow({ line, readOnly, onUpdate, onDelete }) {
  const [editing, setEditing] = useState(false);
  const [quantity, setQuantity] = useState(line.quantity_per_unit);
  const component = line.component_detail;
  return (
    <tr>
      <td><span className="catalog-product-cell"><Photo src={component.photo} label={component.name} /><span><strong>{component.name}</strong><small>{component.unit}</small></span></span></td>
      <td><span className="catalog-mono">{component.sku}</span></td>
      <td>{component.revision_number || "—"}</td>
      <td>{editing ? <input aria-label="Quantity per unit" className="bom-inline-input" min="0.001" onChange={(event) => setQuantity(event.target.value)} step="0.001" type="number" value={quantity} /> : line.quantity_per_unit}</td>
      <td><strong className="bom-total">{line.total_required} <small>{component.unit}</small></strong></td>
      <td className="catalog-row-actions">{!readOnly && (editing ? <><button className="catalog-text-link" onClick={() => { onUpdate(line, quantity); setEditing(false); }}>Save</button><button className="catalog-text-link muted" onClick={() => { setQuantity(line.quantity_per_unit); setEditing(false); }}>Cancel</button></> : <><button className="catalog-icon-button" onClick={() => setEditing(true)} aria-label={`Edit quantity for ${component.name}`}>✎</button><button className="catalog-icon-button delete" onClick={() => onDelete(line)} aria-label={`Delete ${component.name} from BOM`}>×</button></>)}</td>
    </tr>
  );
}

function ComponentForm({ token, component, quickCreate = false, onSaved, onCancel }) {
  const { changed } = useNotify();
  const [error, setError] = useState("");
  const [matches, setMatches] = useState([]);
  const [duplicateSearch, setDuplicateSearch] = useState("");

  useEffect(() => {
    if (component) return undefined;
    const term = duplicateSearch.trim();
    if (term.length < 2) {
      setMatches([]);
      return undefined;
    }
    const timeout = window.setTimeout(() => {
      request(`/api/components/?search=${encodeURIComponent(term)}`, token)
        .then(setMatches)
        .catch((requestError) => setError(requestError.message));
    }, 250);
    return () => window.clearTimeout(timeout);
  }, [component, duplicateSearch, token]);

  async function submit(event) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const body = new FormData();
    ["sku", "name", "description", "category", "unit", "revision_number", "reorder_level"].forEach((key) => body.append(key, data.get(key) || ""));
    body.append("active", data.get("active") === "on");
    const photo = data.get("photo");
    if (photo?.size) body.append("photo", photo);
    try {
      const saved = await request(component ? `/api/components/${component.id}/` : quickCreate ? "/api/components/quick-create/" : "/api/components/", token, {
        method: component ? "PATCH" : "POST",
        body,
      });
      changed(component
        ? (saved.active ? "componentUpdated" : "componentDeactivated")
        : "componentCreated", {
        name: saved.name,
        sku: saved.sku,
      });
      onSaved(saved);
    } catch (requestError) {
      setError(requestError.message);
    }
  }
  return (
    <form className="catalog-form" onSubmit={submit}>
      <div className="catalog-form-heading"><div><span className="eyebrow">COMPONENT RECORD</span><h2>{component ? "Edit component" : "New component"}</h2></div><button className="catalog-icon-button" onClick={onCancel} type="button">×</button></div>
      {error && <Alert message={error} />}
      <div className="catalog-form-grid">
        <label>SKU<input name="sku" onChange={(event) => setDuplicateSearch(event.target.value)} required defaultValue={component?.sku} /></label>
        <label>Component name<input name="name" onChange={(event) => setDuplicateSearch(event.target.value)} required defaultValue={component?.name} /></label>
        <label>Category<input name="category" defaultValue={component?.category} /></label>
        <label>Unit of measure<input name="unit" required placeholder="piece, mm, g…" defaultValue={component?.unit} /></label>
        <label>Revision number<input name="revision_number" defaultValue={component?.revision_number} /></label>
        <label>Reorder level<input min="0" name="reorder_level" step="0.001" type="number" defaultValue={component?.reorder_level || "0"} /></label>
        <label className="catalog-file-field">Component photo<input accept="image/jpeg,image/png,image/webp" name="photo" type="file" /></label>
        <label className="catalog-wide">Description<textarea name="description" rows="3" defaultValue={component?.description} /></label>
        <label className="catalog-check"><input defaultChecked={component?.active ?? true} name="active" type="checkbox" /> Active component</label>
      </div>
      {!component && matches.length > 0 && <div className="duplicate-warning"><strong>Possible existing components found</strong><span>Review these matches before creating a new component.</span><div>{matches.slice(0, 5).map((match) => <Link key={match.id} to={`/components/${match.id}`} onClick={onCancel}>{match.sku} · {match.name}</Link>)}</div></div>}
      <div className="catalog-form-actions"><button className="catalog-secondary-button" onClick={onCancel} type="button">Cancel</button><button className="catalog-primary-button">Save component</button></div>
    </form>
  );
}

export function ComponentListPage({ token, user }) {
  const [components, setComponents] = useState([]);
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState({ sku: "", name: "", category: "", revision: "", active: "" });
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState(null);
  const [refresh, setRefresh] = useState(0);
  const canEdit = user.role === "SUPER_ADMIN";

  useEffect(() => {
    const query = new URLSearchParams();
    if (search.trim()) query.set("search", search.trim());
    Object.entries(filters).forEach(([key, value]) => {
      if (value) query.set(key, value);
    });
    request(`/api/components/?${query}`, token)
      .then((result) => {
        setComponents(result);
        setError("");
      })
      .catch((requestError) => setError(requestError.message));
  }, [token, search, filters, refresh]);

  function saved() {
    setShowForm(false);
    setEditing(null);
    setRefresh((count) => count + 1);
  }

  return (
    <section className="page-content">
      <div className="page-heading">
        <div><div className="eyebrow">TAMIRABOT / CATALOG</div><h1>Components</h1><p className="page-subtitle">Reusable parts referenced by product bills of material.</p></div>
        {canEdit && <button className="catalog-primary-button" onClick={() => { setEditing(null); setShowForm(true); }}>＋ New component</button>}
      </div>
      <div className="catalog-toolbar"><label className="catalog-search"><span>⌕</span><input aria-label="Search components" onChange={(event) => setSearch(event.target.value)} placeholder="Search SKU or component name…" value={search} /></label><span className="catalog-count">{components.length} components</span></div>
      <div className="catalog-filter-row">
        <input aria-label="Filter component SKU" onChange={(event) => setFilters((current) => ({ ...current, sku: event.target.value }))} placeholder="SKU" value={filters.sku} />
        <input aria-label="Filter component name" onChange={(event) => setFilters((current) => ({ ...current, name: event.target.value }))} placeholder="Name" value={filters.name} />
        <input aria-label="Filter component category" onChange={(event) => setFilters((current) => ({ ...current, category: event.target.value }))} placeholder="Category" value={filters.category} />
        <input aria-label="Filter component revision" onChange={(event) => setFilters((current) => ({ ...current, revision: event.target.value }))} placeholder="Revision" value={filters.revision} />
        <select aria-label="Filter active components" onChange={(event) => setFilters((current) => ({ ...current, active: event.target.value }))} value={filters.active}><option value="">All status</option><option value="true">Active</option><option value="false">Inactive</option></select>
      </div>
      <Alert message={error} />
      {showForm && <ComponentForm token={token} component={editing} onSaved={saved} onCancel={() => { setShowForm(false); setEditing(null); }} />}
      <div className="catalog-table-wrap"><table className="catalog-table"><thead><tr><th>COMPONENT</th><th>SKU</th><th>CATEGORY</th><th>REVISION</th><th>UNIT</th><th>USED IN</th><th>STATUS</th><th /></tr></thead>
        <tbody>{components.map((component) => <tr key={component.id}><td><Link className="catalog-product-cell" to={`/components/${component.id}`}><Photo src={component.photo} label={component.name} /><span><strong>{component.name}</strong><small>{component.description || "No description"}</small></span></Link></td><td><span className="catalog-mono">{component.sku}</span></td><td>{component.category || "—"}</td><td>{component.revision_number || "—"}</td><td>{component.unit}</td><td>{component.used_in_count || 0} {component.used_in_count === 1 ? "product" : "products"}</td><td><span className={`status-pill ${component.active ? "is-active" : "is-inactive"}`}>{component.active ? "Active" : "Inactive"}</span></td><td className="catalog-row-actions">{canEdit && <button className="catalog-icon-button" onClick={() => { setEditing(component); setShowForm(true); }} aria-label={`Edit ${component.name}`}>✎</button>}</td></tr>)}
          {!components.length && !error && <tr><td className="catalog-empty-cell" colSpan="8">No components found.</td></tr>}</tbody>
      </table></div>
      <div className="foundation-note"><span>i</span><p>Components used in a product BOM cannot be deleted. Deactivate a component to stop using it in new BOMs.</p></div>
    </section>
  );
}

export function ComponentDetailPage({ token, user }) {
  const { componentId } = useParams();
  const [component, setComponent] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    request(`/api/components/${componentId}/`, token)
      .then(setComponent)
      .catch((requestError) => setError(requestError.message));
  }, [componentId, token]);
  if (!component) return <section className="page-content"><Alert message={error} /><div className="loading-screen">Loading component…</div></section>;
  return (
    <section className="page-content">
      <Link className="catalog-back-link" to="/components">← All components</Link>
      <div className="product-detail-heading component-detail-heading">
        <Photo src={component.photo} label={component.name} />
        <div className="product-detail-title"><div className="eyebrow">COMPONENT / {component.category || "UNCATEGORIZED"}</div><h1>{component.name}</h1><p>{component.sku} <span>·</span> Revision {component.revision_number || "—"} <span>·</span> {component.unit}</p></div>
        <span className={`status-pill ${component.active ? "is-active" : "is-inactive"}`}>{component.active ? "Active" : "Inactive"}</span>
      </div>
      {error && <Alert message={error} />}
      <div className="component-detail-meta"><div><span>SKU</span><strong>{component.sku}</strong></div><div><span>REVISION</span><strong>{component.revision_number || "—"}</strong></div><div><span>REORDER LEVEL</span><strong>{component.reorder_level} {component.unit}</strong></div><div><span>USED IN</span><strong>{component.used_in_count} {component.used_in_count === 1 ? "product" : "products"}</strong></div></div>
      <div className="catalog-toolbar bom-toolbar"><div><div className="eyebrow">PRODUCT REFERENCES</div><h2>Used in</h2></div></div>
      <div className="catalog-table-wrap"><table className="catalog-table"><thead><tr><th>PRODUCT</th><th>PRODUCT NUMBER</th><th>REVISION</th><th>QUANTITY PER UNIT</th></tr></thead>
        <tbody>{component.used_in?.map((usage) => <tr key={`${usage.product_id}-${usage.revision_id}`}><td><Link className="catalog-text-link" to={`/products/${usage.product_id}`}>{usage.product_name} →</Link></td><td><span className="catalog-mono">{usage.product_number}</span></td><td><span className="revision-pill">REV {usage.revision_number}</span></td><td>{usage.quantity_per_unit} {component.unit}</td></tr>)}
          {!component.used_in?.length && <tr><td className="catalog-empty-cell" colSpan="4">This component is not used by any product revision yet.</td></tr>}</tbody>
      </table></div>
      <div className="foundation-note"><span>i</span><p>Inventory and stock quantities are managed in a later module. This catalog record is independent of stock availability.</p></div>
    </section>
  );
}
