import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { apiRequest, useNotify } from "./notifications.jsx";
import { formatMoney } from "./moneyFormat.js";
import { formatCatalogDateTime } from "./catalogFormat.js";
import BarcodeScanInput from "./BarcodeScanInput.jsx";
import {
  readFormValues,
  UnsavedChangesDialog,
  useUnsavedChanges,
} from "./useUnsavedChanges.jsx";

async function request(path, token, options = {}) {
  return apiRequest(path, token, options);
}

function photoUrl(photo) {
  if (!photo) return null;
  const url = new URL(photo, window.location.origin);
  const mediaPath = url.pathname.startsWith("/media/")
    ? url.pathname
    : `/media/${url.pathname.replace(/^\/+/, "")}`;
  return `${mediaPath}${url.search}${url.hash}`;
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

function photoFileName(photo) {
  if (!photo) return "";
  try {
    return decodeURIComponent(new URL(photo, window.location.origin).pathname.split("/").pop());
  } catch {
    return photo.split("/").pop();
  }
}

function formatFileSize(size) {
  return size < 1024 * 1024
    ? `${Math.max(1, Math.round(size / 1024))} KB`
    : `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function PhotoField({ label, name, photo, initialFile = null, onChange, onFileChange }) {
  const inputRef = useRef(null);
  const [file, setFile] = useState(initialFile);
  const [previewUrl, setPreviewUrl] = useState(null);
  useEffect(() => {
    if (!file) {
      setPreviewUrl(null);
      return undefined;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  function cancelReplacement() {
    setFile(null);
    if (inputRef.current) inputRef.current.value = "";
    onFileChange?.(null);
    onChange?.();
  }

  return (
    <div className="catalog-file-field">
      <label htmlFor={`${name}-input`}>{label}</label>
      <input
        accept="image/jpeg,image/png,image/webp"
        id={`${name}-input`}
        name={name}
        onChange={(event) => {
          const selectedFile = event.target.files?.[0] || null;
          setFile(selectedFile);
          onFileChange?.(selectedFile);
          onChange?.();
        }}
        ref={inputRef}
        type="file"
      />
      <div className="catalog-photo-preview">
        {file || photo
          ? <img
              alt={file ? `Preview of ${file.name}` : `Current photo: ${photoFileName(photo)}`}
              src={file ? previewUrl : photoUrl(photo)}
            />
          : <div className="catalog-photo-preview-empty" aria-hidden="true">▧</div>}
        <div>
          <strong>{file ? "New photo (will replace the current one)" : photo ? "Current photo" : "No photo yet"}</strong>
          {(file || photo) && <span>{file ? `${file.name} · ${formatFileSize(file.size)}` : photoFileName(photo)}</span>}
          {file && <button className="catalog-text-link" onClick={cancelReplacement} type="button">Cancel replacement</button>}
        </div>
      </div>
    </div>
  );
}

function ProductForm({ token, product, onSaved, onCancel }) {
  const { changed } = useNotify();
  const formRef = useRef(null);
  const unsaved = useUnsavedChanges(formRef, undefined, onCancel);
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
      generic_name: data.get("generic_name"),
      net_quantity_text: data.get("net_quantity_text"),
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
    <form className="catalog-form" onChange={unsaved.updateDirty} onInput={unsaved.updateDirty} onSubmit={submit} ref={formRef}>
      <div className="catalog-form-heading">
        <div><span className="eyebrow">PRODUCT RECORD</span><h2>{product ? "Edit product" : "New product"}</h2>{unsaved.dirty && <span className="unsaved-indicator">Unsaved changes</span>}</div>
        <button className="catalog-icon-button" type="button" onClick={unsaved.requestClose} aria-label="Close form">×</button>
      </div>
      {error && <Alert message={error} />}
      <div className="catalog-form-grid">
        <label>Product number<input name="product_number" required defaultValue={product?.product_number} /></label>
        <label>Product name<input name="name" required defaultValue={product?.name} /></label>
        <label>SKU<input name="sku" required defaultValue={product?.sku} /></label>
        <label>Generic name<input name="generic_name" defaultValue={product?.generic_name || ""} /></label>
        <label>Net quantity<input name="net_quantity_text" defaultValue={product?.net_quantity_text || "1 Unit"} /></label>
        <PhotoField label="Product photo" name="photo" photo={product?.photo} onChange={unsaved.updateDirty} />
        <label className="catalog-wide">Description<textarea name="description" rows="3" defaultValue={product?.description} /></label>
        <label className="catalog-check"><input name="active" type="checkbox" defaultChecked={product?.active ?? true} /> Active product</label>
      </div>
      <div className="catalog-form-actions">
        <button className="catalog-secondary-button" type="button" onClick={unsaved.requestClose}>Cancel</button>
        <button className="catalog-primary-button" disabled={saving}>{saving ? "Saving…" : "Save product"}</button>
      </div>
      <UnsavedChangesDialog open={unsaved.confirming} onSave={unsaved.saveFromDialog} onDiscard={unsaved.discard} onKeepEditing={unsaved.keepEditing} />
    </form>
  );
}

function ProductWizard({ token, onSaved, onCancel }) {
  const { changed } = useNotify();
  const formRef = useRef(null);
  const [step, setStep] = useState(1);
  const [details, setDetails] = useState(null);
  const [components, setComponents] = useState([]);
  const [componentSearch, setComponentSearch] = useState("");
  const [lines, setLines] = useState([]);
  const [quickCreate, setQuickCreate] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const readValues = useCallback(() => {
    if (!formRef.current) return { ...details, ...(lines.length ? { bom_lines: lines } : {}) };
    const values = readFormValues(formRef.current);
    if (!values.photo && details?.photo) values.photo = details.photo;
    return values;
  }, [details, lines]);
  const unsaved = useUnsavedChanges(formRef, readValues, onCancel);

  useEffect(() => {
    if (details) unsaved.updateDirty();
  }, [details, lines, step, unsaved.updateDirty]);

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
      generic_name: data.get("generic_name"),
      net_quantity_text: data.get("net_quantity_text"),
      sku: data.get("sku"),
      active: data.get("active") === "on",
      photo: data.get("photo")?.size ? data.get("photo") : details?.photo || null,
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
        <div className="catalog-form-heading-actions">{unsaved.dirty && <span className="unsaved-indicator">Unsaved changes</span>}<button className="catalog-icon-button" type="button" onClick={unsaved.requestClose} aria-label="Close form">×</button></div>
      </div>
      <div className="wizard-progress"><span className={step >= 1 ? "complete" : ""} /><span className={step >= 2 ? "complete" : ""} /></div>
      {error && <Alert message={error} />}
      {step === 1 ? (
        <form className="catalog-wizard-details" onChange={unsaved.updateDirty} onInput={unsaved.updateDirty} onSubmit={saveProductDetails} ref={formRef}>
          <div className="catalog-form-grid">
            <label>Product number<input defaultValue={details?.product_number} name="product_number" required /></label>
            <label>Product name<input defaultValue={details?.name} name="name" required /></label>
            <label>SKU<input defaultValue={details?.sku} name="sku" required /></label>
            <label>Generic name<input defaultValue={details?.generic_name || ""} name="generic_name" /></label>
            <label>Net quantity<input defaultValue={details?.net_quantity_text || "1 Unit"} name="net_quantity_text" /></label>
            <PhotoField label="Product photo" name="photo" photo={null} initialFile={details?.photo} onChange={unsaved.updateDirty} onFileChange={(photo) => setDetails((current) => current ? { ...current, photo } : current)} />
            <label className="catalog-wide">Description<textarea defaultValue={details?.description} name="description" rows="3" /></label>
            <label className="catalog-check"><input defaultChecked={details?.active ?? true} name="active" type="checkbox" /> Active product</label>
          </div>
          <div className="catalog-form-actions"><button className="catalog-secondary-button" onClick={unsaved.requestClose} type="button">Cancel</button><button className="catalog-primary-button">Continue to BOM →</button></div>
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
          {quickCreate &&           <div className="catalog-modal-backdrop" onClick={(event) => {
            if (event.target === event.currentTarget) event.currentTarget.querySelector(".catalog-form-heading .catalog-icon-button")?.click();
          }}><div className="catalog-modal" onClick={(event) => event.stopPropagation()}><ComponentForm token={token} quickCreate onSaved={(component) => { addComponent(component); setComponentSearch(""); setQuickCreate(false); }} onCancel={() => setQuickCreate(false)} /></div></div>}
          <div className="catalog-form-actions"><button className="catalog-secondary-button" onClick={() => setStep(1)} type="button">← Back</button><button className="catalog-primary-button" disabled={!lines.length || saving || lines.some((line) => !Number(line.quantity_per_unit) || Number(line.quantity_per_unit) <= 0)} onClick={createProduct} type="button">{saving ? "Saving product and BOM…" : `Create product · ${lines.length} BOM ${lines.length === 1 ? "line" : "lines"}`}</button></div>
          {!lines.length && <div className="wizard-required-note">Add at least one active component before creating this product.</div>}
        </div>
      )}
      <UnsavedChangesDialog
        open={unsaved.confirming}
        onSave={() => unsaved.saveFromDialog(step === 2 ? createProduct : undefined)}
        onDiscard={unsaved.discard}
        onKeepEditing={unsaved.keepEditing}
      />
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
      {showForm && (
        <div className="catalog-modal-backdrop" onClick={(event) => {
          if (event.target === event.currentTarget) {
            event.currentTarget.querySelector(".catalog-form-heading .catalog-icon-button")?.click();
          }
        }}>
          <div className="catalog-modal" onClick={(event) => event.stopPropagation()}>
            {editing
              ? <ProductForm token={token} product={editing} onSaved={saved} onCancel={() => { setShowForm(false); setEditing(null); }} />
              : <ProductWizard token={token} onSaved={saved} onCancel={() => setShowForm(false)} />}
          </div>
        </div>
      )}
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
  const validProductId = /^[1-9]\d*$/.test(productId || "")
    && Number.isSafeInteger(Number(productId));
  const [product, setProduct] = useState(null);
  const [components, setComponents] = useState([]);
  const [bom, setBom] = useState([]);
  const [revisionId, setRevisionId] = useState("");
  const [units, setUnits] = useState("1");
  const [showBomForm, setShowBomForm] = useState(false);
  const [showRevisionForm, setShowRevisionForm] = useState(false);
  const [error, setError] = useState("");
  const [notFound, setNotFound] = useState(false);
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
    if (!validProductId) {
      setProduct(null);
      setNotFound(true);
      return;
    }
    setProduct(null);
    setNotFound(false);
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
    }).catch((requestError) => {
      if (requestError.status === 404) setNotFound(true);
      else setError(requestError.message);
    });
  }, [productId, token, refresh, loadBom, validProductId]);

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

  if (!validProductId || notFound) {
    return <section className="page-content"><h1>Product not found</h1><Link className="catalog-back-link" to="/products">← All products</Link></section>;
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
      {product.product_price_summary && (
        <ProductPriceSummary summary={product.product_price_summary} canEdit={canEdit} />
      )}
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

function ProductPriceSummary({ summary, canEdit }) {
  return (
    <section className="product-price-summary" aria-label="Product pricing summary">
      <div className="panel-heading">
        <div><div className="eyebrow">PRODUCT PRICING</div><h2>Price summary</h2></div>
        <span className={`status-pill ${summary.status === "Complete" ? "is-active" : "is-inactive"}`}>{summary.status}</span>
      </div>
      {summary.cost_changed_review && <div className="pricing-review-badge">Cost changed: review price</div>}
      {!summary.complete && summary.missing_components?.length > 0 && (
        <p className="pricing-incomplete-note">Missing component prices: {summary.missing_components.join(", ")}</p>
      )}
      <div className="product-price-summary-grid">
        <div><span>Computed cost</span><strong>{summary.cost ? formatMoney(summary.cost) : "Incomplete"}</strong></div>
        <div><span>Selling price (incl. tax)</span><strong>{summary.selling_price_incl_tax ? formatMoney(summary.selling_price_incl_tax) : "Not set"}</strong></div>
        <div><span>MRP (incl. tax)</span><strong>{summary.mrp ? formatMoney(summary.mrp) : "Not set"}</strong></div>
        <div><span>GST</span><strong>{summary.gst_rate_percent ? `${summary.gst_rate_percent}%` : "—"}</strong></div>
        <div><span>HSN code</span><strong>{summary.hsn_code || "—"}</strong></div>
        <div><span>Last changed</span><strong>{formatCatalogDateTime(summary.effective_from)}</strong></div>
      </div>
      {canEdit && summary.history?.length > 0 && (
        <details className="product-price-history">
          <summary>Price history · {summary.history.length}</summary>
          <ol>{summary.history.map((entry) => (
            <li key={entry.id}>
              <strong>{formatMoney(entry.selling_price_incl_tax)} · MRP {formatMoney(entry.mrp)}</strong>
              <span>{formatCatalogDateTime(entry.effective_from)} · {entry.reason || "No reason supplied"}</span>
            </li>
          ))}</ol>
        </details>
      )}
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

function ComponentForm({ token, component, initialBarcode = "", quickCreate = false, onSaved, onCancel }) {
  const { changed, notify } = useNotify();
  const navigate = useNavigate();
  const formRef = useRef(null);
  const unsaved = useUnsavedChanges(formRef, undefined, onCancel);
  const [error, setError] = useState("");
  const [matches, setMatches] = useState([]);
  const [duplicateSearch, setDuplicateSearch] = useState("");
  const [barcode, setBarcode] = useState(component?.barcode || initialBarcode || "");
  const [barcodeOwner, setBarcodeOwner] = useState(null);
  const [pricePreview, setPricePreview] = useState(null);
  const [previewingPrice, setPreviewingPrice] = useState(false);
  const [saving, setSaving] = useState(false);

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

  const lookupBarcode = useCallback(async (code) => {
    setError("");
    setBarcodeOwner(null);
    try {
      const result = await request(
        `/api/barcodes/lookup/?code=${encodeURIComponent(code)}`,
        token,
      );
      if (result.type === "none") {
        setBarcode(code);
        window.requestAnimationFrame(unsaved.updateDirty);
        notify("Barcode is available for this component.", "success");
        return true;
      }
      if (result.type === "component") {
        if (component?.id === result.id) {
          setBarcode(code);
          window.requestAnimationFrame(unsaved.updateDirty);
          notify(`${result.name} (${result.sku}) is this component.`, "info");
          return true;
        }
        setBarcode(component?.barcode || initialBarcode || "");
        window.requestAnimationFrame(unsaved.updateDirty);
        const owner = { id: result.id, name: result.name, sku: result.sku };
        setBarcodeOwner(owner);
        notify(`Already registered to ${owner.name} (${owner.sku}).`, "warning", {
          label: "Open",
          onClick: () => navigate(`/components/${owner.id}`),
        });
        return false;
      }
      setBarcode(component?.barcode || initialBarcode || "");
      window.requestAnimationFrame(unsaved.updateDirty);
      const kind = result.type === "product" ? "a product" : "a finished unit";
      notify(`This code belongs to ${kind}, not a component.`, "info");
      return false;
    } catch (requestError) {
      setError(requestError.message);
      return false;
    }
  }, [component?.barcode, component?.id, initialBarcode, navigate, notify, token, unsaved.updateDirty]);

  async function submit(event) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const body = new FormData();
    ["sku", "name", "description", "category", "unit", "revision_number", "reorder_level"].forEach((key) => body.append(key, data.get(key) || ""));
    body.append("barcode", barcode);
    body.append("active", data.get("active") === "on");
    const priceValue = data.get("cost_price");
    if (priceValue !== null && (priceValue !== "" || component)) {
      body.append("cost_price", priceValue);
      body.append("cost_price_reason", data.get("cost_price_reason") || "");
    }
    const photo = data.get("photo");
    if (photo?.size) body.append("photo", photo);
    setError("");
    setSaving(true);
    try {
      const saved = await request(component ? `/api/components/${component.id}/` : quickCreate ? "/api/components/quick-create/" : "/api/components/", token, {
        method: component ? "PATCH" : "POST",
        body,
      });
      if (component && component.cost_price !== saved.cost_price) {
        notify(
          `${saved.name} (${saved.sku}) purchase price: ${component.cost_price === null ? "Not set" : formatMoney(component.cost_price)} → ${saved.cost_price === null ? "Not set" : formatMoney(saved.cost_price)}`,
          "success",
        );
      }
      changed(component
        ? (saved.active ? "componentUpdated" : "componentDeactivated")
        : "componentCreated", {
        name: saved.name,
        sku: saved.sku,
      });
      onSaved(saved);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  async function previewPrice() {
    const form = formRef.current;
    if (!component || !form) return;
    const price = new FormData(form).get("cost_price");
    setPreviewingPrice(true);
    setError("");
    try {
      const result = await request("/api/components/pricing/preview/", token, {
        method: "POST",
        body: JSON.stringify({
          items: [{ component_id: component.id, cost_price: price === "" ? null : price }],
        }),
      });
      setPricePreview(result);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setPreviewingPrice(false);
    }
  }
  return (
    <form
      className={`catalog-form component-form${quickCreate ? " quick-create" : ""}`}
      onChange={(event) => {
        unsaved.updateDirty();
        if (event.target.name === "cost_price") setPricePreview(null);
      }}
      onInput={(event) => {
        unsaved.updateDirty();
        if (event.target.name === "cost_price") setPricePreview(null);
      }}
      onSubmit={submit}
      ref={formRef}
    >
      <div className="catalog-form-heading"><div><span className="eyebrow">COMPONENT RECORD</span><h2>{component ? "Edit component" : "New component"}</h2>{unsaved.dirty && <span className="unsaved-indicator">Unsaved changes</span>}</div><button className="catalog-icon-button" onClick={unsaved.requestClose} type="button">×</button></div>
      {error && <Alert message={error} />}
      <div className="catalog-form-grid">
        <label>SKU<input name="sku" onChange={(event) => setDuplicateSearch(event.target.value)} required defaultValue={component?.sku} /></label>
        <label>Component name<input name="name" onChange={(event) => setDuplicateSearch(event.target.value)} required defaultValue={component?.name} /></label>
        {!quickCreate && <div className="component-barcode-field">
          <label htmlFor="component-barcode">Barcode</label>
          <BarcodeScanInput
            id="component-barcode"
            value={barcode}
            onChange={(event) => {
              setBarcode(event.target.value);
              setBarcodeOwner(null);
              window.requestAnimationFrame(unsaved.updateDirty);
            }}
            onScan={lookupBarcode}
            placeholder="Scan or type barcode"
            autoFocus
          />
          {barcodeOwner && (
            <div className="barcode-existing-owner" role="status">
              <span>Already registered to {barcodeOwner.name} ({barcodeOwner.sku})</span>
              <button className="catalog-text-link" type="button" onClick={() => navigate(`/components/${barcodeOwner.id}`)}>Open</button>
            </div>
          )}
        </div>}
        <label>Category<input name="category" defaultValue={component?.category} /></label>
        <label>Unit of measure<input name="unit" required placeholder="piece, mm, g…" defaultValue={component?.unit} /></label>
        <label>Revision number<input name="revision_number" defaultValue={component?.revision_number} /></label>
        <label>Reorder level<input min="0" name="reorder_level" step="0.001" type="number" defaultValue={component?.reorder_level || "0"} /></label>
        <label>Purchase price (INR, excluding GST)<input inputMode="decimal" name="cost_price" pattern="(?:0|[1-9]\d{0,9})(?:\.\d{1,2})?" defaultValue={component?.cost_price ?? "0.00"} /></label>
        {component?.cost_price !== null && component?.cost_price !== undefined && component?.cost_price !== "" && (
          <label>Reason for price change<input maxLength="500" name="cost_price_reason" placeholder="Required when changing this price" /></label>
        )}
        <PhotoField label="Component photo" name="photo" photo={component?.photo} onChange={unsaved.updateDirty} />
        <label className="catalog-wide">Description<textarea name="description" rows="3" defaultValue={component?.description} /></label>
        <label className="catalog-check"><input defaultChecked={component?.active ?? true} name="active" type="checkbox" /> Active component</label>
      </div>
      {component && (
        <div className="component-price-preview">
          <button className="catalog-secondary-button" disabled={previewingPrice || saving} onClick={previewPrice} type="button">
            {previewingPrice ? "Calculating…" : "Preview product cost changes"}
          </button>
          {pricePreview && (
            <div className="component-price-preview-results">
              {!pricePreview.products?.length && <p>No products use this component’s current BOM price.</p>}
              {pricePreview.products?.map((product) => (
                <div key={product.product_id}>
                  <strong>{product.product_number} · {product.product_name}</strong>
                  <span>
                    {product.computed_cost === null
                      ? `Current cost incomplete (${product.missing_component_count} unpriced)`
                      : formatMoney(product.computed_cost)}
                    {" → "}
                    {product.proposed_cost === null
                      ? `Incomplete (${product.proposed_missing_component_count} unpriced)`
                      : formatMoney(product.proposed_cost)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {!component && matches.length > 0 && <div className="duplicate-warning"><strong>Possible existing components found</strong><span>Review these matches before creating a new component.</span><div>{matches.slice(0, 5).map((match) => <Link key={match.id} to={`/components/${match.id}`} onClick={unsaved.requestClose}>{match.sku} · {match.name}</Link>)}</div></div>}
      <div className="catalog-form-actions"><button className="catalog-secondary-button" onClick={unsaved.requestClose} type="button">Cancel</button><button className="catalog-primary-button" disabled={saving}>{saving ? "Saving…" : "Save component"}</button></div>
      <UnsavedChangesDialog open={unsaved.confirming} onSave={unsaved.saveFromDialog} onDiscard={unsaved.discard} onKeepEditing={unsaved.keepEditing} />
    </form>
  );
}

export function ComponentListPage({ token, user }) {
  const { notify } = useNotify();
  const navigate = useNavigate();
  const [components, setComponents] = useState([]);
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState({ sku: "", name: "", category: "", revision: "", active: "" });
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editing, setEditing] = useState(null);
  const [initialBarcode, setInitialBarcode] = useState("");
  const [showScanPanel, setShowScanPanel] = useState(false);
  const [scanMode, setScanMode] = useState("lookup");
  const [scanValue, setScanValue] = useState("");
  const [scanResult, setScanResult] = useState(null);
  const [scanError, setScanError] = useState("");
  const [scanSession, setScanSession] = useState([]);
  const [receiptPending, setReceiptPending] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const canEdit = user.role === "SUPER_ADMIN";
  const sessionFormRef = useRef(null);
  const sessionRef = useRef(scanSession);
  const idempotencyKeyRef = useRef(null);
  const receiptFingerprintRef = useRef(null);
  const receiptPendingRef = useRef(false);
  sessionRef.current = scanSession;
  function closeScanPanel() {
    sessionRef.current = [];
    setScanSession([]);
    setShowScanPanel(false);
    window.requestAnimationFrame(() => unsaved.updateDirty());
  }
  const unsaved = useUnsavedChanges(
    sessionFormRef,
    () => ({ scanSession: sessionRef.current }),
    closeScanPanel,
  );

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
    setInitialBarcode("");
    setRefresh((count) => count + 1);
  }

  const lookupScannedCode = useCallback(async (code) => {
    if (receiptPendingRef.current) return false;
    setScanError("");
    try {
      const result = await request(
        `/api/barcodes/lookup/?code=${encodeURIComponent(code)}`,
        token,
      );
      setScanResult({ ...result, scannedCode: code });
      if (result.type === "none") {
        notify("Barcode not registered", "warning", canEdit && scanMode === "lookup" ? {
          label: "Register new component",
          onClick: () => {
            setInitialBarcode(code);
            setEditing(null);
            setShowForm(true);
          },
        } : null);
        return false;
      }
      if (result.type === "product") {
        notify("This code belongs to a product, not a component.", "info");
        return false;
      }
      if (result.type === "finished_unit") {
        notify("This code belongs to a finished unit, not a component.", "info");
        return false;
      }

      if (scanMode === "lookup") {
        notify(`${result.name} (${result.sku}) found`, "success");
        return true;
      }
      if (!result.active) {
        notify(`${result.name} (${result.sku}) is inactive.`, "warning");
        return false;
      }

      const current = sessionRef.current;
      const existing = current.find((item) => item.component.id === result.id);
      const quantity = existing ? Number(existing.quantity) + 1 : 1;
      const next = existing
        ? current.map((item) => item.component.id === result.id
            ? { ...item, quantity: String(quantity) }
            : item)
        : [...current, {
            component: result,
            quantity: "1",
            currentOnHand: result.stock.on_hand,
          }];
      sessionRef.current = next;
      setScanSession(next);
      notify(
        `${result.name} (${result.sku}): +1, session total ${quantity}`,
        "success",
        null,
        `component-receive-scan-${result.id}`,
      );
      window.requestAnimationFrame(unsaved.updateDirty);
      return true;
    } catch (requestError) {
      setScanError(requestError.message);
      return false;
    }
  }, [canEdit, notify, scanMode, token, unsaved.updateDirty]);

  function updateSession(updater) {
    if (receiptPendingRef.current) return;
    const next = updater(sessionRef.current);
    sessionRef.current = next;
    setScanSession(next);
    window.requestAnimationFrame(unsaved.updateDirty);
  }

  function changeScanMode(nextMode) {
    setScanMode(nextMode);
    setScanResult(null);
    setScanError("");
  }

  async function confirmReceipt() {
    const currentSession = sessionRef.current;
    if (receiptPendingRef.current || !currentSession.length || currentSession.some((item) => !Number.isFinite(Number(item.quantity))
      || Number(item.quantity) <= 0)) return false;
    const items = currentSession.map((item) => ({
      component_id: item.component.id,
      quantity: item.quantity,
    }));
    const requestFingerprint = JSON.stringify(items);
    if (!idempotencyKeyRef.current || receiptFingerprintRef.current !== requestFingerprint) {
      idempotencyKeyRef.current = globalThis.crypto?.randomUUID?.()
        || `barcode-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      receiptFingerprintRef.current = requestFingerprint;
    }
    receiptPendingRef.current = true;
    setReceiptPending(true);
    setScanError("");
    try {
      const response = await request("/api/components/receive-scanned/", token, {
        method: "POST",
        body: {
          idempotency_key: idempotencyKeyRef.current,
          items,
          note: "",
        },
      });
      response.items.forEach((item) => {
        notify(
          `Received ${item.quantity_received} x ${item.component_name}: on hand ${item.on_hand_before} → ${item.on_hand_after}, available ${item.available_after}`,
          "success",
        );
      });
      idempotencyKeyRef.current = null;
      receiptFingerprintRef.current = null;
      sessionRef.current = [];
      setScanSession([]);
      setRefresh((count) => count + 1);
      window.requestAnimationFrame(unsaved.updateDirty);
      return true;
    } catch (requestError) {
      setScanError(requestError.message);
      return false;
    } finally {
      receiptPendingRef.current = false;
      setReceiptPending(false);
    }
  }

  const saveReceiptAndContinue = async () => {
    if (await confirmReceipt()) unsaved.discard();
  };

  function registerScannedComponent() {
    if (!scanResult?.scannedCode || scanResult.type !== "none") return;
    setInitialBarcode(scanResult.scannedCode);
    setEditing(null);
    setShowForm(true);
  }

  return (
    <section className="page-content">
      <div className="page-heading">
        <div><div className="eyebrow">TAMIRABOT / CATALOG</div><h1>Components</h1><p className="page-subtitle">Reusable parts referenced by product bills of material.</p></div>
        <div className="component-header-actions">
          {canEdit && <Link className="catalog-secondary-button" to="/component-pricing">Component pricing</Link>}
          <button className="catalog-secondary-button" disabled={receiptPending} type="button" onClick={() => {
            if (showScanPanel) unsaved.requestClose();
            else setShowScanPanel(true);
          }}>
            {showScanPanel ? "Close scan" : "Scan"}
          </button>
          {canEdit && <button className="catalog-primary-button" onClick={() => { setEditing(null); setInitialBarcode(""); setShowForm(true); }}>＋ New component</button>}
        </div>
      </div>
      <form hidden ref={sessionFormRef} />
      {showScanPanel && (
        <section className="component-scan-panel">
          <div className="component-scan-heading">
            <div><div className="eyebrow">COMPONENT BARCODE</div><h2>Scan components</h2></div>
            <button className="catalog-icon-button" type="button" aria-label="Close scan panel" onClick={unsaved.requestClose}>×</button>
          </div>
          <div className="component-scan-modes" role="tablist" aria-label="Component scan mode">
            <button aria-selected={scanMode === "lookup"} className={scanMode === "lookup" ? "active" : ""} onClick={() => changeScanMode("lookup")} role="tab" type="button">Lookup</button>
            {canEdit && <button aria-selected={scanMode === "receive"} className={scanMode === "receive" ? "active" : ""} onClick={() => changeScanMode("receive")} role="tab" type="button">Receive stock</button>}
          </div>
          <BarcodeScanInput
            value={scanValue}
            disabled={receiptPending}
            onChange={(event) => setScanValue(event.target.value)}
            onScan={lookupScannedCode}
            continuousCamera
          />
          {scanError && <Alert message={scanError} />}
          {scanResult?.type === "none" && scanMode === "lookup" && (
            <div className="barcode-result-message">
              <strong>Barcode not registered</strong>
              {canEdit && <button className="catalog-primary-button" type="button" onClick={registerScannedComponent}>Register new component</button>}
            </div>
          )}
          {scanResult?.type === "component" && scanMode === "lookup" && (
            <div className="component-scan-result">
              <Photo src={scanResult.photo} label={scanResult.name} />
              <div className="component-scan-result-details">
                <strong>{scanResult.name} <span>({scanResult.sku})</span></strong>
                <span>{scanResult.category || "Uncategorized"} · {scanResult.unit} · Rev {scanResult.revision_number || "—"}</span>
                <span>Reorder level {scanResult.reorder_level} {scanResult.unit} · {scanResult.active ? "Active" : "Inactive"}</span>
                <span>On hand {scanResult.stock.on_hand} · Reserved {scanResult.stock.reserved} · Available {scanResult.stock.available}</span>
              </div>
              <button className="catalog-secondary-button" type="button" onClick={() => navigate(`/components/${scanResult.id}`)}>Open component</button>
            </div>
          )}
          {scanMode === "receive" && canEdit && (
            <div className="component-scan-session">
              <div className="component-scan-session-heading">
                <div><strong>Scan session</strong><span>{scanSession.length} component lines</span></div>
                <button className="catalog-text-link" disabled={!scanSession.length || receiptPending} type="button" onClick={() => updateSession(() => [])}>Clear</button>
              </div>
              {scanSession.map((item) => (
                <div className="component-scan-session-row" key={item.component.id}>
                  <span><strong>{item.component.name}</strong><small>{item.component.sku} · on hand {item.currentOnHand}</small></span>
                  <button className="catalog-secondary-button" disabled={receiptPending} type="button" aria-label={`Decrease ${item.component.name}`} onClick={() => updateSession((current) => current.map((line) => line.component.id === item.component.id ? { ...line, quantity: String(Math.max(0.001, Number(line.quantity) - 1)) } : line))}>−</button>
                  <input aria-label={`Quantity for ${item.component.name}`} disabled={receiptPending} min="0.001" step="0.001" type="number" value={item.quantity} onChange={(event) => updateSession((current) => current.map((line) => line.component.id === item.component.id ? { ...line, quantity: event.target.value } : line))} />
                  <button className="catalog-secondary-button" disabled={receiptPending} type="button" aria-label={`Increase ${item.component.name}`} onClick={() => updateSession((current) => current.map((line) => line.component.id === item.component.id ? { ...line, quantity: String(Number(line.quantity) + 1) } : line))}>＋</button>
                  <button className="catalog-icon-button delete" disabled={receiptPending} type="button" aria-label={`Remove ${item.component.name}`} onClick={() => updateSession((current) => current.filter((line) => line.component.id !== item.component.id))}>×</button>
                </div>
              ))}
              {!scanSession.length && <p className="component-scan-empty">Scan an active component to add it to this receipt.</p>}
              <div className="component-scan-session-actions">
                <button className="catalog-primary-button" disabled={receiptPending || !scanSession.length || scanSession.some((item) => !Number.isFinite(Number(item.quantity)) || Number(item.quantity) <= 0)} onClick={confirmReceipt} type="button">{receiptPending ? "Receiving…" : "Confirm receipt"}</button>
              </div>
            </div>
          )}
        </section>
      )}
      <div className="catalog-toolbar"><label className="catalog-search"><span>⌕</span><input aria-label="Search components" onChange={(event) => setSearch(event.target.value)} placeholder="Search SKU or component name…" value={search} /></label><span className="catalog-count">{components.length} components</span></div>
      <div className="catalog-filter-row">
        <input aria-label="Filter component SKU" onChange={(event) => setFilters((current) => ({ ...current, sku: event.target.value }))} placeholder="SKU" value={filters.sku} />
        <input aria-label="Filter component name" onChange={(event) => setFilters((current) => ({ ...current, name: event.target.value }))} placeholder="Name" value={filters.name} />
        <input aria-label="Filter component category" onChange={(event) => setFilters((current) => ({ ...current, category: event.target.value }))} placeholder="Category" value={filters.category} />
        <input aria-label="Filter component revision" onChange={(event) => setFilters((current) => ({ ...current, revision: event.target.value }))} placeholder="Revision" value={filters.revision} />
        <select aria-label="Filter active components" onChange={(event) => setFilters((current) => ({ ...current, active: event.target.value }))} value={filters.active}><option value="">All status</option><option value="true">Active</option><option value="false">Inactive</option></select>
      </div>
      <Alert message={error} />
      {showForm && (
        <div className="catalog-modal-backdrop" onClick={(event) => {
          if (event.target === event.currentTarget) {
            event.currentTarget.querySelector(".catalog-form-heading .catalog-icon-button")?.click();
          }
        }}>
          <div className="catalog-modal" onClick={(event) => event.stopPropagation()}>
            <ComponentForm token={token} component={editing} initialBarcode={initialBarcode} onSaved={saved} onCancel={() => { setShowForm(false); setEditing(null); }} />
          </div>
        </div>
      )}
      <div className="catalog-table-wrap"><table className="catalog-table"><thead><tr><th>COMPONENT</th><th>SKU</th><th>CATEGORY</th><th>REVISION</th><th>UNIT</th><th>USED IN</th><th>PRICE</th><th>STATUS</th><th /></tr></thead>
        <tbody>{components.map((component) => <tr key={component.id}><td><Link className="catalog-product-cell" to={`/components/${component.id}`}><Photo src={component.photo} label={component.name} /><span><strong>{component.name}</strong><small>{component.description || "No description"}</small></span></Link></td><td><span className="catalog-mono">{component.sku}</span></td><td>{component.category || "—"}</td><td>{component.revision_number || "—"}</td><td>{component.unit}</td><td>{component.used_in_count || 0} {component.used_in_count === 1 ? "product" : "products"}</td><td><span className={`status-pill ${component.cost_price_set ? "is-active" : "is-inactive"}`}>{component.cost_price_set ? "Price set" : "Price not set"}</span></td><td><span className={`status-pill ${component.active ? "is-active" : "is-inactive"}`}>{component.active ? "Active" : "Inactive"}</span></td><td className="catalog-row-actions">{canEdit && <button className="catalog-icon-button" onClick={() => { setEditing(component); setShowForm(true); }} aria-label={`Edit ${component.name}`}>✎</button>}</td></tr>)}
          {!components.length && !error && <tr><td className="catalog-empty-cell" colSpan="9">No components found.</td></tr>}</tbody>
      </table></div>
      <div className="foundation-note"><span>i</span><p>Components used in a product BOM cannot be deleted. Deactivate a component to stop using it in new BOMs.</p></div>
      <UnsavedChangesDialog
        open={unsaved.confirming}
        onSave={() => unsaved.saveFromDialog(saveReceiptAndContinue)}
        onDiscard={unsaved.discard}
        onKeepEditing={unsaved.keepEditing}
      />
    </section>
  );
}

export function ComponentDetailPage({ token, user }) {
  const { componentId } = useParams();
  const [component, setComponent] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    request(`/api/components/${componentId}/`, token)
      .then(setComponent)
      .catch((requestError) => setError(requestError.message));
  }, [componentId, token, refresh]);
  if (!component) return <section className="page-content"><Alert message={error} /><div className="loading-screen">Loading component…</div></section>;
  return (
    <section className="page-content">
      <Link className="catalog-back-link" to="/components">← All components</Link>
      <div className="product-detail-heading component-detail-heading">
        <Photo src={component.photo} label={component.name} />
        <div className="product-detail-title"><div className="eyebrow">COMPONENT / {component.category || "UNCATEGORIZED"}</div><h1>{component.name}</h1><p>{component.sku} <span>·</span> Revision {component.revision_number || "—"} <span>·</span> {component.unit}</p></div>
        {user.role === "SUPER_ADMIN" && <button className="catalog-secondary-button" onClick={() => setShowForm(true)} type="button">Edit component</button>}
        <span className={`status-pill ${component.active ? "is-active" : "is-inactive"}`}>{component.active ? "Active" : "Inactive"}</span>
      </div>
      {error && <Alert message={error} />}
      <div className="component-detail-meta"><div><span>SKU</span><strong>{component.sku}</strong></div><div><span>REVISION</span><strong>{component.revision_number || "—"}</strong></div><div><span>REORDER LEVEL</span><strong>{component.reorder_level} {component.unit}</strong></div><div><span>USED IN</span><strong>{component.used_in_count} {component.used_in_count === 1 ? "product" : "products"}</strong></div></div>
      {user.role === "SUPER_ADMIN" && (
        <section className="component-cost-impact">
          <div className="catalog-toolbar">
            <div><div className="eyebrow">CURRENT REVISION COST</div><h2>Used in {component.product_costs?.length || 0} products</h2></div>
            <span className={`status-pill ${component.cost_price === null ? "is-inactive" : "is-active"}`}>
              {component.cost_price === null ? "Price not set" : formatMoney(component.cost_price)}
            </span>
          </div>
          {!component.product_costs?.length && <p className="pricing-muted">This component is not used in any current product BOM.</p>}
          {component.product_costs?.map((product) => (
            <div className="component-cost-impact-row" key={product.product_id}>
              <Link to={`/products/${product.product_id}`}>{product.product_number} · {product.product_name}</Link>
              <span>{product.computed_cost === null
                ? `Incomplete · ${product.missing_component_count} unpriced component${product.missing_component_count === 1 ? "" : "s"}`
                : formatMoney(product.computed_cost)}</span>
            </div>
          ))}
        </section>
      )}
      {showForm && (
        <div className="catalog-modal-backdrop" onClick={(event) => {
          if (event.target === event.currentTarget) event.currentTarget.querySelector(".catalog-form-heading .catalog-icon-button")?.click();
        }}>
          <div className="catalog-modal" onClick={(event) => event.stopPropagation()}>
            <ComponentForm
              token={token}
              component={component}
              onSaved={(saved) => {
                setComponent(saved);
                setShowForm(false);
                setRefresh((count) => count + 1);
              }}
              onCancel={() => setShowForm(false)}
            />
          </div>
        </div>
      )}
      <div className="catalog-toolbar bom-toolbar"><div><div className="eyebrow">PRODUCT REFERENCES</div><h2>Used in</h2></div></div>
      <div className="catalog-table-wrap"><table className="catalog-table"><thead><tr><th>PRODUCT</th><th>PRODUCT NUMBER</th><th>REVISION</th><th>QUANTITY PER UNIT</th></tr></thead>
        <tbody>{component.used_in?.map((usage, index) => {
          const productId = String(usage.product_id ?? "");
          const hasProductId = /^[1-9]\d*$/.test(productId)
            && Number.isSafeInteger(Number(productId));
          return (
            <tr key={usage.revision_id ?? index}>
              <td>{hasProductId
                ? <Link className="catalog-text-link" to={`/products/${usage.product_id}`}>{usage.product_name} →</Link>
                : usage.product_name}</td>
              <td><span className="catalog-mono">{usage.product_number}</span></td>
              <td><span className="revision-pill">REV {usage.revision_number}</span></td>
              <td>{usage.quantity_per_unit} {component.unit}</td>
            </tr>
          );
        })}
          {!component.used_in?.length && <tr><td className="catalog-empty-cell" colSpan="4">This component is not used by any product revision yet.</td></tr>}</tbody>
      </table></div>
      <div className="foundation-note"><span>i</span><p>Inventory and stock quantities are managed in a later module. This catalog record is independent of stock availability.</p></div>
    </section>
  );
}
