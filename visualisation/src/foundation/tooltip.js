let tooltipSequence = 0;

export function createTooltip(root = document.body) {
  const tooltip = document.createElement("div");
  tooltip.className = "pr-tooltip";
  tooltip.id = `pr-tooltip-${++tooltipSequence}`;
  tooltip.setAttribute("role", "tooltip");
  tooltip.hidden = true;
  root.append(tooltip);
  let active = null;

  const hide = () => {
    if (active) active.removeAttribute("aria-describedby");
    active = null;
    tooltip.hidden = true;
  };
  const show = (target, text) => {
    active = target;
    target.setAttribute("aria-describedby", tooltip.id);
    tooltip.textContent = String(text);
    tooltip.hidden = false;
    const bounds = target.getBoundingClientRect();
    tooltip.style.left = `${Math.max(8, bounds.left + bounds.width / 2)}px`;
    tooltip.style.top = `${Math.max(8, bounds.top - 8)}px`;
  };
  const attach = (target, text) => {
    const resolve = () => typeof text === "function" ? text(target) : text;
    target.addEventListener("pointerenter", () => show(target, resolve()));
    target.addEventListener("pointerleave", hide);
    target.addEventListener("focus", () => show(target, resolve()));
    target.addEventListener("blur", hide);
    target.addEventListener("keydown", (event) => { if (event.key === "Escape") hide(); });
    return target;
  };
  return Object.freeze({attach, hide, element: tooltip, destroy: () => tooltip.remove()});
}
