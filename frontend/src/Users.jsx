import { useCallback, useEffect, useState } from "react";

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
    const messages = [];
    const collect = (value) => {
      if (Array.isArray(value)) value.forEach(collect);
      else if (value && typeof value === "object") Object.values(value).forEach(collect);
      else if (value) messages.push(String(value));
    };
    collect(body);
    throw new Error(messages.join(" ") || `Request failed (${response.status}).`);
  }
  return response.status === 204 ? null : response.json();
}

export default function UsersPage({ token, currentUser }) {
  const [users, setUsers] = useState([]);
  const [showForm, setShowForm] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);

  const loadUsers = useCallback(async () => {
    try {
      setUsers(await request("/api/users/", token));
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
      await request("/api/users/", token, {
        method: "POST",
        body: JSON.stringify({
          username: form.get("username").trim(),
          email: form.get("email").trim(),
          password,
          role: "ADMIN",
        }),
      });
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
      setUsers((existing) => existing.map((item) => item.id === updated.id ? updated : item));
      setNotice(`Updated ${updated.username}.`);
    } catch (requestError) {
      setError(requestError.message);
    }
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
