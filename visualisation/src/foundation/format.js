export const STATES = Object.freeze(["ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"]);

const STATE_ALIASES = Object.freeze({
  ACT: "ACT", "AUSTRALIAN CAPITAL TERRITORY": "ACT",
  NSW: "NSW", "NEW SOUTH WALES": "NSW",
  NT: "NT", "NORTHERN TERRITORY": "NT",
  QLD: "QLD", QUEENSLAND: "QLD",
  SA: "SA", "SOUTH AUSTRALIA": "SA",
  TAS: "TAS", TASMANIA: "TAS",
  VIC: "VIC", VICTORIA: "VIC",
  WA: "WA", "WESTERN AUSTRALIA": "WA"
});

export function normaliseState(value) {
  const state = String(value || "").trim().toUpperCase();
  return STATE_ALIASES[state] || state;
}

export function stateForRow(row) {
  for (const value of [row?.state, row?.contest_name, row?.reporting_unit]) {
    const state = normaliseState(value);
    if (STATES.includes(state)) return state;
  }
  return normaliseState(row?.state);
}

export function formatNumber(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? new Intl.NumberFormat("en-AU").format(number) : String(value);
}

export function formatPercent(value, digits = 1) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? `${number.toFixed(digits)}%` : "—";
}

export function aggregateParticipation(rows, state, chamber) {
  const percentages = rows.filter((row) => {
    if (!["turnout_percentage", "informality_percentage"].includes(row.measure_type)) return false;
    if (chamber !== "ALL" && row.chamber_id !== chamber) return false;
    return state === "ALL" || stateForRow(row) === state;
  });
  const groups = new Map();
  for (const row of percentages) {
    const value = Number(row.decimal_value);
    if (!Number.isFinite(value)) continue;
    const place = state === "ALL" ? stateForRow(row) || row.reporting_unit || "National" : row.contest_name || row.reporting_unit || stateForRow(row);
    const key = [place, row.chamber_id, row.measure_type].join("|");
    const current = groups.get(key) || {place, chamber: row.chamber_id, measure: row.measure_type, total: 0, count: 0};
    current.total += value;
    current.count += 1;
    groups.set(key, current);
  }
  return [...groups.values()]
    .map((item) => ({...item, value: item.total / item.count}))
    .sort((a, b) => a.place.localeCompare(b.place) || a.chamber.localeCompare(b.chamber) || a.measure.localeCompare(b.measure))
    .slice(0, state === "ALL" ? 32 : 80);
}
