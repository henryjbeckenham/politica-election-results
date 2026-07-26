import {clear, element, publicPath, safeReleasePart} from "./dom.511e4b4e.js";

export function renderSourcePanel(root, options) {
  const evidence = root.querySelector("#pr-evidence-list");
  const downloads = root.querySelector("#pr-downloads");
  clear(evidence);
  clear(downloads);
  const catalogue = options.catalogue || {};
  const visualisations = options.visualisations || {};
  const release = catalogue.release || {};
  const composition = catalogue.supplemental_contracts?.parliamentary_composition || {};
  const boundary = visualisations.boundary_geometry || {};
  const derivedBoundary = boundary.derived_geometry || {};
  const boundarySource = boundary.source || {};
  const electionId = options.electionId || catalogue.default_election_id;
  const election = (catalogue.elections || []).find((item) => item.election_id === electionId);
  const electionYear = String(election?.election_date || "").slice(0, 4) || "Selected";
  const facts = [
    ["Release ID", release.release_id],
    ["Database SHA-256", release.database_sha256],
    ["Release manifest SHA-256", release.release_manifest_sha256],
    ["Application / schema", `${release.application_version || "1.8.0"} / ${release.schema_version || "0.2.0"}`],
    ["Feed contract", catalogue.feed_version || "1.8.0"],
    ["Visualisation contract", `${visualisations.contract_version || "2.0.0"} · ${safeReleasePart(visualisations.contract_sha256, 18)}`],
    ["House boundary dataset", `${boundary.feature_count || "—"} divisions · ${boundary.effective_as_at || "Unavailable"}`],
    ["Boundary contract SHA-256", safeReleasePart(boundary.contract_sha256, 28)],
    ["Boundary GeoJSON SHA-256", safeReleasePart(derivedBoundary.sha256, 28)]
  ];
  if (electionId === "election_fed_2025_05_03_general") {
    facts.splice(6, 0,
      ["Senate composition snapshot", `${composition.snapshot_as_at || "Unavailable"} · ${composition.seat_count || "—"} seats`],
      ["Composition contract SHA-256", safeReleasePart(composition.contract_sha256, 28)]
    );
  }
  for (const [label, value] of facts) {
    const item = element("div", "pr-evidence-item");
    item.append(element("span", "", label), element("code", "", value || "Unavailable"));
    evidence.append(item);
  }
  for (const feed of catalogue.feeds || []) {
    const link = element("a", "pr-download");
    link.href = publicPath(feed.feed_id, "csv", electionId, options.apiBase, options.staticBase);
    link.setAttribute("download", "");
    link.append(element("span", "", feed.title), element("span", "", "Download CSV"));
    downloads.append(link);
  }
  const countLink = element("a", "pr-download");
  countLink.href = publicPath("senate_count_progress", "json", electionId, options.apiBase, options.staticBase);
  countLink.target = "_blank";
  countLink.rel = "noopener";
  countLink.append(element("span", "", "Senate count progression"), element("span", "", "Open JSON"));
  downloads.append(countLink);

  if (derivedBoundary.public_asset_path) {
    const geometryLink = element("a", "pr-download");
    geometryLink.href = options.staticBase
      ? `${options.staticBase}/${derivedBoundary.public_asset_path}`
      : `/results/data/${derivedBoundary.public_asset_path}`;
    geometryLink.setAttribute("download", "");
    geometryLink.append(element("span", "", `${electionYear} House electorate boundaries`), element("span", "", "Download GeoJSON"));
    downloads.append(geometryLink);
  }
  const attributionLink = element("a", "pr-download");
  attributionLink.href = options.staticBase
    ? `${options.staticBase}/boundaries/AEC_BOUNDARY_ATTRIBUTION.txt`
    : "/results/data/boundaries/AEC_BOUNDARY_ATTRIBUTION.txt";
  attributionLink.setAttribute("download", "");
  attributionLink.append(element("span", "", "AEC boundary attribution"), element("span", "", "Download notice"));
  downloads.append(attributionLink);
  if (boundarySource.landing_page_url) {
    const sourceLink = element("a", "pr-download");
    sourceLink.href = boundarySource.landing_page_url;
    sourceLink.target = "_blank";
    sourceLink.rel = "noopener";
    sourceLink.append(element("span", "", "Australian Electoral Commission boundaries"), element("span", "", "Open source"));
    downloads.append(sourceLink);
  }
}
