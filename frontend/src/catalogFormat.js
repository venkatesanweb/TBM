const catalogDateTimeFormatter = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "Asia/Kolkata",
});

export function formatCatalogDateTime(value) {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return catalogDateTimeFormatter
    .format(date)
    .replace(",", "")
    .replace(/(\d{2}) ([A-Za-z]{3}) (\d{4})/, "$1-$2-$3");
}
