import { AGEING_THRESHOLD_DAYS, formatSaleDate, formatSaleDateTime, formatSaleMoney, saleAgeDays, saleStatusSince } from "./salesFormat.js";

const labels = {
  READY_TO_SHIP: "Ready to Ship",
  DELIVERED: "Delivered",
  PAYMENT_PENDING: "Payment Pending",
  PAYMENT_RECEIVED: "Payment Received",
  RETURNED: "Return",
  IN_STOCK: "In Stock",
  RESERVED_FOR_SALE: "Reserved for sale",
  SOLD: "Sold",
  UNDER_INSPECTION: "Under inspection",
  SCRAPPED: "Scrapped",
};

const stages = ["READY_TO_SHIP", "DELIVERED", "PAYMENT_PENDING", "PAYMENT_RECEIVED"];

export function StatusChip({ status }) {
  const label = labels[status] || status.replaceAll("_", " ");
  const icon = status === "PAYMENT_RECEIVED" || status === "IN_STOCK"
    ? "✓"
    : status === "RETURNED" || status === "SCRAPPED"
      ? "!"
      : "•";
  return <span className={`sales-status-chip sales-status-${status.toLowerCase()}`}><span aria-hidden="true">{icon}</span>{label}</span>;
}

export function SaleProgress({ sale }) {
  const currentIndex = stages.indexOf(sale.status);
  const returned = sale.status === "RETURNED";
  return (
    <div className={`sale-progress${returned ? " is-returned" : ""}`} aria-label={`Sale status: ${labels[sale.status] || sale.status}`}>
      <ol>
        {stages.map((stage, index) => {
          const complete = !returned && currentIndex > index;
          const current = !returned && currentIndex === index;
          return <li className={complete ? "complete" : current ? "current" : ""} key={stage}>
            <span className="sale-progress-marker">{complete ? "✓" : index + 1}</span>
            <span>{labels[stage]}</span>
          </li>;
        })}
      </ol>
      {returned && <div className="sale-progress-returned"><StatusChip status="RETURNED" /></div>}
    </div>
  );
}

export function SaleAge({ sale, now = Date.now() }) {
  const days = saleAgeDays(sale, now);
  const since = saleStatusSince(sale);
  const attention = days >= AGEING_THRESHOLD_DAYS
    && ["DELIVERED", "PAYMENT_PENDING"].includes(sale.status);
  return (
    <span className={`sale-age${attention ? " needs-attention" : ""}`} title={since ? formatSaleDateTime(since) : undefined}>
      {labels[sale.status] || sale.status} {days === 0 ? "today" : `${days} day${days === 1 ? "" : "s"} ago`}
    </span>
  );
}

export { formatSaleDate, formatSaleDateTime, formatSaleMoney, labels as saleStatusLabels };
