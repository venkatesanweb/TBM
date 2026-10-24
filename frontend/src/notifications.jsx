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

function Toast({ toast, onDismiss }) {
  const [paused, setPaused] = useState(false);
  const remaining = useRef(toast.duration);
  const startedAt = useRef(Date.now());
  useEffect(() => {
    remaining.current = toast.duration;
    if (toast.duration === 0 || paused) return undefined;
    startedAt.current = Date.now();
    const timer = window.setTimeout(onDismiss, remaining.current);
    return () => {
      window.clearTimeout(timer);
      remaining.current = Math.max(0, remaining.current - (Date.now() - startedAt.current));
    };
  }, [onDismiss, paused, toast.createdAt, toast.duration]);
  return (
    <div
      className={`notification-toast notification-${toast.type}`}
      role={toast.type === "warning" || toast.type === "error" ? "alert" : undefined}
      aria-live={toast.type === "success" || toast.type === "info" ? "polite" : undefined}
      onFocus={() => setPaused(true)}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setPaused(false);
      }}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <span>{toast.message}</span>
      {toast.count > 1 && <span className="notification-count" aria-label={`${toast.count} occurrences`}>×{toast.count}</span>}
      <button type="button" onClick={onDismiss} aria-label="Dismiss notification">×</button>
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
  const [stockProblem, setStockProblem] = useState(null);
  const notify = useCallback((message, type = "info") => {
    const text = plainText(message);
    if (!text) return;
    const now = Date.now();
    setToasts((current) => {
      const duplicate = current.find((toast) => toast.type === type
        && toast.message === text && now - toast.createdAt <= 3000);
      if (duplicate) {
        return [{
          ...duplicate,
          count: duplicate.count + 1,
          createdAt: now,
          duration: type === "warning" || type === "error" ? 0 : type === "success" ? 4000 : 5000,
        }, ...current.filter((item) => item.id !== duplicate.id)].slice(0, 4);
      }
      const duration = type === "success" ? 4000 : type === "info" ? 5000 : 0;
      return [{ id: `${now}-${Math.random()}`, type, message: text, count: 1, createdAt: now, duration }, ...current].slice(0, 4);
    });
  }, []);
  const closeStockProblem = useCallback(() => setStockProblem(null), []);
  const stockDialog = useCallback((message, rows = []) => {
    setStockProblem({ message: plainText(message), rows });
  }, []);
  const changed = useCallback((action, values) => {
    notify(successMessage(action, values), "success");
  }, [notify]);

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

  const value = useMemo(() => ({ notify, changed, stockDialog }), [notify, changed, stockDialog]);
  return (
    <NotificationContext.Provider value={value}>
      {children}
      <div className="notification-stack" aria-label="Notifications">
        {toasts.map((toast) => (
          <Toast
            key={toast.id}
            toast={toast}
            onDismiss={() => setToasts((current) => current.filter((item) => item.id !== toast.id))}
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
