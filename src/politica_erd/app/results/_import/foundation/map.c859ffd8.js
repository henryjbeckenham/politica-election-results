import {clear, element} from "./dom.511e4b4e.js";
import {partyColour, partyKey} from "./party.3576021c.js";
import {normaliseState, stateForRow} from "./format.e2a4a187.js";

const SVG_NS = "http://www.w3.org/2000/svg";
export const MIN_MAP_ZOOM = 1;
export const MAX_MAP_ZOOM = 40;

export const MAP_BOUNDS = Object.freeze({
  ALL: [112.0, -44.2, 154.4, -9.2],
  ACT: [148.72, -36.02, 149.48, -35.08],
  NSW: [140.75, -37.75, 154.35, -27.75],
  NT: [128.7, -26.2, 138.3, -10.3],
  QLD: [137.7, -29.3, 153.75, -9.0],
  SA: [128.8, -38.2, 141.25, -25.7],
  TAS: [143.4, -43.85, 148.6, -39.1],
  VIC: [140.7, -39.35, 150.25, -33.75],
  WA: [112.3, -35.3, 129.25, -13.2]
});

export const MAP_VIEWS = Object.freeze({
  ALL: {label: "Australia", group: "National", state: "", bounds: MAP_BOUNDS.ALL},
  ACT: {label: "Australian Capital Territory", shortLabel: "ACT", group: "States and territories", state: "ACT", bounds: MAP_BOUNDS.ACT},
  NSW: {label: "New South Wales", shortLabel: "NSW", group: "States and territories", state: "NSW", bounds: MAP_BOUNDS.NSW},
  NT: {label: "Northern Territory", shortLabel: "NT", group: "States and territories", state: "NT", bounds: MAP_BOUNDS.NT},
  QLD: {label: "Queensland", shortLabel: "QLD", group: "States and territories", state: "QLD", bounds: MAP_BOUNDS.QLD},
  SA: {label: "South Australia", shortLabel: "SA", group: "States and territories", state: "SA", bounds: MAP_BOUNDS.SA},
  TAS: {label: "Tasmania", shortLabel: "TAS", group: "States and territories", state: "TAS", bounds: MAP_BOUNDS.TAS},
  VIC: {label: "Victoria", shortLabel: "VIC", group: "States and territories", state: "VIC", bounds: MAP_BOUNDS.VIC},
  WA: {label: "Western Australia", shortLabel: "WA", group: "States and territories", state: "WA", bounds: MAP_BOUNDS.WA},
  SYDNEY: {label: "Sydney metropolitan area", shortLabel: "Sydney", group: "Capital-city close-ups", state: "NSW", bounds: [150.55, -34.18, 151.48, -33.42]},
  MELBOURNE: {label: "Melbourne metropolitan area", shortLabel: "Melbourne", group: "Capital-city close-ups", state: "VIC", bounds: [144.42, -38.18, 145.78, -37.42]},
  BRISBANE: {label: "Brisbane metropolitan area", shortLabel: "Brisbane", group: "Capital-city close-ups", state: "QLD", bounds: [152.60, -27.88, 153.48, -27.08]},
  ADELAIDE: {label: "Adelaide metropolitan area", shortLabel: "Adelaide", group: "Capital-city close-ups", state: "SA", bounds: [138.22, -35.27, 139.08, -34.53]},
  PERTH: {label: "Perth metropolitan area", shortLabel: "Perth", group: "Capital-city close-ups", state: "WA", bounds: [115.50, -32.48, 116.30, -31.42]},
  HOBART: {label: "Hobart metropolitan area", shortLabel: "Hobart", group: "Capital-city close-ups", state: "TAS", bounds: [146.96, -43.18, 147.70, -42.50]},
  CANBERRA: {label: "Canberra metropolitan area", shortLabel: "Canberra", group: "Capital-city close-ups", state: "ACT", bounds: [148.70, -36.00, 149.48, -35.02]},
  DARWIN: {label: "Darwin metropolitan area", shortLabel: "Darwin", group: "Capital-city close-ups", state: "NT", bounds: [130.62, -12.78, 131.30, -12.00]}
});

export const MAP_VIEW_VALUES = Object.freeze(Object.keys(MAP_VIEWS));
export const CITY_MAP_VIEW_VALUES = Object.freeze(
  MAP_VIEW_VALUES.filter((value) => MAP_VIEWS[value].group === "Capital-city close-ups")
);

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, Number(value)));
}

export function normaliseElectorateName(value) {
  return String(value || "")
    .normalize("NFKD")
    .replace(/[’‘]/g, "'")
    .replace(/[^a-zA-Z0-9']/g, "")
    .toLowerCase();
}

export function indexElectorateSeats(rows) {
  const index = new Map();
  for (const row of rows || []) {
    const key = normaliseElectorateName(row.contest_name);
    if (!key || index.has(key)) continue;
    index.set(key, row);
  }
  return index;
}

function ringsForGeometry(geometry) {
  if (!geometry) return [];
  if (geometry.type === "Polygon") return geometry.coordinates || [];
  if (geometry.type === "MultiPolygon") {
    return (geometry.coordinates || []).flatMap((polygon) => polygon || []);
  }
  return [];
}

function coordinateBounds(coordinates) {
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  for (const coordinate of coordinates) {
    const longitude = Number(coordinate?.[0]);
    const latitude = Number(coordinate?.[1]);
    if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) continue;
    west = Math.min(west, longitude);
    south = Math.min(south, latitude);
    east = Math.max(east, longitude);
    north = Math.max(north, latitude);
  }
  return [west, south, east, north].every(Number.isFinite)
    ? {west, south, east, north, width: east - west, height: north - south}
    : null;
}

export function geometryIntersectsBounds(geometry, bounds) {
  const geometryBounds = coordinateBounds(ringsForGeometry(geometry).flat());
  if (!geometryBounds) return false;
  const [viewWest, viewSouth, viewEast, viewNorth] = bounds;
  return geometryBounds.east >= viewWest && geometryBounds.west <= viewEast
    && geometryBounds.north >= viewSouth && geometryBounds.south <= viewNorth;
}

function projector(bounds, width, height, padding) {
  const [west, south, east, north] = bounds;
  const midLatitude = (south + north) / 2;
  const longitudeFactor = Math.max(0.2, Math.cos(midLatitude * Math.PI / 180));
  const projectedWest = west * longitudeFactor;
  const projectedEast = east * longitudeFactor;
  const availableWidth = width - 2 * padding;
  const availableHeight = height - 2 * padding;
  const scale = Math.min(
    availableWidth / Math.max(0.000001, projectedEast - projectedWest),
    availableHeight / Math.max(0.000001, north - south)
  );
  const usedWidth = (projectedEast - projectedWest) * scale;
  const usedHeight = (north - south) * scale;
  const offsetX = (width - usedWidth) / 2;
  const offsetY = (height - usedHeight) / 2;
  return ([longitude, latitude]) => [
    offsetX + (longitude * longitudeFactor - projectedWest) * scale,
    offsetY + (north - latitude) * scale
  ];
}

export function geometryPath(geometry, bounds, width = 1000, height = 700, padding = 28) {
  const project = projector(bounds, width, height, padding);
  return ringsForGeometry(geometry).map((ring) => {
    if (!ring?.length) return "";
    const points = ring.map((coordinate) => project(coordinate));
    return `${points.map(([x, y], index) => `${index ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`).join("")}Z`;
  }).join("");
}

export function geometryProjectedBounds(geometry, bounds, width = 1000, height = 700, padding = 28) {
  const project = projector(bounds, width, height, padding);
  const projected = ringsForGeometry(geometry).flat().map(project);
  const result = coordinateBounds(projected);
  if (!result) return null;
  return {
    minX: result.west,
    minY: result.south,
    maxX: result.east,
    maxY: result.north,
    width: result.width,
    height: result.height,
    centreX: (result.west + result.east) / 2,
    centreY: (result.south + result.north) / 2
  };
}

function matchesSearch(seat, search) {
  const query = String(search || "").trim().toLowerCase();
  if (!query) return true;
  return [seat.contest_name, seat.person_name, seat.candidate_name, seat.party_name]
    .some((value) => String(value || "").toLowerCase().includes(query));
}

function recordsForView(features, seats, view) {
  const seatIndex = indexElectorateSeats(seats);
  const selectedState = normaliseState(view.state) || "ALL";
  const city = view.group === "Capital-city close-ups";
  return (features || []).map((feature) => ({
    feature,
    seat: seatIndex.get(normaliseElectorateName(feature?.properties?.electorate))
  })).filter(({seat}) => seat && (
    selectedState === "ALL" || stateForRow(seat) === selectedState
  )).filter(({feature}) => !city || geometryIntersectsBounds(feature.geometry, view.bounds));
}

export function renderMapViewThumbnails(root, options = {}) {
  if (!root) return;
  clear(root);
  const document = root.ownerDocument;
  for (const viewValue of CITY_MAP_VIEW_VALUES) {
    const view = MAP_VIEWS[viewValue];
    const records = recordsForView(options.features || [], options.seats || [], view)
      .sort((a, b) => Number(b.feature?.properties?.area_sq_km || 0) - Number(a.feature?.properties?.area_sq_km || 0));
    const button = element("button", "pr-map-inset");
    button.type = "button";
    button.dataset.mapView = viewValue;
    button.setAttribute("aria-pressed", String(options.selected === viewValue));
    button.setAttribute("aria-label", `Open ${view.label}; ${records.length} intersecting electorates`);
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 160 96");
    svg.setAttribute("aria-hidden", "true");
    for (const {feature, seat} of records) {
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", geometryPath(feature.geometry, view.bounds, 160, 96, 4));
      path.setAttribute("fill", partyColour(seat));
      path.setAttribute("fill-rule", "evenodd");
      svg.append(path);
    }
    const copy = element("span", "pr-map-inset-copy");
    copy.append(
      element("strong", "", view.shortLabel),
      element("span", "", `${records.length} electorates`)
    );
    button.append(svg, copy);
    button.addEventListener("click", () => options.onSelect?.(viewValue));
    root.append(button);
  }
}

export function renderElectorateMap(root, options = {}) {
  clear(root);
  const document = root.ownerDocument;
  const features = options.features || [];
  const seats = options.seats || [];
  if (!features.length || !seats.length) {
    root.append(element("div", "pr-empty", "The governed electorate boundary geometry is unavailable."));
    return {featureCount: 0, matchedCount: 0};
  }

  const selectedView = String(options.view || options.state || "ALL").toUpperCase();
  const view = MAP_VIEWS[selectedView] || MAP_VIEWS.ALL;
  const bounds = view.bounds;
  const records = recordsForView(features, seats, view)
    .map((record) => ({
      ...record,
      projectedBounds: geometryProjectedBounds(record.feature.geometry, bounds)
    }))
    .filter((record) => record.projectedBounds)
    .sort((a, b) => Number(b.feature?.properties?.area_sq_km || 0) - Number(a.feature?.properties?.area_sq_km || 0));

  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "pr-electorate-map");
  svg.setAttribute("viewBox", "0 0 1000 700");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${view.label} House electorate map. Zoom up to forty times, drag or use arrow keys to pan, and select a division to centre it.`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.setAttribute("tabindex", "0");
  const background = document.createElementNS(SVG_NS, "rect");
  background.setAttribute("class", "pr-map-background");
  background.setAttribute("width", "1000");
  background.setAttribute("height", "700");
  svg.append(background);

  const labels = [];
  const recordByName = new Map();
  let suppressClickUntil = 0;
  let zoom = options.initialCamera?.view === selectedView
    ? clamp(options.initialCamera.zoom, MIN_MAP_ZOOM, MAX_MAP_ZOOM)
    : MIN_MAP_ZOOM;
  let centreX = options.initialCamera?.view === selectedView
    ? clamp(options.initialCamera.centreX, 0, 1000)
    : 500;
  let centreY = options.initialCamera?.view === selectedView
    ? clamp(options.initialCamera.centreY, 0, 700)
    : 350;

  for (const record of records) {
    const {feature, seat, projectedBounds} = record;
    recordByName.set(normaliseElectorateName(seat.contest_name), record);
    const path = document.createElementNS(SVG_NS, "path");
    const label = `${seat.contest_name}: ${seat.person_name || seat.candidate_name || "Declared member"}, ${seat.party_name || "Independent"}`;
    path.setAttribute("d", geometryPath(feature.geometry, bounds));
    path.setAttribute("class", "pr-map-electorate");
    path.setAttribute("fill", partyColour(seat));
    path.setAttribute("fill-rule", "evenodd");
    path.setAttribute("role", "button");
    path.setAttribute("tabindex", "0");
    path.setAttribute("aria-label", label);
    path.setAttribute("aria-pressed", String(options.selectedSeat?.contest_id === seat.contest_id));
    path.dataset.electorate = seat.contest_name || "";
    path.dataset.party = partyKey(seat);
    path.dataset.state = stateForRow(seat);
    const matchesParty = !options.party || options.party === "ALL" || partyKey(seat) === options.party;
    if (!matchesParty || !matchesSearch(seat, options.search)) path.classList.add("is-muted");
    if (options.selectedSeat?.contest_id === seat.contest_id) path.classList.add("is-selected");
    const title = document.createElementNS(SVG_NS, "title");
    title.textContent = label;
    path.append(title);
    const select = () => {
      if (Date.now() < suppressClickUntil) return;
      focusRecord(record, {announce: true});
      options.onSelect?.(seat);
    };
    path.addEventListener("click", select);
    path.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        focusRecord(record, {announce: true});
        options.onSelect?.(seat);
      }
    });
    options.tooltip?.attach(path, label);
    svg.append(path);

    const text = document.createElementNS(SVG_NS, "text");
    text.setAttribute("class", "pr-map-electorate-label");
    text.setAttribute("x", projectedBounds.centreX.toFixed(2));
    text.setAttribute("y", projectedBounds.centreY.toFixed(2));
    text.setAttribute("text-anchor", "middle");
    text.setAttribute("dominant-baseline", "middle");
    text.setAttribute("aria-hidden", "true");
    text.textContent = seat.contest_name || "";
    labels.push({text, record});
    svg.append(text);
  }

  const toolbar = element("div", "pr-map-toolbar");
  toolbar.setAttribute("role", "group");
  toolbar.setAttribute("aria-label", "Map zoom controls");
  const back = element("button", "pr-map-tool pr-map-back", "Back to Australia");
  back.type = "button";
  back.hidden = selectedView === "ALL";
  const zoomOut = element("button", "pr-map-tool", "−");
  zoomOut.type = "button";
  zoomOut.setAttribute("aria-label", "Zoom out");
  const slider = document.createElement("input");
  slider.className = "pr-map-zoom-slider";
  slider.type = "range";
  slider.min = String(MIN_MAP_ZOOM * 100);
  slider.max = String(MAX_MAP_ZOOM * 100);
  slider.step = "25";
  slider.setAttribute("aria-label", "Map zoom level");
  const zoomIn = element("button", "pr-map-tool", "+");
  zoomIn.type = "button";
  zoomIn.setAttribute("aria-label", "Zoom in");
  const reset = element("button", "pr-map-tool pr-map-reset", "Reset view");
  reset.type = "button";
  const zoomLabel = element("span", "pr-map-zoom-label", "100%");
  zoomLabel.setAttribute("aria-live", "polite");
  toolbar.append(back, zoomOut, slider, zoomIn, reset, zoomLabel);

  function cameraState() {
    return {view: selectedView, zoom, centreX, centreY};
  }

  function updateLabels() {
    for (const {text, record} of labels) {
      const projectedSize = Math.max(record.projectedBounds.width, record.projectedBounds.height) * zoom;
      const selected = options.selectedSeat?.contest_id === record.seat.contest_id;
      const visible = selected || (zoom >= (view.group === "Capital-city close-ups" ? 1.25 : 2) && projectedSize >= 42);
      text.classList.toggle("is-visible", visible);
      text.style.fontSize = `${clamp(12 / zoom, 0.35, 9)}px`;
      text.style.strokeWidth = `${clamp(3 / zoom, 0.08, 1.4)}px`;
    }
  }

  function applyCamera({announce = false, emit = true} = {}) {
    zoom = clamp(zoom, MIN_MAP_ZOOM, MAX_MAP_ZOOM);
    const width = 1000 / zoom;
    const height = 700 / zoom;
    centreX = clamp(centreX, width / 2, 1000 - width / 2);
    centreY = clamp(centreY, height / 2, 700 - height / 2);
    svg.setAttribute("viewBox", `${(centreX - width / 2).toFixed(2)} ${(centreY - height / 2).toFixed(2)} ${width.toFixed(2)} ${height.toFixed(2)}`);
    slider.value = String(Math.round(zoom * 100));
    zoomLabel.textContent = `${Math.round(zoom * 100)}%`;
    reset.disabled = zoom === MIN_MAP_ZOOM && centreX === 500 && centreY === 350;
    updateLabels();
    if (emit) options.onCameraChange?.(cameraState());
    if (announce) svg.focus({preventScroll: true});
  }

  function mapPointForClient(clientX, clientY) {
    const box = svg.getBoundingClientRect();
    if (!box.width || !box.height) return {x: centreX, y: centreY, fractionX: 0.5, fractionY: 0.5};
    const fractionX = clamp((clientX - box.left) / box.width, 0, 1);
    const fractionY = clamp((clientY - box.top) / box.height, 0, 1);
    const width = 1000 / zoom;
    const height = 700 / zoom;
    return {
      x: centreX - width / 2 + fractionX * width,
      y: centreY - height / 2 + fractionY * height,
      fractionX,
      fractionY
    };
  }

  function zoomToAt(targetZoom, clientX = null, clientY = null) {
    const point = clientX === null || clientY === null
      ? {x: centreX, y: centreY, fractionX: 0.5, fractionY: 0.5}
      : mapPointForClient(clientX, clientY);
    const nextZoom = clamp(targetZoom, MIN_MAP_ZOOM, MAX_MAP_ZOOM);
    const nextWidth = 1000 / nextZoom;
    const nextHeight = 700 / nextZoom;
    centreX = point.x + nextWidth / 2 - point.fractionX * nextWidth;
    centreY = point.y + nextHeight / 2 - point.fractionY * nextHeight;
    zoom = nextZoom;
    applyCamera();
  }

  function zoomBy(factor, clientX = null, clientY = null) {
    zoomToAt(zoom * factor, clientX, clientY);
  }

  function panBy(deltaX, deltaY) {
    centreX += deltaX;
    centreY += deltaY;
    applyCamera();
  }

  function focusRecord(record, {announce = false} = {}) {
    if (!record?.projectedBounds) return false;
    const target = record.projectedBounds;
    const widthZoom = 1000 / Math.max(target.width * 1.65, 5);
    const heightZoom = 700 / Math.max(target.height * 1.65, 5);
    zoom = clamp(Math.min(widthZoom, heightZoom), 1.6, MAX_MAP_ZOOM);
    centreX = target.centreX;
    centreY = target.centreY;
    applyCamera({announce});
    return true;
  }

  function focusSeat(value, options = {}) {
    return focusRecord(recordByName.get(normaliseElectorateName(value)), options);
  }

  function resetCamera() {
    zoom = MIN_MAP_ZOOM;
    centreX = 500;
    centreY = 350;
    applyCamera({announce: true});
  }

  back.addEventListener("click", () => options.onBackToAustralia?.());
  zoomOut.addEventListener("click", () => zoomBy(1 / 1.8));
  zoomIn.addEventListener("click", () => zoomBy(1.8));
  reset.addEventListener("click", resetCamera);
  slider.addEventListener("input", () => zoomToAt(Number(slider.value) / 100));
  svg.addEventListener("wheel", (event) => {
    event.preventDefault();
    const factor = clamp(Math.exp(-Number(event.deltaY || 0) * 0.0025), 0.72, 1.38);
    zoomBy(factor, event.clientX, event.clientY);
  }, {passive: false});
  svg.addEventListener("dblclick", (event) => {
    event.preventDefault();
    zoomBy(event.shiftKey ? 1 / 2 : 2, event.clientX, event.clientY);
  });
  svg.addEventListener("keydown", (event) => {
    const step = 70 / zoom;
    if (["+", "="].includes(event.key)) {
      event.preventDefault();
      zoomBy(1.8);
    } else if (event.key === "-") {
      event.preventDefault();
      zoomBy(1 / 1.8);
    } else if (event.key === "0") {
      event.preventDefault();
      resetCamera();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      panBy(-step, 0);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      panBy(step, 0);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      panBy(0, -step);
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      panBy(0, step);
    }
  });

  const pointers = new Map();
  let lastPointer = null;
  let pinchDistance = null;
  let moved = false;
  svg.addEventListener("pointerdown", (event) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
    lastPointer = {x: event.clientX, y: event.clientY};
    moved = false;
    if (pointers.size === 2) {
      const [first, second] = [...pointers.values()];
      pinchDistance = Math.hypot(second.x - first.x, second.y - first.y);
    }
    svg.classList.add("is-dragging");
    svg.setPointerCapture?.(event.pointerId);
  });
  svg.addEventListener("pointermove", (event) => {
    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
    if (pointers.size >= 2) {
      const [first, second] = [...pointers.values()];
      const distance = Math.hypot(second.x - first.x, second.y - first.y);
      const midpointX = (first.x + second.x) / 2;
      const midpointY = (first.y + second.y) / 2;
      if (pinchDistance && distance > 0) zoomBy(distance / pinchDistance, midpointX, midpointY);
      pinchDistance = distance;
      moved = true;
      suppressClickUntil = Date.now() + 300;
      return;
    }
    if (!lastPointer) return;
    const box = svg.getBoundingClientRect();
    const deltaX = event.clientX - lastPointer.x;
    const deltaY = event.clientY - lastPointer.y;
    if (Math.abs(deltaX) + Math.abs(deltaY) >= 2) moved = true;
    if (box.width && box.height && zoom > 1) {
      centreX -= deltaX * (1000 / zoom) / box.width;
      centreY -= deltaY * (700 / zoom) / box.height;
      applyCamera();
    }
    lastPointer = {x: event.clientX, y: event.clientY};
  });
  const stopPointer = (event) => {
    if (moved) suppressClickUntil = Date.now() + 300;
    pointers.delete(event.pointerId);
    pinchDistance = null;
    lastPointer = pointers.size === 1 ? [...pointers.values()][0] : null;
    if (!pointers.size) svg.classList.remove("is-dragging");
    try {
      svg.releasePointerCapture?.(event.pointerId);
    } catch {
      // Pointer capture may already have been released by the browser.
    }
  };
  svg.addEventListener("pointerup", stopPointer);
  svg.addEventListener("pointercancel", stopPointer);

  applyCamera({emit: false});
  root.append(toolbar, svg);
  if (!options.initialCamera && options.selectedSeat) {
    focusSeat(options.selectedSeat.contest_name);
  }
  return {
    featureCount: features.length,
    matchedCount: records.length,
    view: selectedView,
    getCamera: cameraState,
    zoomBy,
    zoomTo: zoomToAt,
    panBy,
    focusSeat,
    reset: resetCamera
  };
}
