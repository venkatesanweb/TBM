import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

const NotificationContext = createContext(null);
const notificationEvent = "tamirabot:notification";
export const LOW_STOCK_REMINDER_INTERVAL_MS = 48 * 60 * 60 * 1000;
export const TOAST_DURATION_MS = 10000;

export function lowStockClock() {
  return Date.now();
}

export function lowStockStorageKey(userId) {
  return `tamirabot.lowstock.${userId}`;
}

export function readLowStockState(userId) {
  if (userId === null || userId === undefined) return {};
  try {
    const raw = localStorage.getItem(lowStockStorageKey(userId));
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(Object.entries(parsed).filter(([, entry]) => (
      entry
      && typeof entry === "object"
      && Number.isFinite(Date.parse(entry.episodeStartedAt))
      && Number.isFinite(Date.parse(entry.lastPopupAt))
      && (entry.acknowledgedAt === null || Number.isFinite(Date.parse(entry.acknowledgedAt)))
    )));
  } catch {
    return {};
  }
}

export function writeLowStockState(userId, state) {
  if (userId === null || userId === undefined) return;
  try {
    localStorage.setItem(lowStockStorageKey(userId), JSON.stringify(state));
  } catch {
    // Storage may be unavailable or full; keep the current session usable.
  }
}

function lowStockStatus(state) {
  if (state.acknowledgedAt) return "Accepted";
  return Date.parse(state.lastPopupAt) > Date.parse(state.episodeStartedAt)
    ? "Reminded"
    : "New";
}

function normalizedLowStockItem(item) {
  return {
    id: item.component.id,
    name: item.component.name,
    sku: item.component.sku,
    available: item.available,
    minimum: item.component.reorder_level,
    unit: item.component.unit,
  };
}

export function evaluateLowStockState(items, existingState, complete, now = lowStockClock()) {
  const timestamp = new Date(now).toISOString();
  const state = Object.fromEntries(
    Object.entries(existingState).map(([id, entry]) => [id, { ...entry }]),
  );
  const lowItems = items.filter((item) => item.low_stock);
  const lowIds = new Set(lowItems.map((item) => String(item.component.id)));
  const dueItems = [];

  lowItems.forEach((item) => {
    const id = String(item.component.id);
    const current = state[id];
    if (!current) {
      state[id] = {
        episodeStartedAt: timestamp,
        lastPopupAt: timestamp,
        acknowledgedAt: null,
      };
      dueItems.push(normalizedLowStockItem(item));
    } else if (
      !current.acknowledgedAt
      && now - Date.parse(current.lastPopupAt) >= LOW_STOCK_REMINDER_INTERVAL_MS
    ) {
      state[id] = { ...current, lastPopupAt: timestamp };
      dueItems.push(normalizedLowStockItem(item));
    }
  });

  if (complete) {
    Object.keys(state).forEach((id) => {
      if (!lowIds.has(id)) delete state[id];
    });
  } else {
    items.filter((item) => !item.low_stock).forEach((item) => {
      delete state[String(item.component.id)];
    });
  }
  return { state, dueItems };
}

export function lowStockPopupMessage(dueItems) {
  if (dueItems.length !== 1) return `${dueItems.length} components are low on stock`;
  const item = dueItems[0];
  return `Low stock: ${item.name} (${item.sku}) has ${item.available} ${item.unit} available; minimum level ${item.minimum}.`;
}

function plainText(value) {
  return String(value || "")
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 240);
}

export async function getOrderShortageRows(order, token, fallbackMessage = "") {
  if (!order?.bom_components?.length) return [];
  const shortages = String(order.blocked_reason || fallbackMessage)
    .matchAll(/short by\s+([\d.]+)\s+of component\s+([A-Za-z0-9_-]+)/gi);
  const shortageBySku = new Map([...shortages].map((match) => [match[2], match[1]]));
  if (!shortageBySku.size) return [];
  const inventory = await apiRequest("/api/inventory/", token);
  return order.bom_components
    .filter((line) => shortageBySku.has(line.sku))
    .map((line) => {
      const stock = inventory.find((item) => item.component.sku === line.sku);
      return {
        name: line.name,
        sku: line.sku,
        required: line.total_required,
        available: stock?.available ?? "0",
        shortBy: shortageBySku.get(line.sku),
      };
    });
}

export function successMessage(action, values = {}) {
  const {
    name = "Item",
    sku,
    quantity,
    onHand,
    available,
    from,
    to,
    serialStart,
    serialEnd,
    status,
  } = values;
  const label = sku ? `${name} (${sku})` : name;
  const messages = {
    stockReceived: `${quantity} received for ${label}. On hand: ${onHand}; available: ${available}.`,
    adjustmentRequested: `Stock adjustment of ${quantity} requested for ${label}; counts remain unchanged pending approval.`,
    adjustmentApproved: `Stock adjustment of ${quantity} approved for ${label}.`,
    adjustmentRejected: `Stock adjustment of ${quantity} rejected for ${label}.`,
    workOrderCreated: `Work order ${name} created and assigned to ${to}${status === "BLOCKED" ? "; blocked by unavailable stock" : " and reserved"}.`,
    workOrderAssigned: `${name} assigned to ${to}.`,
    workOrderReassigned: `${name} reassigned from ${from} to ${to}.`,
    workOrderReduced: `${name} quantity reduced to ${quantity}; excess stock reservation released.`,
    workOrderCancelled: `${name} cancelled; remaining reserved stock released.`,
    workOrderStarted: `${name} started.`,
    workOrderPacked: `${quantity} units packed for ${name}${serialStart ? ` (${serialStart}${serialEnd && serialEnd !== serialStart ? `–${serialEnd}` : ""})` : ""}.`,
    workOrderCompleted: `${name} completed.`,
    blockedOrdersRetried: `${quantity} blocked work orders retried; ${to} are now reserved.`,
    workOrdersReserved: `${quantity} blocked work order${quantity === 1 ? "" : "s"} now reserved after stock was received: ${name}.`,
    labelReprinted: `Label reprinted for ${name}.`,
    productCreated: `Product ${name} created.`,
    productUpdated: `Product ${name} updated.`,
    productDeactivated: `Product ${name} deactivated.`,
    productDeleted: `Product ${name} deleted.`,
    componentCreated: `Component ${label} created.`,
    componentUpdated: `Component ${label} updated.`,
    componentDeactivated: `Component ${label} deactivated.`,
    revisionCreated: `Revision ${quantity} created for ${name}.`,
    userCreated: `Admin account created for ${name}.`,
    userUpdated: `User ${name} updated.`,
    userDeactivated: `User ${name} deactivated.`,
    dashboardAccessUpdated: `Dashboard access for ${name}: ${values.changes || "no access changes"}.`,
    dashboardReportExported: `${name} downloaded as ${values.format || "file"}.`,
    bomAdded: `${name} added to the BOM at ${quantity} per product.`,
    bomUpdated: `${name} BOM quantity updated to ${quantity} per product.`,
    bomRemoved: `${name} removed from the BOM.`,
  };
  return messages[action] || `${name} updated.`;
}

export function newlyReservedWorkOrders(before, after) {
  if (!before || !after) return [];
  const previous = new Map(before.map((order) => [order.id, order]));
  return after.filter((order) =>
    previous.get(order.id)?.status === "BLOCKED" && order.status === "RESERVED");
}

export function notifyApiError(message, stockProblem = false) {
  window.dispatchEvent(new CustomEvent(notificationEvent, {
    detail: { type: "error", message, stockProblem },
  }));
}

export async function apiRequest(path, token, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        ...(options.body && !(options.body instanceof FormData)
          ? { "Content-Type": "application/json" }
          : {}),
        ...(token ? { Authorization: `Token ${token}` } : {}),
        ...options.headers,
      },
    });
  } catch {
    const message = "Network connection failed. Check your internet and try again.";
    notifyApiError(message);
    throw new Error(message);
  }

  if (!response.ok) {
    let body = {};
    try {
      body = await response.json();
    } catch {
      body = {};
    }
    const collect = (value, messages) => {
      if (Array.isArray(value)) value.forEach((item) => collect(item, messages));
      else if (value && typeof value === "object") {
        Object.values(value).forEach((item) => collect(item, messages));
      } else if (value) {
        messages.push(plainText(value));
      }
    };
    const messages = [];
    collect(body, messages);
    const detail = plainText(messages.join(" ")) || `The server could not complete the request (${response.status}).`;
    const stockProblem = /insufficient|stock.{0,30}negative|short by|not enough stock|reservation.{0,20}failed/i.test(detail);
    notifyApiError(
      response.status >= 500
        ? "The server could not complete the request. Please try again."
        : detail,
      stockProblem,
    );
    const error = new Error(detail);
    error.responseBody = body;
    error.status = response.status;
    throw error;
  }
  if (response.status === 204) return null;
  return response.json();
}

function Toast({ toast, onDismiss, onAccept, onAction }) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [documentVisible, setDocumentVisible] = useState(() => document.visibilityState !== "hidden");
  const remaining = useRef(TOAST_DURATION_MS);
  const startedAt = useRef(null);
  const previousTimerKey = useRef(toast.timerKey);
  const paused = hovered || focused || !documentVisible;

  useEffect(() => {
    const handleVisibilityChange = () => {
      setDocumentVisible(document.visibilityState !== "hidden");
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => document.removeEventListener("visibilitychange", handleVisibilityChange);
  }, []);

  useEffect(() => {
    if (previousTimerKey.current !== toast.timerKey) {
      previousTimerKey.current = toast.timerKey;
      remaining.current = TOAST_DURATION_MS;
    }
    if (paused) return undefined;
    startedAt.current = lowStockClock();
    const timer = window.setTimeout(onDismiss, remaining.current);
    return () => {
      window.clearTimeout(timer);
      if (startedAt.current !== null) {
        remaining.current = Math.max(0, remaining.current - (lowStockClock() - startedAt.current));
        startedAt.current = null;
      }
    };
  }, [onDismiss, paused, toast.timerKey]);
  return (
    <div
      className={`notification-toast notification-${toast.type}`}
      data-paused={paused}
      role={toast.type === "warning" || toast.type === "error" ? "alert" : undefined}
      aria-live={toast.type === "success" || toast.type === "info" ? "polite" : undefined}
      onFocus={() => setFocused(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false);
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          onDismiss();
        }
      }}
    >
      <span>{toast.message}</span>
      {toast.action && (
        <button className="notification-action" type="button" onClick={onAction}>
          {toast.action.label}
        </button>
      )}
      {toast.lowStockIds?.length === 1 && (
        <button className="notification-action" type="button" onClick={onAccept}>Accept</button>
      )}
      {toast.lowStockIds?.length > 1 && (
        <a className="notification-action" href="/inventory">View list</a>
      )}
      {toast.count > 1 && <span className="notification-count" aria-label={`${toast.count} occurrences`}>×{toast.count}</span>}
      <button type="button" onClick={onDismiss} aria-label="Dismiss notification">×</button>
      <span
        aria-hidden="true"
        className="notification-progress"
        key={toast.timerKey}
        style={{ animationDuration: `${TOAST_DURATION_MS}ms` }}
      />
    </div>
  );
}

export function RecentNotificationsList() {
  const { recentNotifications } = useNotify();
  if (!recentNotifications.length) {
    return <p className="inventory-empty">No recent notifications.</p>;
  }
  return (
    <ol className="recent-notifications-list">
      {recentNotifications.map((notification) => (
        <li className={`recent-notification-${notification.type}`} key={notification.id}>
          <span>{notification.message}</span>
          <time dateTime={notification.time}>{new Date(notification.time).toLocaleTimeString()}</time>
        </li>
      ))}
    </ol>
  );
}

export function LowStockAlertList({ compact = false }) {
  const { lowStockAlerts, acceptLowStock } = useNotify();
  if (!lowStockAlerts.length) {
    return <p className="inventory-empty">No components are below their reorder level.</p>;
  }
  return (
    <div className={`low-stock-alert-list${compact ? " compact" : ""}`}>
      {lowStockAlerts.map((item) => (
        <div className="low-stock-alert-item" key={item.id}>
          <a href={`/inventory/${item.id}`}>
            <strong>{item.name}</strong>
            <small>{item.sku}</small>
          </a>
          <span className="low-stock-alert-quantity">
            {item.available} {item.unit} available · minimum {item.minimum}
          </span>
          <span className={`low-stock-alert-status ${item.status.toLowerCase()}`}>{item.status}</span>
          {item.status !== "Accepted" && (
            <button type="button" className="catalog-text-link" onClick={() => acceptLowStock(item.id)}>
              Accept
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function StockProblemDialog({ problem, onClose }) {
  const dialogRef = useRef(null);
  const closeRef = useRef(null);
  useEffect(() => {
    const previouslyFocused = document.activeElement;
    closeRef.current?.focus();
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        onClose();
      } else if (event.key === "Tab") {
        const controls = dialogRef.current?.querySelectorAll(
          'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        );
        if (!controls?.length) return;
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="stock-dialog-backdrop">
      <section
        aria-labelledby="stock-problem-title"
        aria-modal="true"
        className="stock-problem-dialog"
        ref={dialogRef}
        role="dialog"
      >
        <div className="stock-dialog-heading">
          <div><span className="eyebrow">STOCK UNAVAILABLE</span><h2 id="stock-problem-title">This action needs more stock</h2></div>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="Close stock message">×</button>
        </div>
        <p>{problem.message || "The requested action cannot be completed with the available stock."}</p>
        <div className="stock-dialog-table-wrap">
          <table>
            <thead><tr><th>Component</th><th>SKU</th><th>Required</th><th>Available</th><th>Short by</th></tr></thead>
            <tbody>{problem.rows.length ? problem.rows.map((row, index) => (
                <tr key={`${row.sku}-${index}`}>
                  <td>{row.name || "Component"}</td><td>{row.sku || "—"}</td>
                  <td>{row.required ?? "—"}</td><td>{row.available ?? "—"}</td><td>{row.shortBy ?? "—"}</td>
                </tr>
              )) : <tr><td colSpan="5">Shortage details were not included in this response.</td></tr>}</tbody>
          </table>
        </div>
        <button className="catalog-primary-button" onClick={onClose} type="button">Close</button>
      </section>
    </div>
  );
}

export function NotificationProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const [recentNotifications, setRecentNotifications] = useState([]);
  const [stockProblem, setStockProblem] = useState(null);
  const [lowStockUser, setLowStockUserState] = useState(null);
  const [lowStockState, setLowStockState] = useState({});
  const [lowStockItems, setLowStockItems] = useState({});
  const activeLowStockUser = useRef(null);
  const notify = useCallback((message, type = "info", action = null, duplicateKey = null) => {
    const text = plainText(message);
    if (!text) return;
    const now = lowStockClock();
    const timerKey = `${now}-${Math.random()}`;
    setRecentNotifications((current) => [{
      id: timerKey,
      message: text,
      type,
      time: new Date(now).toISOString(),
    }, ...current].slice(0, 20));
    setToasts((current) => {
      const duplicate = current.find((toast) => toast.type === type
        && (duplicateKey
          ? toast.duplicateKey === duplicateKey
          : toast.message === text)
        && now - toast.createdAt <= 3000);
      if (duplicate) {
        return [{
          ...duplicate,
          message: text,
          count: duplicate.count + 1,
          createdAt: now,
          timerKey,
          action: action || duplicate.action,
          duplicateKey: duplicateKey || duplicate.duplicateKey,
        }, ...current.filter((item) => item.id !== duplicate.id)].slice(0, 4);
      }
      return [{ id: timerKey, timerKey, type, message: text, count: 1, createdAt: now, action, duplicateKey }, ...current].slice(0, 4);
    });
  }, []);
  const closeStockProblem = useCallback(() => setStockProblem(null), []);
  const stockDialog = useCallback((message, rows = []) => {
    setStockProblem({ message: plainText(message), rows });
  }, []);
  const changed = useCallback((action, values) => {
    notify(successMessage(action, values), "success");
  }, [notify]);
  const setLowStockUser = useCallback((userId) => {
    const activeUserId = userId ?? null;
    activeLowStockUser.current = activeUserId === null ? null : String(activeUserId);
    setLowStockUserState(activeUserId);
    setLowStockState(readLowStockState(userId));
    setLowStockItems({});
    setToasts((current) => current.filter((toast) => (
      !toast.lowStockUser || toast.lowStockUser === activeUserId
    )));
  }, []);
  const evaluateLowStock = useCallback((userId, items, complete = false) => {
    if (
      userId === null
      || userId === undefined
      || activeLowStockUser.current !== String(userId)
    ) return;
    const now = lowStockClock();
    const { state, dueItems } = evaluateLowStockState(
      items,
      readLowStockState(userId),
      complete,
      now,
    );
    writeLowStockState(userId, state);
    setLowStockState(state);
    setLowStockUserState(userId);
    setLowStockItems((current) => {
      const next = complete ? {} : { ...current };
      items.forEach((item) => {
        const id = String(item.component.id);
        if (item.low_stock && state[id]) next[id] = normalizedLowStockItem(item);
        else delete next[id];
      });
      return next;
    });

    if (dueItems.length) {
      const message = lowStockPopupMessage(dueItems);
      const timerKey = `${now}-${Math.random()}`;
      setRecentNotifications((current) => [{
        id: timerKey,
        message,
        type: "warning",
        time: new Date(now).toISOString(),
      }, ...current].slice(0, 20));
      setToasts((current) => [{
        id: timerKey,
        timerKey,
        type: "warning",
        message,
        count: 1,
        createdAt: now,
        lowStockUser: userId,
        lowStockIds: dueItems.map((item) => item.id),
      }, ...current].slice(0, 4));
    }
  }, []);
  const acceptLowStock = useCallback((componentId) => {
    if (lowStockUser === null) return;
    const state = readLowStockState(lowStockUser);
    const entry = state[String(componentId)];
    if (!entry || entry.acknowledgedAt) return;
    state[String(componentId)] = {
      ...entry,
      acknowledgedAt: new Date(lowStockClock()).toISOString(),
    };
    writeLowStockState(lowStockUser, state);
    setLowStockState(state);
    setToasts((current) => current.filter((toast) => (
      !(toast.lowStockUser === lowStockUser && toast.lowStockIds?.includes(componentId))
    )));
  }, [lowStockUser]);
  const lowStockAlerts = useMemo(() => Object.entries(lowStockItems)
    .filter(([id]) => lowStockState[id])
    .map(([id, item]) => ({ ...item, id, status: lowStockStatus(lowStockState[id]) }))
    .sort((left, right) => left.name.localeCompare(right.name)), [lowStockItems, lowStockState]);
  const quietAlertCount = lowStockAlerts.filter((item) => item.status === "Accepted").length;

  useEffect(() => {
    const handleNotification = (event) => {
      const { type, message, stockProblem: isStockProblem } = event.detail;
      notify(message, type);
      if (isStockProblem) {
        stockDialog(message, []);
      }
    };
    window.addEventListener(notificationEvent, handleNotification);
    return () => window.removeEventListener(notificationEvent, handleNotification);
  }, [notify, stockDialog]);

  const value = useMemo(() => ({
    notify,
    changed,
    stockDialog,
    setLowStockUser,
    evaluateLowStock,
    acceptLowStock,
    lowStockAlerts,
    quietAlertCount,
    recentNotifications,
  }), [
    notify,
    changed,
    stockDialog,
    setLowStockUser,
    evaluateLowStock,
    acceptLowStock,
    lowStockAlerts,
    quietAlertCount,
    recentNotifications,
  ]);
  return (
    <NotificationContext.Provider value={value}>
      {children}
      <div className="notification-stack" aria-label="Notifications">
        {toasts.map((toast) => (
          <Toast
            key={toast.id}
            toast={toast}
            onDismiss={() => setToasts((current) => current.filter((item) => item.id !== toast.id))}
            onAccept={() => acceptLowStock(toast.lowStockIds[0])}
            onAction={() => {
              toast.action?.onClick?.();
              setToasts((current) => current.filter((item) => item.id !== toast.id));
            }}
          />
        ))}
      </div>
      {stockProblem && <StockProblemDialog problem={stockProblem} onClose={closeStockProblem} />}
    </NotificationContext.Provider>
  );
}

export function useNotify() {
  const value = useContext(NotificationContext);
  if (!value) throw new Error("useNotify must be used inside NotificationProvider.");
  return value;
}

export function useLiveNotifications(token, user) {
  const { notify } = useNotify();
  useEffect(() => {
    if (!token || !user) return undefined;
    let active = true;
    let polling = false;
    let timer;
    let cursor = null;
    let failures = 0;

    const schedule = (delay) => {
      window.clearTimeout(timer);
      if (active && document.visibilityState === "visible") {
        timer = window.setTimeout(poll, delay);
      }
    };

    const poll = async () => {
      if (!active || document.visibilityState !== "visible") return;
      if (polling) {
        schedule(1000);
        return;
      }
      polling = true;
      try {
        const query = cursor === null ? "" : `?after=${encodeURIComponent(cursor)}`;
        const response = await apiRequest(`/api/notifications/${query}`, token);
        if (!active) return;
        if (cursor === null) {
          cursor = response.cursor;
        } else {
          const events = response.results || [];
          if (events.length > 3) {
            notify(`${events.length} updates from other users.`, "info");
          } else {
            events.forEach((event) => notify(event.message, "info"));
          }
          events.forEach((event) => {
            if (event.object?.type === "component") {
              window.dispatchEvent(new CustomEvent("tamirabot:stock-changed", {
                detail: { componentId: event.object.id },
              }));
            }
          });
          cursor = response.cursor;
        }
        failures = 0;
        schedule(15000);
      } catch {
        if (!active) return;
        failures += 1;
        schedule(Math.min(15000 * (2 ** Math.min(failures, 3)), 120000));
      } finally {
        polling = false;
      }
    };

    const onVisibilityChange = () => {
      window.clearTimeout(timer);
      if (document.visibilityState === "visible") {
        schedule(cursor === null ? 0 : 15000);
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    if (document.visibilityState === "visible") schedule(0);
    return () => {
      active = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [notify, token, user]);
}
