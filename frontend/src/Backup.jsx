import { useState } from "react";
import { apiRequest, useNotify } from "./notifications.jsx";
import "./Backup.css";

async function submitBackupForm(token, fields) {
  const body = new FormData();
  Object.entries(fields).forEach(([key, value]) => body.append(key, value));
  return apiRequest("/api/backups/restore/", token, { method: "POST", body });
}

export default function BackupPage({ token }) {
  const { notify } = useNotify();
  const [password, setPassword] = useState("");
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function createBackup(event) {
    event.preventDefault();
    setError("");
    setBusy("backup");
    try {
      const response = await fetch("/api/backups/", {
        method: "POST",
        headers: {
          Authorization: `Token ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ password }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || body.password?.[0] || "The backup could not be created.");
      }
      const blob = await response.blob();
      const filename = response.headers.get("Content-Disposition")?.match(/filename="?([^";]+)"?/i)?.[1]
        || `tamirabot-backup-${new Date().toISOString().slice(0, 10)}.zip`;
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      notify("Tamirabot backup created and downloaded.", "success");
      setPassword("");
    } catch (requestError) {
      setError(requestError.message === "Failed to fetch"
        ? "Network connection failed. Check your internet and try again."
        : requestError.message || "The backup could not be created.");
    } finally {
      setBusy("");
    }
  }

  async function previewRestore(event) {
    event.preventDefault();
    setError("");
    setPreview(null);
    setConfirmed(false);
    setBusy("preview");
    try {
      const result = await submitBackupForm(token, {
        action: "dry_run",
        backup: file,
        password,
      });
      setPreview(result);
    } catch (requestError) {
      setError(requestError.message || "The backup could not be previewed. Check the file and retry.");
    } finally {
      setBusy("");
    }
  }

  async function restoreBackup() {
    setError("");
    setBusy("restore");
    try {
      const result = await submitBackupForm(token, {
        action: "restore",
        backup: file,
        password,
        preview_token: preview.preview_token,
      });
      notify(`Backup from ${formatDate(result.created_at)} restored. The workspace will reload.`, "success");
      setTimeout(() => window.location.reload(), 1600);
    } catch (requestError) {
      setError(requestError.message || "The backup could not be restored. Your existing data was not intentionally changed.");
      setBusy("");
    }
  }

  function chooseFile(event) {
    setFile(event.target.files?.[0] || null);
    setPreview(null);
    setConfirmed(false);
    setError("");
  }

  return (
    <section className="page-content backup-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow">TAMIRABOT / DATA PROTECTION</div>
          <h1>Backup &amp; Restore</h1>
          <p className="page-subtitle">Create a portable copy or restore a verified backup.</p>
        </div>
        <span className="role-badge">Super Admin</span>
      </div>

      {error && <div className="catalog-alert" role="alert">{error}</div>}

      <div className="backup-warning" role="note">
        <strong>Restoring replaces the current database and all uploaded media.</strong>
        <span>Only restore a backup from a trusted source. The file includes business data and user account records.</span>
      </div>

      <section className="backup-card">
        <div className="backup-card-heading">
          <span className="backup-card-icon" aria-hidden="true">↓</span>
          <div>
            <h2>Create a backup</h2>
            <p>Downloads one ZIP with a PostgreSQL dump, portable table exports, uploaded media, and a checksum manifest.</p>
          </div>
        </div>
        <form className="backup-form" onSubmit={createBackup}>
          <label>
            Confirm your password
            <input
              autoComplete="current-password"
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </label>
          <button className="catalog-primary-button" disabled={Boolean(busy)} type="submit">
            {busy === "backup" ? "Preparing backup…" : "Create and download backup"}
          </button>
        </form>
      </section>

      <section className="backup-card">
        <div className="backup-card-heading">
          <span className="backup-card-icon restore" aria-hidden="true">↑</span>
          <div>
            <h2>Restore a backup</h2>
            <p>Choose a Tamirabot ZIP. The system verifies its checksums and schema, then previews the changes before importing.</p>
          </div>
        </div>
        <form className="backup-form" onSubmit={previewRestore}>
          <label>
            Backup ZIP file
            <input accept=".zip,application/zip" onChange={chooseFile} required type="file" />
          </label>
          <label>
            Confirm your password
            <input
              autoComplete="current-password"
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </label>
          {file && <div className="backup-file-name">Selected: {file.name} ({formatBytes(file.size)})</div>}
          <button className="catalog-secondary-button" disabled={!file || Boolean(busy)} type="submit">
            {busy === "preview" ? "Verifying backup…" : "Verify and preview changes"}
          </button>
        </form>

        {preview && (
          <div className="backup-preview" aria-live="polite">
            <div className="backup-preview-heading">
              <div>
                <span className="eyebrow">VERIFIED BACKUP</span>
                <h3>Restore preview</h3>
              </div>
              <span className="backup-verified">Checksums and schema valid</span>
            </div>
            <p>
              Created {formatDate(preview.created_at)} · App version {preview.app_version || "unversioned"} ·
              {" "}{Object.keys(preview.record_counts || {}).length} database tables ·
              {" "}{preview.media_file_count} media files
            </p>
            <div className="backup-change-summary">
              <strong>{preview.database_tables_changed} table counts differ</strong>
              <span>Media: {preview.media_added.length} to add, {preview.media_changed.length} to replace, {preview.media_removed.length} to remove.</span>
            </div>
            {preview.table_changes.length > 0 && (
              <div className="backup-table-wrap">
                <table className="backup-table">
                  <thead><tr><th>Database table</th><th>Current rows</th><th>Backup rows</th><th>Contents</th></tr></thead>
                  <tbody>
                    {preview.table_changes.map((change) => (
                      <tr key={change.table}>
                        <td>{change.table}</td><td>{change.current}</td><td>{change.backup}</td>
                        <td>{change.contents_changed ? "Different" : "Same"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {(preview.media_added.length > 0 || preview.media_changed.length > 0 || preview.media_removed.length > 0) && (
              <details className="backup-media-details">
                <summary>Show media file changes</summary>
                <MediaChangeList label="Added" files={preview.media_added} />
                <MediaChangeList label="Replaced" files={preview.media_changed} />
                <MediaChangeList label="Removed" files={preview.media_removed} />
              </details>
            )}
            <label className="backup-confirmation">
              <input checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} type="checkbox" />
              <span>I understand this will replace all current database records and uploaded media.</span>
            </label>
            <p className="backup-preview-expiry">This preview is tied to this backup and expires after 15 minutes.</p>
            <button
              className="backup-danger-button"
              disabled={!confirmed || Boolean(busy)}
              onClick={restoreBackup}
              type="button"
            >
              {busy === "restore" ? "Restoring…" : "Restore this backup"}
            </button>
          </div>
        )}
      </section>
    </section>
  );
}

function MediaChangeList({ label, files }) {
  if (!files.length) return null;
  return (
    <div className="backup-media-list">
      <strong>{label} ({files.length})</strong>
      <ul>{files.slice(0, 20).map((name) => <li key={name}>{name}</li>)}</ul>
      {files.length > 20 && <span>And {files.length - 20} more files.</span>}
    </div>
  );
}

function formatDate(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "an unknown date";
  return new Intl.DateTimeFormat("en-IN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Kolkata",
  }).format(date);
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 1024) return `${bytes || 0} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes;
  let index = -1;
  do {
    value /= 1024;
    index += 1;
  } while (value >= 1024 && index < units.length - 1);
  return `${value.toFixed(1)} ${units[index]}`;
}
