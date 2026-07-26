import {clear, element} from "./dom.js";
import {partyColour, partyKey, partyLabel} from "./party.js";

export function renderPartyLegend(root, rows, options = {}) {
  clear(root);
  root.setAttribute("role", "list");
  root.setAttribute("aria-label", options.label || "Party legend and filter");
  for (const row of rows) {
    const key = partyKey(row);
    const count = Number(row.declared_seats ?? row.count ?? 0);
    const item = element("span", "pr-legend-item");
    item.setAttribute("role", "listitem");
    const button = element("button", "pr-legend-button");
    button.type = "button";
    button.dataset.party = key;
    button.setAttribute("aria-pressed", String(options.selected === key));
    const swatch = element("span", "pr-swatch");
    swatch.style.background = partyColour(row);
    swatch.setAttribute("aria-hidden", "true");
    button.append(swatch, document.createTextNode(`${partyLabel(row)} ${count}`));
    button.addEventListener("click", () => options.onSelect?.(options.selected === key ? "ALL" : key));
    item.append(button);
    root.append(item);
  }
}
