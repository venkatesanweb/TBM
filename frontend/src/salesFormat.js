export const AGEING_THRESHOLD_DAYS = 7;

export function salesTodayISODate(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${values.year}-${values.month}-${values.day}`;
}

const saleDateFormatter = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  timeZone: "Asia/Kolkata",
});

const saleDateTimeFormatter = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "Asia/Kolkata",
});

const saleMoneyFormatter = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatSaleMoney(amount) {
  const value = Number(amount);
  return Number.isFinite(value) ? saleMoneyFormatter.format(value) : "—";
}

function parseSaleDate(value) {
  if (!value) return null;
  const date = /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? new Date(`${value}T12:00:00+05:30`)
    : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatSaleDate(value) {
  const date = parseSaleDate(value);
  return date ? saleDateFormatter.format(date).replaceAll(" ", "-") : "—";
}

export function formatSaleDateTime(value) {
  const date = parseSaleDate(value);
  return date
    ? saleDateTimeFormatter.format(date).replace(",", "").replace(/(\d{2}) ([A-Za-z]{3}) (\d{4})/, "$1-$2-$3")
    : "—";
}

export function saleStatusSince(sale) {
  const matchingStatus = (sale.status_history || [])
    .filter((entry) => entry.to_status === sale.status)
    .at(-1);
  return matchingStatus?.timestamp || sale.created_at;
}

export function saleAgeDays(sale, now = Date.now()) {
  const timestamp = parseSaleDate(saleStatusSince(sale));
  if (!timestamp) return 0;
  return Math.max(0, Math.floor((now - timestamp.getTime()) / 86400000));
}

export function saleNeedsAttention(sale, now = Date.now()) {
  return ["DELIVERED", "PAYMENT_PENDING"].includes(sale.status)
    && saleAgeDays(sale, now) >= AGEING_THRESHOLD_DAYS;
}
