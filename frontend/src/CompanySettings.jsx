import { useEffect, useRef, useState } from "react";
import { apiRequest, useNotify } from "./notifications.jsx";
import { UnsavedChangesDialog, useUnsavedChanges } from "./useUnsavedChanges.jsx";

async function request(path, token, options = {}) {
  return apiRequest(path, token, options);
}

const fieldLabels = {
  legal_name: "Legal name",
  trade_name: "Trade name",
  address_line1: "Address line 1",
  address_line2: "Address line 2",
  city: "City",
  state_code: "State / UT",
  pincode: "PIN code",
  gstin: "GSTIN",
  pan: "PAN",
  phone: "Company phone",
  email: "Company email",
  packer_manufacturer_name: "Packer / manufacturer name",
  packer_address: "Packer address",
  country_of_origin: "Country of origin",
  consumer_care_name: "Consumer care name",
  consumer_care_address: "Consumer care address",
  consumer_care_phone: "Consumer care phone",
  consumer_care_email: "Consumer care email",
  invoice_prefix: "Invoice prefix",
  credit_note_prefix: "Credit note prefix",
  logo: "Company logo",
  authorised_signatory_name: "Authorised signatory",
  signature_image: "Signature image",
};

function complianceItems(profile) {
  const missing = (field) => !profile?.[field];
  return {
    labels: [
      ["packer_manufacturer_name", missing("packer_manufacturer_name")],
      ["packer_address", missing("packer_address")],
      ["country_of_origin", missing("country_of_origin")],
      ["consumer_care_name", missing("consumer_care_name")],
      ["consumer_care_address", missing("consumer_care_address")],
      ["consumer_care_phone", missing("consumer_care_phone")],
      ["consumer_care_email", missing("consumer_care_email")],
    ].filter(([, isMissing]) => isMissing).map(([field]) => field),
    invoices: [
      ["legal_name", missing("legal_name")],
      ["address_line1", missing("address_line1")],
      ["city", missing("city")],
      ["state_code", missing("state_code")],
      ["pincode", missing("pincode")],
      ["gstin", missing("gstin")],
      ["invoice_prefix", missing("invoice_prefix")],
      ["authorised_signatory_name", missing("authorised_signatory_name")],
    ].filter(([, isMissing]) => isMissing).map(([field]) => field),
  };
}

export default function CompanySettingsPage({ token }) {
  const { notify } = useNotify();
  const formRef = useRef(null);
  const [profile, setProfile] = useState(null);
  const [states, setStates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const unsaved = useUnsavedChanges(
    formRef,
    undefined,
  );

  useEffect(() => {
    let active = true;
    request("/api/company-profile/", token)
      .then((result) => {
        if (!active) return;
        setProfile(result.profile);
        setStates(result.states);
        setError("");
      })
      .catch((requestError) => {
        if (active) setError(requestError.message);
      })
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [token]);

  useEffect(() => {
    if (profile) unsaved.resetClean();
  }, [profile, unsaved.resetClean]);

  async function save(event) {
    event.preventDefault();
    setSaving(true);
    setError("");
    const body = new FormData();
    for (const [key, value] of new FormData(event.currentTarget).entries()) {
      if (value instanceof File && value.size === 0) continue;
      body.append(key, value);
    }
    try {
      const result = await request("/api/company-profile/", token, {
        method: "PATCH",
        body,
      });
      setProfile(result.profile);
      setStates(result.states);
      unsaved.resetClean();
      notify("Company and compliance settings updated.", "success");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  function focusField(field) {
    const input = document.getElementById(`company-${field}`);
    input?.scrollIntoView({ behavior: "smooth", block: "center" });
    input?.focus();
  }

  if (loading) return <section className="page-content"><div className="loading-screen">Loading company settings…</div></section>;
  if (!profile) return <section className="page-content"><div className="catalog-alert" role="alert">{error || "Company settings could not be loaded."}</div></section>;
  const readiness = complianceItems(profile);

  return (
    <section className="page-content company-settings-page">
      <div className="page-heading">
        <div><div className="eyebrow">TAMIRABOT / ADMINISTRATION</div><h1>Company & Compliance</h1><p className="page-subtitle">Maintain the company details used on labels and future invoices.</p></div>
      </div>
      {error && <div className="catalog-alert" role="alert">{error}</div>}
      <section className="panel compliance-readiness">
        <div className="panel-heading"><div><div className="eyebrow">READINESS CHECK</div><h2>Compliance readiness</h2><p>Incomplete information is highlighted before it is needed on printed documents.</p></div></div>
        <div className="compliance-columns">
          {[["Product labels", readiness.labels], ["Invoices", readiness.invoices]].map(([title, missing]) => (
            <div className="compliance-group" key={title}>
              <h3>{title}</h3>
              {missing.length ? <ul>{missing.map((field) => (
                <li key={field}><span>No {fieldLabels[field]}</span><button onClick={() => focusField(field)} type="button">Go to field</button></li>
              ))}</ul> : <p className="compliance-ready">Ready — required company details are present.</p>}
            </div>
          ))}
        </div>
      </section>
      <form className="company-settings-form" key={profile.updated_at} onChange={unsaved.updateDirty} onInput={unsaved.updateDirty} onSubmit={save} ref={formRef}>
        <section className="panel">
          <div className="panel-heading"><div><h2>Legal business details</h2><p>GSTIN state prefix must match the selected state code.</p></div>{unsaved.dirty && <span className="unsaved-indicator">Unsaved changes</span>}</div>
          <div className="company-settings-grid">
            <label>Legal name<input id="company-legal_name" name="legal_name" defaultValue={profile.legal_name} /></label>
            <label>Trade name<input id="company-trade_name" name="trade_name" defaultValue={profile.trade_name} /></label>
            <label>Address line 1<input id="company-address_line1" name="address_line1" defaultValue={profile.address_line1} /></label>
            <label>Address line 2<input id="company-address_line2" name="address_line2" defaultValue={profile.address_line2} /></label>
            <label>City<input id="company-city" name="city" defaultValue={profile.city} /></label>
            <label>State / UT<select id="company-state_code" name="state_code" defaultValue={profile.state_code || ""}><option value="">Select state / UT</option>{states.map((state) => <option key={state.state_code} value={state.state_code}>{state.state_code} · {state.name}</option>)}</select></label>
            <label>PIN code<input id="company-pincode" inputMode="numeric" maxLength="6" name="pincode" pattern="[0-9]{6}" defaultValue={profile.pincode} /></label>
            <label>GSTIN<input id="company-gstin" maxLength="15" name="gstin" defaultValue={profile.gstin} /></label>
            <label>PAN (optional)<input id="company-pan" maxLength="10" name="pan" defaultValue={profile.pan} /></label>
            <label>Phone<input id="company-phone" name="phone" type="tel" defaultValue={profile.phone} /></label>
            <label>Email<input id="company-email" name="email" type="email" defaultValue={profile.email} /></label>
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading"><div><h2>Product label & consumer care</h2><p>These details can be shown only on labels users are already permitted to print.</p></div></div>
          <div className="company-settings-grid">
            <label>Packer / manufacturer name<input id="company-packer_manufacturer_name" name="packer_manufacturer_name" defaultValue={profile.packer_manufacturer_name} /></label>
            <label>Country of origin<input id="company-country_of_origin" name="country_of_origin" defaultValue={profile.country_of_origin || "India"} /></label>
            <label className="company-settings-wide">Packer address<textarea id="company-packer_address" name="packer_address" rows="2" defaultValue={profile.packer_address} /></label>
            <label>Consumer care name<input id="company-consumer_care_name" name="consumer_care_name" defaultValue={profile.consumer_care_name} /></label>
            <label>Consumer care phone<input id="company-consumer_care_phone" name="consumer_care_phone" type="tel" defaultValue={profile.consumer_care_phone} /></label>
            <label>Consumer care email<input id="company-consumer_care_email" name="consumer_care_email" type="email" defaultValue={profile.consumer_care_email} /></label>
            <label className="company-settings-wide">Consumer care address<textarea id="company-consumer_care_address" name="consumer_care_address" rows="2" defaultValue={profile.consumer_care_address} /></label>
            <label>Company logo<input accept="image/jpeg,image/png,image/webp" id="company-logo" name="logo" type="file" />{profile.logo && <small>Current file: {profile.logo.split("/").pop()}</small>}</label>
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading"><div><h2>Invoice & authorisation</h2><p>Prepared for future invoice and credit-note features.</p></div></div>
          <div className="company-settings-grid">
            <label>Invoice prefix<input id="company-invoice_prefix" name="invoice_prefix" defaultValue={profile.invoice_prefix} /></label>
            <label>Credit note prefix<input id="company-credit_note_prefix" name="credit_note_prefix" defaultValue={profile.credit_note_prefix} /></label>
            <label>Authorised signatory<input id="company-authorised_signatory_name" name="authorised_signatory_name" defaultValue={profile.authorised_signatory_name} /></label>
            <label>Signature image<input accept="image/jpeg,image/png,image/webp" id="company-signature_image" name="signature_image" type="file" />{profile.signature_image && <small>Current file: {profile.signature_image.split("/").pop()}</small>}</label>
          </div>
        </section>
        <div className="company-settings-actions">
          <button className="catalog-primary-button" disabled={saving}>{saving ? "Saving…" : "Save company settings"}</button>
        </div>
      </form>
      <UnsavedChangesDialog open={unsaved.confirming} onSave={unsaved.saveFromDialog} onDiscard={unsaved.discard} onKeepEditing={unsaved.keepEditing} />
    </section>
  );
}
