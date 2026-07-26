const STATE_VALUES = new Set(["ALL", "ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"]);
const SENATE_VALUES = new Set([...STATE_VALUES].filter((value) => value !== "ALL"));
const CHAMBERS = new Set(["ALL", "chamber_house", "chamber_senate"]);
const MAP_VIEWS = new Set(["ALL", "ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA", "SYDNEY", "MELBOURNE", "BRISBANE", "ADELAIDE", "PERTH", "HOBART", "CANBERRA", "DARWIN"]);

function cleanText(value, maximum = 120) {
  return String(value || "").trim().slice(0, maximum);
}

export function parseUrlState(url, contract = {}) {
  const source = url instanceof URL ? url : new URL(String(url), "http://localhost/");
  const availableRoutes = new Set((contract.routes || []).filter((route) => route.status === "available").map((route) => route.route_id));
  const defaultRoute = availableRoutes.has(contract.default_route) ? contract.default_route : "overview";
  const requestedRoute = cleanText(source.searchParams.get("view"), 40);
  const requestedState = cleanText(source.searchParams.get("state"), 8).toUpperCase();
  const requestedSenate = cleanText(source.searchParams.get("senate_state"), 8).toUpperCase();
  const requestedChamber = cleanText(source.searchParams.get("chamber"), 30);
  const requestedMapView = cleanText(source.searchParams.get("map_view"), 30).toUpperCase();
  return {
    election: cleanText(source.searchParams.get("election"), 200),
    view: availableRoutes.has(requestedRoute) ? requestedRoute : defaultRoute,
    state: STATE_VALUES.has(requestedState) ? requestedState : "ALL",
    party: cleanText(source.searchParams.get("party")) || "ALL",
    electorate: cleanText(source.searchParams.get("electorate")),
    member: cleanText(source.searchParams.get("member")),
    search: cleanText(source.searchParams.get("q")),
    chamber: CHAMBERS.has(requestedChamber) ? requestedChamber : "ALL",
    senateState: SENATE_VALUES.has(requestedSenate) ? requestedSenate : "NSW",
    mapView: MAP_VIEWS.has(requestedMapView)
      ? requestedMapView
      : (STATE_VALUES.has(requestedState) ? requestedState : "ALL")
  };
}

export function serialiseUrlState(currentUrl, state) {
  const url = currentUrl instanceof URL ? new URL(currentUrl) : new URL(String(currentUrl), "http://localhost/");
  for (const key of ["election", "view", "state", "party", "electorate", "member", "q", "chamber", "senate_state", "map_view"]) url.searchParams.delete(key);
  if (state.election) url.searchParams.set("election", state.election);
  if (state.view && state.view !== "overview") url.searchParams.set("view", state.view);
  if (state.state && state.state !== "ALL") url.searchParams.set("state", state.state);
  if (state.party && state.party !== "ALL") url.searchParams.set("party", state.party);
  if (state.electorate) url.searchParams.set("electorate", state.electorate);
  if (state.member) url.searchParams.set("member", state.member);
  if (state.search) url.searchParams.set("q", state.search);
  if (state.chamber && state.chamber !== "ALL") url.searchParams.set("chamber", state.chamber);
  if (state.senateState && state.senateState !== "NSW") url.searchParams.set("senate_state", state.senateState);
  if (state.mapView && state.mapView !== "ALL") url.searchParams.set("map_view", state.mapView);
  return url;
}

export function createUrlStateStore(contract, environment = globalThis) {
  const location = environment.location;
  const history = environment.history;
  const listeners = new Set();
  let state = parseUrlState(location?.href || "http://localhost/", contract);
  const publish = () => listeners.forEach((listener) => listener({...state}));
  const write = (mode = "replaceState") => {
    if (!history || !location) return;
    const url = serialiseUrlState(location.href, state);
    history[mode]?.({}, "", `${url.pathname}${url.search}${url.hash}`);
  };
  const onPopState = () => {
    state = parseUrlState(location.href, contract);
    publish();
  };
  environment.addEventListener?.("popstate", onPopState);
  return Object.freeze({
    get: () => ({...state}),
    update: (patch, options = {}) => {
      state = {...state, ...patch};
      write(options.push ? "pushState" : "replaceState");
      publish();
      return {...state};
    },
    reset: () => {
      state = parseUrlState("http://localhost/", contract);
      write();
      publish();
      return {...state};
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    destroy: () => environment.removeEventListener?.("popstate", onPopState)
  });
}
