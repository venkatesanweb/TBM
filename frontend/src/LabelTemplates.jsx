import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { apiRequest, useNotify } from "./notifications.jsx";
import { UnsavedChangesDialog, useUnsavedChanges } from "./useUnsavedChanges.jsx";

const defaultToggles = {
  show_company_trade_name: true,
  show_company_logo: true,
  show_product_generic_name: true,
  show_order_reference: true,
  show_invoice_number: true,
};

const sizes = [
  { width_mm: 100, height_mm: 150, label: "100 × 150 mm (standard)" },
  { width_mm: 105, height_mm: 148, label: "A6 (105 × 148 mm)" },
  { width_mm: 210, height_mm: 297, label: "A4 (210 × 297 mm)" },
];

const optionalFields = [
  ["show_company_trade_name", "Company trade name"],
  ["show_company_logo", "Company logo"],
  ["show_product_generic_name", "Product common / generic name"],
  ["show_order_reference", "Order reference"],
  ["show_invoice_number", "Invoice number when issued"],
];

async function request(path, token, options = {}) {
  return apiRequest(path, token, options);
}

function blankForm(platformId = "") {
  return {
    platform: String(platformId),
    name: "",
    width_mm: "100",
    height_mm: "150",
    field_toggles: { ...defaultToggles },
    is_default: false,
  };
}

export default function LabelTemplatesPage({ token }) {
  const { notify } = useNotify();
  const formRef = useRef(null);
  const unsaved = useUnsavedChanges(formRef);
  const [platforms, setPlatforms] = useState([]);
  const [templates, setTemplates] = useState([]);
  const [form, setForm] = useState(blankForm());
  const [editingId, setEditingId] = useState(null);
  const [formKey, setFormKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const [platformRows, templateRows] = await Promise.all([
        request("/api/platforms/", token),
        request("/api/label-templates/", token),
      ]);
      setPlatforms(platformRows.filter((platform) => platform.label_template_type === "TAMIRABOT"));
      setTemplates(templateRows);
    } catch (failure) {
      setError(failure.message);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!loading) unsaved.resetClean();
  }, [formKey, loading, unsaved.resetClean]);

  function startNew() {
    setEditingId(null);
    setForm(blankForm(platforms[0]?.id || ""));
    setFormKey((current) => current + 1);
    setError("");
  }

  function editTemplate(template) {
    setEditingId(template.id);
    setForm({
      platform: String(template.platform),
      name: template.name,
      width_mm: String(template.width_mm),
      height_mm: String(template.height_mm),
      field_toggles: { ...defaultToggles, ...template.field_toggles },
      is_default: template.is_default,
    });
    setFormKey((current) => current + 1);
    setError("");
  }

  function updateField(event) {
    const { name, value, checked, type } = event.target;
    if (name.startsWith("field_toggles.")) {
      const field = name.slice("field_toggles.".length);
      setForm((current) => ({
        ...current,
        field_toggles: { ...current.field_toggles, [field]: checked },
      }));
      return;
    }
    setForm((current) => ({
      ...current,
      [name]: type === "checkbox" ? checked : value,
    }));
  }

  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const path = editingId
        ? `/api/label-templates/${editingId}/`
        : "/api/label-templates/";
      await request(path, token, {
        method: editingId ? "PATCH" : "POST",
        body: JSON.stringify({
          ...form,
          platform: Number(form.platform),
          width_mm: Number(form.width_mm),
          height_mm: Number(form.height_mm),
        }),
      });
      notify(
        `Label template ${form.name} ${editingId ? "updated" : "created"}.`,
        "success",
      );
      setEditingId(null);
      setForm(blankForm(platforms[0]?.id || ""));
      setFormKey((current) => current + 1);
      await load();
    } catch (failure) {
      setError(failure.message);
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <section className="page-content"><p className="muted">Loading label templates…</p></section>;
  }

  return (
    <section className="page-content label-template-page">
      <Link className="catalog-back-link" to="/sales">← Sales register</Link>
      <div className="page-heading">
        <div>
          <div className="eyebrow">SALES TRACKING / SETTINGS</div>
          <h1>Label templates</h1>
          <p className="page-subtitle">Configure optional fields and label paper size. Legal Metrology declarations remain on every generated Tamirabot label.</p>
        </div>
        <button className="catalog-primary-button" disabled={!platforms.length} onClick={startNew} type="button">New template</button>
      </div>
      {error && <div className="form-error" role="alert">{error}</div>}
      {!platforms.length && <div className="empty-state">No generated Tamirabot label platform is configured.</div>}

      <section className="panel label-template-list">
        <div className="panel-heading"><div><h2>Configured templates</h2><p>Only one template per platform is used as the default.</p></div></div>
        {templates.length ? <div className="table-wrap"><table>
          <thead><tr><th>Platform</th><th>Name</th><th>Paper size</th><th>Default</th><th /></tr></thead>
          <tbody>{templates.map((template) => <tr key={template.id}>
            <td>{template.platform_name}</td>
            <td><strong>{template.name}</strong></td>
            <td>{template.width_mm} × {template.height_mm} mm</td>
            <td>{template.is_default ? "Yes" : "No"}</td>
            <td><button className="catalog-text-link" onClick={() => editTemplate(template)} type="button">Edit</button></td>
          </tr>)}</tbody>
        </table></div> : <div className="empty-state">No templates yet. Create a default template before printing legal labels.</div>}
      </section>

      {platforms.length > 0 && <form className="panel label-template-form" key={formKey} onChange={() => window.requestAnimationFrame(unsaved.updateDirty)} onSubmit={save} ref={formRef}>
        <div className="panel-heading"><div><h2>{editingId ? "Edit label template" : "Create label template"}</h2><p>Preview and print sizing are applied in the sale detail page.</p></div></div>
        <div className="label-template-fields">
          <label>Platform<select name="platform" onChange={updateField} required value={form.platform}>
            <option value="">Select platform</option>
            {platforms.map((platform) => <option key={platform.id} value={platform.id}>{platform.name}</option>)}
          </select></label>
          <label>Template name<input maxLength="120" name="name" onChange={updateField} required value={form.name} /></label>
          <label>Paper size<select name="size" onChange={(event) => {
            const selected = sizes.find((size) => `${size.width_mm}x${size.height_mm}` === event.target.value);
            if (selected) setForm((current) => ({
              ...current,
              width_mm: String(selected.width_mm),
              height_mm: String(selected.height_mm),
            }));
          }} value={`${form.width_mm}x${form.height_mm}`}>
            {sizes.map((size) => <option key={size.label} value={`${size.width_mm}x${size.height_mm}`}>{size.label}</option>)}
          </select></label>
          <label className="label-template-default"><input checked={form.is_default} name="is_default" onChange={updateField} type="checkbox" /> Use as the default template for this platform</label>
        </div>
        <fieldset className="label-template-options">
          <legend>Optional fields</legend>
          {optionalFields.map(([field, label]) => <label key={field}>
            <input checked={form.field_toggles[field]} name={`field_toggles.${field}`} onChange={updateField} type="checkbox" />
            {label}
          </label>)}
        </fieldset>
        <p className="label-template-locked-note">Ship-to details, serial barcode, MRP and the complete legal declarations are required and cannot be disabled.</p>
        <div className="catalog-form-actions">
          <button className="catalog-primary-button" disabled={saving}>{saving ? "Saving…" : editingId ? "Save template" : "Create template"}</button>
          {editingId && <button className="catalog-secondary-button" onClick={startNew} type="button">Cancel edit</button>}
        </div>
        <UnsavedChangesDialog open={unsaved.confirming} onSave={unsaved.saveFromDialog} onDiscard={unsaved.discard} onKeepEditing={unsaved.keepEditing} />
      </form>}
    </section>
  );
}
