import { useCallback, useEffect, useState } from "react";
import { apiRequest, useNotify } from "./notifications.jsx";

const DASHBOARD_SECTIONS = [
  ["sales_overview", "Sales overview"],
  ["payments", "Payments"],
  ["returns", "Returns"],
  ["inventory", "Inventory"],
  ["finished_goods", "Finished goods"],
  ["production", "Production"],
];
const ACCESS_PRESETS = [
  ["Sales", ["sales_overview", "payments", "returns"], false],
  ["Inventory", ["inventory", "finished_goods"], false],
  ["Production", ["production"], false],
  ["All sections + exports", DASHBOARD_SECTIONS.map(([key]) => key), true],
  ["No access", [], false],
];

async function request(path, token, options = {}) {
  return apiRequest(path, token, options);
}

export default function UsersPage({ token, currentUser }) {
  const { changed } = useNotify();
  const [users, setUsers] = useState([]);
  const [savedAccess, setSavedAccess] = useState({});
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const [savingAccess, setSavingAccess] = useState({});

  const loadUsers = useCallback(async () => {
    try {
      const loaded = await request("/api/users/", token);
      setUsers(loaded);
      setSavedAccess(Object.fromEntries(loaded.map((user) => [user.id, {
        dashboard_sections: user.dashboard_sections || [],
        can_export_dashboard_reports: Boolean(user.can_export_dashboard_reports),
      }])));
      setError("");
    } catch (requestError) {
      setError(requestError.message);
    }
  }, [token]);

  useEffect(() => {
    loadUsers();
  }, [loadUsers]);

  async function createAdmin(event) {
    event.preventDefault();
    setError("");
    setNotice("");
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const password = form.get("password");
    if (password !== form.get("password_confirmation")) {
      setError("The passwords do not match.");
      return;
    }

    setSaving(true);
    try {
      const created = await request("/api/users/", token, {
        method: "POST",
        body: JSON.stringify({
          username: form.get("username").trim(),
          email: form.get("email").trim(),
          password,
          role: "ADMIN",
        }),
      });
      changed("userCreated", { name: created.username || form.get("username").trim() });
      setShowForm(false);
      setNotice(`Admin account created for ${form.get("username").trim()}.`);
      formElement.reset();
      await loadUsers();
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSaving(false);
    }
  }

  async function updateUser(user, changes) {
    setError("");
    setNotice("");
    try {
      const updated = await request(`/api/users/${user.id}/`, token, {
        method: "PATCH",
        body: JSON.stringify(changes),
      });
      changed(
        changes.is_active === false
          ? "userDeactivated"
          : "userUpdated",
        { name: updated.username },
      );
      setUsers((existing) => existing.map((item) => item.id === updated.id ? updated : item));
      setNotice(`Updated ${updated.username}.`);
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  function updateDashboardDraft(userId, changes) {
    setUsers((existing) => existing.map((item) => item.id === userId ? { ...item, ...changes } : item));
  }

  async function saveDashboardAccess(user) {
    setError("");
    setNotice("");
    setSavingAccess((current) => ({ ...current, [user.id]: true }));
    try {
      const updated = await request(`/api/users/${user.id}/dashboard-access/`, token, {
        method: "PATCH",
        body: JSON.stringify({
          dashboard_sections: user.dashboard_sections || [],
          can_export_dashboard_reports: Boolean(user.can_export_dashboard_reports),
        }),
      });
      setUsers((existing) => existing.map((item) => item.id === updated.id ? updated : item));
      const prior = savedAccess[user.id] || {};
      setSavedAccess((existing) => ({
        ...existing,
        [updated.id]: {
          dashboard_sections: updated.dashboard_sections || [],
          can_export_dashboard_reports: Boolean(updated.can_export_dashboard_reports),
        },
      }));
      const before = new Set(prior.dashboard_sections || []);
      const after = new Set(updated.dashboard_sections || []);
      const changes = [
        ...DASHBOARD_SECTIONS.filter(([key]) => !before.has(key) && after.has(key)).map(([, label]) => `${label} granted`),
        ...DASHBOARD_SECTIONS.filter(([key]) => before.has(key) && !after.has(key)).map(([, label]) => `${label} revoked`),
      ];
      if (Boolean(prior.can_export_dashboard_reports) !== Boolean(updated.can_export_dashboard_reports)) {
        changes.push(`report exports ${updated.can_export_dashboard_reports ? "enabled" : "disabled"}`);
      }
      const summary = changes.join(", ") || "no access changes";
      changed("dashboardAccessUpdated", { name: updated.username, changes: summary });
      setNotice(`Dashboard access for ${updated.username}: ${summary}.`);
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSavingAccess((current) => ({ ...current, [user.id]: false }));
    }
  }

  function applyAccessPreset(user, sections, exportAccess) {
    updateDashboardDraft(user.id, {
      dashboard_sections: sections,
      can_export_dashboard_reports: exportAccess,
    });
  }

  return (
    <section className="page-content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">TAMIRABOT / ADMINISTRATION</div>
          <h1>Users</h1>
          <p className="page-subtitle">Create Admin accounts and manage user access.</p>
        </div>
        <button className="catalog-primary-button" onClick={() => { setShowForm((open) => !open); setError(""); }} type="button">
          {showForm ? "Close form" : "＋ Add Admin"}
        </button>
      </div>

      {error && <div className="catalog-alert" role="alert">{error}</div>}
      {notice && <div className="catalog-alert success" role="status">{notice}</div>}

      {showForm && (
        <form className="catalog-form user-create-form" onSubmit={createAdmin}>
          <div className="catalog-form-heading">
            <div><span className="eyebrow">NEW ACCOUNT</span><h2>Create Admin</h2></div>
          </div>
          <div className="catalog-form-grid">
            <label>Username<input autoComplete="username" name="username" required /></label>
            <label>Email address<input autoComplete="email" name="email" required type="email" /></label>
            <label>Password<input autoComplete="new-password" minLength="12" name="password" required type="password" /></label>
            <label>Confirm password<input autoComplete="new-password" minLength="12" name="password_confirmation" required type="password" /></label>
          </div>
          <p className="user-password-note">Use a unique password with at least 12 characters. The server also checks it against the account details and common passwords.</p>
          <div className="catalog-form-actions">
            <button className="catalog-secondary-button" onClick={() => setShowForm(false)} type="button">Cancel</button>
            <button className="catalog-primary-button" disabled={saving} type="submit">{saving ? "Creating account…" : "Create Admin account"}</button>
          </div>
        </form>
      )}

      <div className="catalog-toolbar users-toolbar">
        <div><div className="eyebrow">ACCESS CONTROL</div><h2>System users</h2></div>
        <span className="catalog-count">{users.length} {users.length === 1 ? "user" : "users"}</span>
      </div>
      <div className="catalog-table-wrap">
        <table className="catalog-table users-table">
          <thead><tr><th>USER</th><th>EMAIL</th><th>ROLE</th><th>STATUS</th><th>JOINED</th><th>ACCESS ACTIONS</th></tr></thead>
          <tbody>
            {users.map((user) => (
                <tr key={user.id}>
                  <td><strong className="user-name">{user.username}{user.id === currentUser.id ? " (you)" : ""}</strong></td>
                  <td>{user.email}</td>
                  <td>
                    <select
                      aria-label={`Role for ${user.username}`}
                      disabled={user.id === currentUser.id}
                      onChange={(event) => updateUser(user, { role: event.target.value })}
                      value={user.role}
                    >
                      <option value="ADMIN">Admin</option>
                      <option value="SUPER_ADMIN">Super Admin</option>
                    </select>
                  </td>
                  <td><span className={`status-pill ${user.is_active ? "is-active" : "is-inactive"}`}>{user.is_active ? "Active" : "Deactivated"}</span></td>
                  <td>{new Date(user.date_joined).toLocaleDateString()}</td>
                  <td className="user-actions">
                    <button
                      className={user.is_active ? "catalog-danger-button" : "catalog-secondary-button"}
                      disabled={user.id === currentUser.id}
                      onClick={() => updateUser(user, { is_active: !user.is_active })}
                      type="button"
                    >
                      {user.is_active ? "Deactivate" : "Reactivate"}
                    </button>
                    {user.role === "ADMIN" && <details className="dashboard-access-details"><summary>Dashboard access</summary>
                      <div className="dashboard-access-panel">
                        <div className="dashboard-access-presets">
                          {ACCESS_PRESETS.map(([label, sections, exportAccess]) => (
                            <button key={label} onClick={() => applyAccessPreset(user, sections, exportAccess)} type="button">{label}</button>
                          ))}
                        </div>
                        <fieldset>
                          <legend>Dashboard sections</legend>
                          {DASHBOARD_SECTIONS.map(([key, label]) => (
                            <label key={key}>
                              <input
                                checked={(user.dashboard_sections || []).includes(key)}
                                onChange={(event) => {
                                  const sections = new Set(user.dashboard_sections || []);
                                  if (event.target.checked) sections.add(key);
                                  else sections.delete(key);
                                  updateDashboardDraft(user.id, { dashboard_sections: [...sections] });
                                }}
                                type="checkbox"
                              />
                              {label}
                            </label>
                          ))}
                        </fieldset>
                        <label className="dashboard-export-checkbox">
                          <input
                            checked={Boolean(user.can_export_dashboard_reports)}
                            onChange={(event) => updateDashboardDraft(user.id, { can_export_dashboard_reports: event.target.checked })}
                            type="checkbox"
                          />
                          Can export Excel and PDF reports
                        </label>
                        <button className="catalog-primary-button" disabled={savingAccess[user.id]} onClick={() => saveDashboardAccess(user)} type="button">
                          {savingAccess[user.id] ? "Saving…" : "Save dashboard access"}
                        </button>
                      </div>
                    </details>}
                    {user.role === "SUPER_ADMIN" && <small className="dashboard-super-admin-access">All dashboard sections and exports</small>}
                  </td>
                </tr>
            ))}
            {!users.length && !error && <tr><td className="catalog-empty-cell" colSpan="6">No users found.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="foundation-note"><span>i</span><p>Only Super Admins can access user management. Deactivated users cannot sign in; their existing authentication tokens are revoked.</p></div>
    </section>
  );
}
