export const FALLBACK_COLOURS = Object.freeze({
  labor: "#d64545",
  liberal: "#255aa8",
  national: "#0b7a43",
  greens: "#2e8b57",
  independent: "#747f8f",
  default: "#697787"
});

export function partyKey(row) {
  return String(row?.party_id || row?.party_name || "independent").trim() || "independent";
}

export function partyLabel(row) {
  return String(row?.party_name || "Independent / ungrouped");
}

export function partyColour(row) {
  const supplied = String(row?.party_colour || row?.colour_hex || "").trim();
  if (/^#[0-9a-f]{6}$/i.test(supplied)) return supplied;
  const name = String(row?.party_name || "").toLowerCase();
  if (name.includes("labor")) return FALLBACK_COLOURS.labor;
  if (name.includes("liberal")) return FALLBACK_COLOURS.liberal;
  if (name.includes("national")) return FALLBACK_COLOURS.national;
  if (name.includes("green")) return FALLBACK_COLOURS.greens;
  if (name.includes("independent") || !row?.party_id) return FALLBACK_COLOURS.independent;
  return FALLBACK_COLOURS.default;
}
