import {partyKey} from "./party.js";

const BLOC_ORDER = Object.freeze({government: 0, crossbench: 1, opposition: 2});

export function parliamentaryBloc(row) {
  if (row?.bloc && Object.hasOwn(BLOC_ORDER, row.bloc)) return row.bloc;
  const value = `${row?.party_id || ""} ${row?.party_name || ""}`.toLowerCase();
  if (value.includes("labor")) return "government";
  if (
    value.includes("liberal") ||
    value.includes("national") ||
    value.includes("country_liberal")
  ) return "opposition";
  return "crossbench";
}

export function sortComposition(rows) {
  return [...rows].sort((left, right) => {
    const bloc = BLOC_ORDER[parliamentaryBloc(left)] - BLOC_ORDER[parliamentaryBloc(right)];
    if (bloc) return bloc;
    return partyKey(left).localeCompare(partyKey(right)) ||
      String(left.person_name || left.candidate_name || "").localeCompare(
        String(right.person_name || right.candidate_name || "")
      );
  });
}

function allocateRows(total, rowCount) {
  if (!total) return [];
  const radii = Array.from({length: Math.max(1, rowCount)}, (_, index) => 180 + index * 56);
  const weight = radii.reduce((sum, radius) => sum + radius, 0);
  const raw = radii.map((radius) => total * radius / weight);
  const counts = raw.map(Math.floor);
  let remaining = total - counts.reduce((sum, value) => sum + value, 0);
  const remainderOrder = raw
    .map((value, index) => ({index, fraction: value - Math.floor(value)}))
    .sort((left, right) => right.fraction - left.fraction || right.index - left.index);
  for (let index = 0; index < remaining; index += 1) counts[remainderOrder[index].index] += 1;
  return counts.map((count, index) => ({count, radius: radii[index], row: index + 1}));
}

export function semicircleLayout(rows, options = {}) {
  const members = sortComposition(rows);
  const rowCount = Number(options.rows || (members.length > 100 ? 6 : 5));
  const slots = [];
  for (const ring of allocateRows(members.length, rowCount)) {
    for (let index = 0; index < ring.count; index += 1) {
      const angle = Math.PI - ((index + 0.5) * Math.PI / ring.count);
      slots.push({
        angle,
        row: ring.row,
        x: 500 + ring.radius * Math.cos(angle),
        y: 500 - ring.radius * Math.sin(angle)
      });
    }
  }
  slots.sort((left, right) => right.angle - left.angle || left.row - right.row);
  return members.map((member, index) => ({...member, ...slots[index]}));
}

export function compositionSummary(rows) {
  const summary = new Map();
  for (const row of rows) {
    const key = partyKey(row);
    const current = summary.get(key) || {
      party_id: row.party_id,
      party_name: row.party_name || "Independent / ungrouped",
      party_colour: row.party_colour,
      bloc: parliamentaryBloc(row),
      count: 0
    };
    current.count += 1;
    summary.set(key, current);
  }
  return [...summary.values()].sort(
    (left, right) => BLOC_ORDER[left.bloc] - BLOC_ORDER[right.bloc] ||
      right.count - left.count || left.party_name.localeCompare(right.party_name)
  );
}
