export function element(tag, className = "", text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

export function clear(node) {
  while (node?.firstChild) node.removeChild(node.firstChild);
}

export function setText(root, selector, value) {
  const node = root.querySelector(selector);
  if (node) node.textContent = value === undefined || value === null ? "—" : String(value);
}

export function safeReleasePart(value, length = 18) {
  const text = String(value || "");
  return text.length > length ? `${text.slice(0, length)}…` : text || "Unavailable";
}

export function apiBaseFromLocation(locationObject = globalThis.location) {
  if (!locationObject) return "";
  const parameters = new URLSearchParams(locationObject.search || "");
  const override = parameters.get("api");
  if (override && /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(override)) {
    return override.replace(/\/$/, "");
  }
  if (["127.0.0.1", "localhost"].includes(locationObject.hostname) && locationObject.port && locationObject.port !== "8765") {
    return "http://127.0.0.1:8765";
  }
  return "";
}

export function publicPath(feedId, format, electionId, apiBase = "", staticBase = "") {
  if (staticBase) {
    const suffix = format === "manifest" ? ".manifest.json" : `.${format}`;
    const election = electionId ? `${encodeURIComponent(electionId)}/` : "";
    return `${staticBase.replace(/\/$/, "")}/feeds/${election}${feedId}${suffix}`;
  }
  const suffix = format === "manifest" ? "/manifest.json" : `.${format}`;
  const path = `${apiBase}/api/public/v1/feeds/${feedId}${suffix}`;
  if (!electionId) return path;
  return `${path}?election_id=${encodeURIComponent(electionId)}`;
}

export function contractPath(apiBase = "", staticBase = "", electionId = "") {
  if (staticBase) {
    const suffix = electionId ? `/${encodeURIComponent(electionId)}.json` : ".json";
    return `${staticBase.replace(/\/$/, "")}/visualisations${suffix}`;
  }
  const path = `${apiBase}/api/public/v1/visualisations`;
  return electionId ? `${path}?election_id=${encodeURIComponent(electionId)}` : path;
}

export async function fetchJson(fetchImpl, url) {
  const response = await fetchImpl(url, {headers: {Accept: "application/json"}});
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`.trim();
    try {
      const body = await response.json();
      if (body?.detail) message = body.detail;
    } catch {
      // Keep the HTTP status when the response is not JSON.
    }
    throw new Error(message);
  }
  return response.json();
}
