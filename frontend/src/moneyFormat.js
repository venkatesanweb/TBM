const inrFormatter = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export function formatMoney(amount) {
  if (typeof amount !== "string" || !/^-?\d+(?:\.\d{1,2})?$/.test(amount)) {
    return "—";
  }
  return inrFormatter.format(amount);
}
