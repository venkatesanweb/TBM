import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router-dom";
import {
  ComponentDetailPage,
  ComponentListPage,
  ProductDetailPage,
  ProductListPage,
} from "./Catalog.jsx";
import UsersPage from "./Users.jsx";
import { ComponentStockDetailPage, InventoryPage } from "./Inventory.jsx";
import {
  FinishedUnitLabelPage,
  WorkOrderDetailPage,
  WorkOrderListPage,
} from "./WorkOrders.jsx";
import { FinishedGoodsPage, FinishedUnitDetailPage } from "./FinishedGoods.jsx";

const navigation = [
  { label: "Dashboard", path: "/", icon: "◫", roles: ["SUPER_ADMIN", "ADMIN"] },
  { label: "Product & BOM", path: "/products", icon: "▦", roles: ["SUPER_ADMIN", "ADMIN"] },
  { label: "Components", path: "/components", icon: "◈", roles: ["SUPER_ADMIN", "ADMIN"] },
  { label: "Work Orders", path: "/work-orders", icon: "▤", roles: ["SUPER_ADMIN", "ADMIN"] },
  { label: "Inventory", path: "/inventory", icon: "▣", roles: ["SUPER_ADMIN", "ADMIN"] },
  { label: "Finished Goods", path: "/finished-goods", icon: "▧", roles: ["SUPER_ADMIN", "ADMIN"] },
  { label: "Sales", path: "/sales", icon: "↗", roles: ["SUPER_ADMIN"] },
  { label: "Backup", path: "/backup", icon: "◷", roles: ["SUPER_ADMIN"] },
  { label: "Users", path: "/users", icon: "♙", roles: ["SUPER_ADMIN"] },
];

async function api(path, token, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Token ${token}` } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || Object.values(body).flat().join(" ") || "Request failed.");
  }
  return response.status === 204 ? null : response.json();
}

function App() {
  const [token, setToken] = useState(() => sessionStorage.getItem("tamirabot-token"));
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(Boolean(token));
  const [error, setError] = useState("");
  const location = useLocation();

  useEffect(() => {
    if (!token) {
      setUser(null);
      setLoading(false);
      return;
    }
    api("/api/auth/me/", token)
      .then(setUser)
      .catch(() => {
        sessionStorage.removeItem("tamirabot-token");
        setToken(null);
      })
      .finally(() => setLoading(false));
  }, [token]);

  async function signIn(event) {
    event.preventDefault();
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      const result = await api("/api/auth/login/", null, {
        method: "POST",
        body: JSON.stringify({
          username: form.get("username"),
          password: form.get("password"),
          second_factor_code: form.get("second_factor_code") || undefined,
        }),
      });
      sessionStorage.setItem("tamirabot-token", result.token);
      setUser(result.user);
      setToken(result.token);
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function signOut() {
    try {
      await api("/api/auth/logout/", token, { method: "POST" });
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      sessionStorage.removeItem("tamirabot-token");
      setToken(null);
      setUser(null);
    }
  }

  if (loading) return <div className="loading-screen">Opening your workspace…</div>;
  if (!user) return <Login onSubmit={signIn} error={error} />;

  const visibleNavigation = navigation.filter((item) => item.roles.includes(user.role));
  const page = visibleNavigation.find((item) => item.path === location.pathname)
    || (location.pathname.startsWith("/products/") ? visibleNavigation.find((item) => item.path === "/products") : null)
    || (location.pathname.startsWith("/components/") ? visibleNavigation.find((item) => item.path === "/components") : null)
    || (location.pathname.startsWith("/inventory/") ? visibleNavigation.find((item) => item.path === "/inventory") : null)
    || (location.pathname.startsWith("/finished-goods/") ? visibleNavigation.find((item) => item.path === "/finished-goods") : null);
  const activePage = page || (location.pathname.startsWith("/work-orders/") || location.pathname.startsWith("/finished-units/")
    ? visibleNavigation.find((item) => item.path === "/work-orders")
    : null);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link className="brand" to="/">
          <img className="brand-logo" src="/tamirabot-logo.png?v=2" alt="Tamirabot" />
        </Link>
        <div className="workspace-label">WORKSPACE</div>
        <nav className="nav-list" aria-label="Main navigation">
          {visibleNavigation.map((item) => (
            <Link
              className={`nav-link ${activePage?.path === item.path ? "active" : ""}`}
              key={item.path}
              to={item.path}
            >
              <span className="nav-icon" aria-hidden="true">{item.icon}</span>
              {item.label}
              {item.path === "/work-orders" && <span className="nav-dot" />}
            </Link>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="online-status"><span /> System online</div>
          <div className="sidebar-version">TAMIRABOT · FOUNDATION</div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumb">Workspace <span>/</span> {activePage?.label || "Workspace"}</div>
          <div className="topbar-user">
            <span className="user-avatar">{user.username.slice(0, 1).toUpperCase()}</span>
            <span className="user-meta"><strong>{user.username}</strong><small>{roleLabel(user.role)}</small></span>
            <button className="signout-button" onClick={signOut}>Sign out</button>
          </div>
        </header>
        {error && <div className="toast-error" role="alert">{error}</div>}
        <Routes>
          <Route path="/products" element={<ProductListPage token={token} user={user} />} />
          <Route path="/products/:productId" element={<ProductDetailPage token={token} user={user} />} />
          <Route path="/components" element={<ComponentListPage token={token} user={user} />} />
          <Route path="/components/:componentId" element={<ComponentDetailPage token={token} user={user} />} />
          <Route path="/inventory" element={<InventoryPage token={token} user={user} />} />
          <Route path="/inventory/:componentId" element={<ComponentStockDetailPage token={token} />} />
          <Route path="/work-orders" element={<WorkOrderListPage token={token} user={user} />} />
          <Route path="/work-orders/:workOrderId" element={<WorkOrderDetailPage token={token} user={user} />} />
          <Route path="/finished-units/:unitId/label" element={<FinishedUnitLabelPage token={token} />} />
          <Route path="/finished-goods" element={<FinishedGoodsPage token={token} user={user} />} />
          <Route path="/finished-goods/:unitId" element={<FinishedUnitDetailPage token={token} />} />
          <Route
            path="/users"
            element={user.role === "SUPER_ADMIN" ? <UsersPage token={token} currentUser={user} /> : <Navigate to="/" replace />}
          />
          {visibleNavigation.filter((item) => !["/products", "/components", "/inventory", "/work-orders", "/finished-goods", "/users"].includes(item.path)).map((item) => (
            <Route
              key={item.path}
              path={item.path}
              element={<PlaceholderPage item={item} user={user} />}
            />
          ))}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
        <AppFooter />
      </main>
    </div>
  );
}

function Login({ onSubmit, error }) {
  return (
    <main className="login-page">
      <div className="login-aside">
        <Link className="brand brand-light" to="/">
          <img className="brand-logo" src="/tamirabot-logo.png?v=2" alt="Tamirabot" />
        </Link>
        <div className="login-message">
          <div className="eyebrow"><span /> YOUR WORKSHOP, IN SYNC</div>
          <h1>Make every<br />build <em>count.</em></h1>
          <p>A single workspace for the people, parts, and products that keep your business moving.</p>
        </div>
        <div className="login-footer">SELF-HOSTED · BUILT FOR YOUR WORKFLOW</div>
      </div>
      <div className="login-panel">
        <form className="login-form" onSubmit={onSubmit}>
          <div className="form-kicker">WELCOME BACK</div>
          <h2>Sign in to Tamirabot</h2>
          <p className="form-description">Use your administrator account to continue.</p>
          {error && <div className="form-error" role="alert">{error}</div>}
          <label htmlFor="username">Username</label>
          <input autoComplete="username" id="username" name="username" required />
          <label htmlFor="password">Password</label>
          <input autoComplete="current-password" id="password" name="password" required type="password" />
          <label htmlFor="second_factor_code">Authenticator code <span className="optional-label">if enabled</span></label>
          <input autoComplete="one-time-code" id="second_factor_code" inputMode="numeric" name="second_factor_code" />
          <button className="primary-button" type="submit">Sign in <span>→</span></button>
          <div className="secure-note"><span>▣</span> Your connection is protected</div>
        </form>
      </div>
    </main>
  );
}

function AppFooter() {
  return (
    <footer className="app-footer">
      <div className="footer-brand">
        <img src="/tamirabot-logo.png?v=2" alt="Tamirabot" />
        <p>Inventory and sales operations, built around your workshop.</p>
      </div>
      <div className="footer-group">
        <h2>Connect with us</h2>
        <a href="https://www.instagram.com/tamirabot_offical/" rel="noreferrer" target="_blank"><span aria-hidden="true">◎</span> Instagram</a>
        <a href="https://www.facebook.com/TamiraBot" rel="noreferrer" target="_blank"><span aria-hidden="true">f</span> Facebook</a>
        <a href="https://www.linkedin.com/company/tamirabot/" rel="noreferrer" target="_blank"><span aria-hidden="true">in</span> LinkedIn</a>
      </div>
      <div className="footer-group">
        <h2>Contact</h2>
        <a href="mailto:tamirabotsales@gmail.com">tamirabotsales@gmail.com</a>
        <a href="mailto:sales@tamirabot.com">sales@tamirabot.com</a>
        <a href="tel:+919629644781"><span aria-hidden="true">☎</span> +91 96296 44781</a>
        <a href="https://wa.me/919629644781" rel="noreferrer" target="_blank"><span aria-hidden="true">◉</span> WhatsApp us</a>
      </div>
      <div className="footer-bottom">© {new Date().getFullYear()} Tamirabot. All rights reserved.</div>
    </footer>
  );
}

function PlaceholderPage({ item, user }) {
  return (
    <section className="page-content">
      <div className="page-heading">
        <div>
          <div className="eyebrow">TAMIRABOT / WORKSPACE</div>
          <h1>{item.label}</h1>
          <p className="page-subtitle">{pageDescription(item.path)}</p>
        </div>
        <span className="role-badge">{roleLabel(user.role)}</span>
      </div>
      {item.path === "/" ? (
        <div className="welcome-card">
          <div className="welcome-copy">
            <span className="welcome-icon">✳</span>
            <div className="eyebrow">FOUNDATION READY</div>
            <h2>Your operations, one step closer.</h2>
            <p>The secure workspace is ready. Product, inventory, work order, and sales tools will appear here as each module is built.</p>
          </div>
          <div className="welcome-stat"><span>01</span><small>FOUNDATION<br />MODULE</small></div>
        </div>
      ) : (
        <div className="empty-state">
          <div className="empty-icon">{item.icon}</div>
          <span className="eyebrow">COMING IN A FUTURE MODULE</span>
          <h2>{item.label} tools are not built yet</h2>
          <p>This page is a navigation placeholder. No business operations are available here yet.</p>
          <Link className="back-link" to="/">← Back to dashboard</Link>
        </div>
      )}
      <div className="foundation-note"><span>i</span><p>Foundation build · Authentication, roles, and audit logging are active. Inventory and sales actions are not yet available.</p></div>
    </section>
  );
}

function roleLabel(role) {
  return role === "SUPER_ADMIN" ? "Super Admin" : "Admin";
}

function pageDescription(path) {
  const descriptions = {
    "/": "A clear view of what is happening across your workshop.",
    "/products": "Manage your product catalogue and revision history.",
    "/work-orders": "Plan, assign, and follow production work.",
    "/inventory": "Keep raw materials and stock movements in view.",
    "/sales": "Follow finished units from shipment to payment.",
    "/backup": "Protect your data and manage recovery points.",
    "/users": "Manage Admin accounts and user access.",
  };
  return descriptions[path];
}

export default App;
