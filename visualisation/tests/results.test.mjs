import assert from "node:assert/strict";
import test from "node:test";
import {JSDOM} from "jsdom";

import {
  createResultsApp,
  filterHouseSeats,
  formatNumber,
  normaliseState,
  candidateRowsForResult,
  seatChangeLabel,
  stateForRow
} from "../src/components/results.js";
import {createVisualisationRegistry} from "../src/foundation/registry.js";
import {parseUrlState, serialiseUrlState} from "../src/foundation/url-state.js";
import {compositionSummary, semicircleLayout} from "../src/foundation/chamber.js";
import {
  geometryIntersectsBounds,
  geometryPath,
  geometryProjectedBounds,
  indexElectorateSeats,
  MAP_VIEWS,
  MAX_MAP_ZOOM,
  normaliseElectorateName
} from "../src/foundation/map.js";
import {closestContestRows, partyGainLossRows, stateComparisonRows, tcpSwingRows, voteSeatRows} from "../src/foundation/analytics.js";
import {
  senateDelegationSummary,
  senateGroupQuotaRows,
  senateMilestones,
  senateMovementRows,
  senateRoundSnapshot,
  senateRounds
} from "../src/foundation/senate.js";

const electionId = "election_fed_2025_05_03_general";
const boundaries = {
  type: "FeatureCollection",
  features: [
    {type: "Feature", properties: {electorate: "Farrer", area_sq_km: 126000}, geometry: {type: "Polygon", coordinates: [[[150.9, -34.0], [151.2, -34.0], [151.2, -33.7], [150.9, -33.7], [150.9, -34.0]]]}},
    {type: "Feature", properties: {electorate: "Test", area_sq_km: 1200}, geometry: {type: "Polygon", coordinates: [[[144, -38], [146, -38], [146, -36], [144, -36], [144, -38]]]}}
  ]
};
const feeds = {
  house_seat_results: [
    {contest_id: "farrer", contest_name: "Farrer", state: "nsw", person_name: "Sussan Ley", party_id: "party_liberal", party_name: "Liberal", party_colour: "#255aa8", winning_margin_percentage_points: 12.4, tcp_vote_share: 56.2, tcp_swing: -1.2, seat_change_type: "retained", seat_change_label: "Retained", enrolment: 120000, formal_votes: 107000, informal_votes: 4600, votes_counted: 111600, turnout_percentage: 93.0, informality_percentage: 4.1, counted_percentage_of_enrolment: 93.0},
    {contest_id: "test", contest_name: "Test", state: "VIC", person_name: "Example Member", party_id: "party_labor", party_name: "Labor", party_colour: "#d64545", winning_margin_percentage_points: 4.2, tcp_vote_share: 52.1, tcp_swing: 3.8, seat_change_type: "gained", defeated_incumbent_party_name: "Liberal", seat_change_label: "Gained from Liberal", turnout_percentage: 91.2}
  ],
  house_party_summary: [
    {party_id: "party_labor", party_name: "Labor", party_colour: "#d64545", first_preference_votes: 5_000_000, first_preference_vote_share: 34.5, declared_seats: 1},
    {party_id: "party_liberal", party_name: "Liberal", party_colour: "#255aa8", first_preference_votes: 4_500_000, first_preference_vote_share: 31.2, declared_seats: 1}
  ],
  house_candidate_results: [
    {contest_id: "farrer", result_type: "first_preference", subject_name: "Sussan Ley", party_name: "Liberal", party_colour: "#255aa8", votes: 52000, vote_share: 48.6, swing: -2.1},
    {contest_id: "farrer", result_type: "first_preference", subject_name: "Other Candidate", party_name: "Labor", party_colour: "#d64545", votes: 39000, vote_share: 36.4, swing: 1.4},
    {contest_id: "farrer", result_type: "tcp", subject_name: "Sussan Ley", party_name: "Liberal", party_colour: "#255aa8", votes: 60000, vote_share: 56.2},
    {contest_id: "farrer", result_type: "tcp", subject_name: "Other Candidate", party_name: "Labor", party_colour: "#d64545", votes: 47000, vote_share: 43.8},
    {contest_id: "farrer", result_type: "tpp", subject_name: "Coalition", party_name: "Coalition", party_colour: "#255aa8", votes: 60000, vote_share: 56.2},
    {contest_id: "farrer", result_type: "tpp", subject_name: "Labor", party_name: "Labor", party_colour: "#d64545", votes: 47000, vote_share: 43.8}
  ],
  senate_group_results: [
    {state: null, contest_name: "New South Wales", reporting_level: "contest", subject_name: "Group A", party_name: "Labor", party_colour: "#d64545", votes: 1000000, vote_share: 35.1}
  ],
  turnout_informality: [
    {state: "nsw", chamber_id: "chamber_house", contest_name: "Farrer", measure_type: "turnout_percentage", decimal_value: 92.4},
    {state: "nsw", chamber_id: "chamber_house", contest_name: "Farrer", measure_type: "informality_percentage", decimal_value: 4.1}
  ],
  declared_members: [
    {state: "NSW", chamber_id: "chamber_house", contest_id: "farrer", person_name: "Sussan Ley", party_name: "Liberal"},
    {state: null, contest_name: "New South Wales", chamber_id: "chamber_senate", contest_id: "senate_nsw", person_name: "Example Senator", party_name: "Labor", elected_order: 1}
  ],
  senate_composition: [
    {snapshot_id: "parliament_48_senate_2026_05_14", snapshot_as_at: "2026-05-14", chamber_id: "chamber_senate", person_id: "person_example_senator", person_name: "Example Senator", state: "NSW", party_id: "party_labor", party_name: "Australian Labor Party", party_colour: "#d64545", bloc: "government", term_expiry: "2031-06-30"},
    {snapshot_id: "parliament_48_senate_2026_05_14", snapshot_as_at: "2026-05-14", chamber_id: "chamber_senate", person_id: "person_other_senator", person_name: "Other Senator", state: "VIC", party_id: "party_liberal", party_name: "Liberal Party of Australia", party_colour: "#255aa8", bloc: "opposition", term_expiry: "2028-06-30"}
  ],
  senate_count_progress: [
    {state: "NSW", contest_id: "senate_nsw", round_number: 1, round_label: "Count 1", action_type: "first_preferences", quota_value: 100000, candidacy_id: "candidate_a", candidate_name: "Candidate A", party_name: "Labor", party_colour: "#d64545", progressive_total: 90000, candidate_count_status: "continuing"},
    {state: "NSW", contest_id: "senate_nsw", round_number: 1, round_label: "Count 1", action_type: "first_preferences", quota_value: 100000, candidacy_id: "candidate_b", candidate_name: "Candidate B", party_name: "Liberal", party_colour: "#255aa8", progressive_total: 60000, candidate_count_status: "continuing"},
    {state: "NSW", contest_id: "senate_nsw", round_number: 2, round_label: "Count 2", action_type: "exclusion", quota_value: 100000, candidacy_id: "candidate_a", candidate_name: "Candidate A", party_name: "Labor", party_colour: "#d64545", progressive_total: 110000, candidate_count_status: "elected"},
    {state: "NSW", contest_id: "senate_nsw", round_number: 2, round_label: "Count 2", action_type: "exclusion", quota_value: 100000, candidacy_id: "candidate_b", candidate_name: "Candidate B", party_name: "Liberal", party_colour: "#255aa8", progressive_total: 0, candidate_count_status: "excluded"}
  ],
  senate_count_movements: [
    {state: "NSW", contest_id: "senate_nsw", round_number: 2, action_type: "exclusion", to_candidacy_id: "candidate_a", to_candidate_name: "Candidate A", to_party_name: "Labor", to_party_colour: "#d64545", votes_value: 20000, exhausted: false},
    {state: "NSW", contest_id: "senate_nsw", round_number: 2, action_type: "exclusion", to_candidacy_id: "candidate_b", to_candidate_name: "Candidate B", to_party_name: "Liberal", to_party_colour: "#255aa8", votes_value: -60000, exhausted: false},
    {state: "NSW", contest_id: "senate_nsw", round_number: 2, action_type: "exclusion", votes_value: 500, exhausted: true}
  ]
};

const visualisationContract = {
  contract_version: "1.5.0",
  design_system_version: "1.5.0",
  default_route: "overview",
  read_only: true,
  contract_sha256: "c".repeat(64),
  boundary_geometry: {
    boundary_id: "aec_federal_electoral_boundaries_2025_03_04",
    feature_count: 2,
    effective_as_at: "2025-03-04",
    contract_sha256: "e".repeat(64),
    source: {landing_page_url: "https://www.aec.gov.au/Electorates/gis/gis_datadownload.htm"},
    derived_geometry: {public_asset_path: "boundaries/house_electorates_2025.geojson", sha256: "f".repeat(64)}
  },
  routes: ["overview", "house", "senate", "analytics", "sources"].map((route_id) => ({route_id, title: route_id, status: "available"})),
  visualisations: [
    ["house_composition", "overview"],
    ["house_party_result", "overview"],
    ["house_electorate_results", "house"],
    ["electorate_map", "house"],
    ["senate_group_result", "senate"],
    ["senate_declared_outcomes", "senate"],
    ["senate_composition", "senate"],
    ["senate_member_chamber", "senate"],
    ["senate_state_delegations", "senate"],
    ["senate_count_animation", "senate"],
    ["senate_quota_progress", "senate"],
    ["senate_transfer_movements", "senate"],
    ["senate_candidate_milestones", "senate"],
    ["senate_elected_timeline", "senate"],
    ["house_member_chamber", "overview"],
    ["participation", "overview"],
    ["source_evidence", "sources"],
    ["electoral_pendulum", "analytics"],
    ["closest_contests", "analytics"],
    ["house_swing_analysis", "analytics"],
    ["party_gains_losses", "analytics"],
    ["vote_seat_comparison", "analytics"],
    ["state_comparison", "analytics"],
    ["vote_type_comparison", "analytics"]
  ].map(([visualisation_id, route_id]) => ({visualisation_id, route_id, status: "available", required_feeds: []}))
};

test("helpers normalise historical states and filter seats safely", () => {
  assert.equal(normaliseState("nsw"), "NSW");
  assert.equal(normaliseState("New South Wales"), "NSW");
  assert.equal(normaliseState("Australian Capital Territory"), "ACT");
  assert.equal(stateForRow({state: null, contest_name: "Western Australia"}), "WA");
  assert.equal(formatNumber(1234567), "1,234,567");
  assert.deepEqual(filterHouseSeats(feeds.house_seat_results, "NSW", "farrer").map((row) => row.contest_id), ["farrer"]);
  assert.deepEqual(filterHouseSeats(feeds.house_seat_results, "ALL", "' OR 1=1 --"), []);
  assert.equal(seatChangeLabel(feeds.house_seat_results[0]), "Retained");
  assert.equal(seatChangeLabel(feeds.house_seat_results[1]), "Gained from Liberal");
  assert.equal(candidateRowsForResult(feeds.house_candidate_results, "farrer", "tpp").length, 2);
});

test("visualisation registry and URL state reject unknown routes and preserve safe filters", () => {
  const registry = createVisualisationRegistry(visualisationContract);
  assert.equal(registry.get("house_composition").route_id, "overview");
  assert.equal(registry.list({route: "senate"}).length, 10);
  const parsed = parseUrlState("https://example.test/?view=house&state=nsw&party=party_labor&q=Farrer&map_view=SYDNEY", visualisationContract);
  assert.deepEqual(parsed, {
    election: "", view: "house", state: "NSW", party: "party_labor", electorate: "", member: "", search: "Farrer", chamber: "ALL", senateState: "NSW", mapView: "SYDNEY"
  });
  const serialised = serialiseUrlState("https://example.test/", parsed);
  assert.equal(serialised.searchParams.get("view"), "house");
  assert.equal(serialised.searchParams.get("state"), "NSW");
  assert.equal(serialised.searchParams.get("q"), "Farrer");
  assert.equal(serialised.searchParams.get("map_view"), "SYDNEY");
});

test("semicircle layout assigns one unique governed position to every member", () => {
  const rows = Array.from({length: 150}, (_, index) => ({
    person_name: `Member ${index + 1}`,
    party_id: index < 80 ? "party_labor" : "party_liberal",
    party_name: index < 80 ? "Labor" : "Liberal"
  }));
  const layout = semicircleLayout(rows, {rows: 6});
  assert.equal(layout.length, 150);
  assert.equal(new Set(layout.map((row) => `${row.x.toFixed(6)}|${row.y.toFixed(6)}`)).size, 150);
  assert.deepEqual(compositionSummary(feeds.senate_composition).map((row) => row.count), [1, 1]);
});

test("Senate helpers reconcile delegations, quotas, rounds, milestones and movements", () => {
  assert.deepEqual(senateRounds(feeds.senate_count_progress, "NSW"), [1, 2]);
  const snapshot = senateRoundSnapshot(feeds.senate_count_progress, "NSW", 2);
  assert.equal(snapshot[0].candidate_name, "Candidate A");
  assert.equal(snapshot[0].change, 20000);
  assert.equal(senateMovementRows(feeds.senate_count_movements, "NSW", 2)[0].candidateName, "Candidate B");
  assert.deepEqual(senateMilestones(feeds.senate_count_progress, "NSW").map((row) => row.type), ["elected", "excluded"]);
  assert.equal(senateDelegationSummary(feeds.senate_composition).find((row) => row.state === "NSW").members.length, 1);
  assert.equal(senateGroupQuotaRows(feeds.senate_group_results, 100000)[0].quotaMultiple, 10);
});

test("governed map helpers match electorate identities and create finite SVG paths", () => {
  assert.equal(normaliseElectorateName("Eden–Monaro"), normaliseElectorateName("Eden-Monaro"));
  assert.equal(indexElectorateSeats(feeds.house_seat_results).get("farrer").person_name, "Sussan Ley");
  const path = geometryPath(boundaries.features[0].geometry, [112, -44, 154, -9]);
  assert.match(path, /^M/);
  assert.doesNotMatch(path, /NaN|Infinity/);
  assert.equal(MAP_VIEWS.SYDNEY.state, "NSW");
  assert.equal(MAP_VIEWS.MELBOURNE.group, "Capital-city close-ups");
  assert.equal(geometryIntersectsBounds(boundaries.features[0].geometry, MAP_VIEWS.SYDNEY.bounds), true);
  assert.equal(geometryIntersectsBounds(boundaries.features[1].geometry, MAP_VIEWS.SYDNEY.bounds), false);
  assert.equal(MAX_MAP_ZOOM, 40);
  const projected = geometryProjectedBounds(boundaries.features[0].geometry, MAP_VIEWS.SYDNEY.bounds);
  assert.ok(projected.width > 0);
  assert.ok(projected.centreX >= 0 && projected.centreX <= 1000);
});

test("House analytical helpers rank margins, swings, movements and representation deterministically", () => {
  assert.deepEqual(closestContestRows(feeds.house_seat_results, 2).map((row) => row.contest_name), ["Test", "Farrer"]);
  assert.deepEqual(tcpSwingRows(feeds.house_seat_results, 2).map((row) => row.contest_name), ["Test", "Farrer"]);
  const movement = partyGainLossRows(feeds.house_seat_results);
  assert.equal(movement.find((row) => row.partyName === "Labor").gains, 1);
  assert.equal(movement.find((row) => row.partyName === "Liberal").losses, 1);
  assert.deepEqual(stateComparisonRows(feeds.house_seat_results).map((row) => row.state), ["NSW", "VIC"]);
  const comparison = voteSeatRows(feeds.house_party_summary, feeds.house_seat_results, feeds.house_candidate_results);
  assert.equal(comparison.reduce((sum, row) => sum + row.seats, 0), 2);
});

test("public results runtime renders feeds, filters seats and exposes downloads", async () => {
  const dom = new JSDOM("<!doctype html><div id='mount'></div>", {url: "http://127.0.0.1:8765/results/"});
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.location = dom.window.location;
  globalThis.Event = dom.window.Event;
  globalThis.KeyboardEvent = dom.window.KeyboardEvent;

  const requests = [];
  const fetchImpl = async (url) => {
    requests.push(String(url));
    if (String(url).endsWith("/api/public/v1/feeds")) {
      return {
        ok: true,
        json: async () => ({
          api_version: "v1",
          feed_version: "1.5.0",
          default_election_id: electionId,
          release: {release_id: "release_test", database_sha256: "a".repeat(64), application_version: "1.5.0", schema_version: "0.2.0"},
          supplemental_contracts: {parliamentary_composition: {snapshot_as_at: "2026-05-14", seat_count: 76, contract_sha256: "d".repeat(64)}},
          elections: [{election_id: electionId, election_name: "2025 Australian federal election", election_date: "2025-05-03"}],
          feeds: Object.keys(feeds).map((feed_id) => ({feed_id, title: feed_id.replaceAll("_", " ")}))
        })
      };
    }
    if (String(url).startsWith("/api/public/v1/visualisations")) {
      return {ok: true, json: async () => ({...visualisationContract, release: {release_id: "release_test", database_sha256: "a".repeat(64)}})};
    }
    if (String(url) === "/results/data/boundaries/house_electorates_2025.geojson") {
      return {ok: true, json: async () => boundaries};
    }
    const match = String(url).match(/feeds\/([a-z_]+)\.json/);
    if (match && feeds[match[1]]) return {ok: true, json: async () => ({manifest: {}, data: feeds[match[1]]})};
    return {ok: false, status: 404, statusText: "Not Found", json: async () => ({detail: "missing fixture"})};
  };

  const app = createResultsApp(document.querySelector("#mount"), {fetchImpl, apiBase: ""});
  await app.ready;
  assert.equal(app.dataset.ready, "true");
  assert.equal(document.querySelector("#pr-metric-seats").textContent, "2");
  assert.equal(document.querySelectorAll("#pr-seat-cards .pr-result-card").length, 2);
  assert.equal(document.querySelectorAll("#pr-house-map .pr-map-electorate").length, 2);
  assert.match(document.querySelector("#pr-map-count").textContent, /2 electorates available/);
  assert.equal(document.querySelectorAll("#pr-map-state option").length, Object.keys(MAP_VIEWS).length);
  assert.equal(document.querySelectorAll("#pr-house-map .pr-map-tool").length, 4);
  assert.equal(document.querySelectorAll("#pr-map-insets .pr-map-inset").length, 8);
  assert.equal(document.querySelectorAll("#pr-map-insets svg").length, 8);
  assert.equal(document.querySelectorAll("#pr-map-electorate-focus option").length, 3);
  const slider = document.querySelector("#pr-house-map .pr-map-zoom-slider");
  assert.equal(slider.max, "4000");
  const originalViewBox = document.querySelector("#pr-house-map svg").getAttribute("viewBox");
  document.querySelector('#pr-house-map [aria-label="Zoom in"]').dispatchEvent(new Event("click", {bubbles: true}));
  assert.notEqual(document.querySelector("#pr-house-map svg").getAttribute("viewBox"), originalViewBox);
  slider.value = "4000";
  slider.dispatchEvent(new Event("input", {bubbles: true}));
  assert.match(document.querySelector("#pr-house-map svg").getAttribute("viewBox"), /25\.00 17\.50$/);
  assert.ok(document.querySelectorAll("#pr-house-map .pr-map-electorate-label.is-visible").length >= 1);
  document.querySelector("#pr-house-map .pr-map-reset").dispatchEvent(new Event("click", {bubbles: true}));
  assert.equal(document.querySelector("#pr-house-map svg").getAttribute("viewBox"), "0.00 0.00 1000.00 700.00");
  const electorateFinder = document.querySelector("#pr-map-electorate-focus");
  electorateFinder.value = "Farrer";
  electorateFinder.dispatchEvent(new Event("change", {bubbles: true}));
  assert.equal(document.querySelector("#pr-map-state").value, "NSW");
  assert.equal(document.querySelector("#pr-map-electorate-focus").value, "Farrer");
  assert.ok(Number(document.querySelector("#pr-house-map svg").getAttribute("viewBox").split(" ")[2]) < 1000);
  assert.match(document.querySelector("#pr-map-detail").textContent, /Sussan Ley/);
  const mapView = document.querySelector("#pr-map-state");
  mapView.value = "SYDNEY";
  mapView.dispatchEvent(new Event("change", {bubbles: true}));
  assert.equal(document.querySelectorAll("#pr-house-map .pr-map-electorate").length, 1);
  assert.match(document.querySelector("#pr-map-count").textContent, /Sydney metropolitan area/);
  assert.equal(dom.window.location.search.includes("map_view=SYDNEY"), true);
  assert.equal(document.querySelectorAll("#pr-senate-tabs button").length, 8);
  assert.doesNotMatch(document.querySelector("#pr-senate-chart").textContent, /No NSW/);
  assert.match(document.querySelector("#pr-senate-chart").textContent, /Labor/);
  assert.match(document.querySelector("#pr-senate-members").textContent, /Example Senator/);
  assert.equal(document.querySelectorAll("#pr-senate-chamber button").length, 2);
  assert.match(document.querySelector("#pr-senate-snapshot").textContent, /2026-05-14/);
  assert.equal(document.querySelectorAll("#pr-senate-delegations .pr-delegation-card").length, 8);
  document.querySelector('[data-view="senate"]').dispatchEvent(new Event("click", {bubbles: true}));
  await app.resultsState.senateLoadPromise;
  assert.equal(document.querySelector('[data-view="senate"]').getAttribute("aria-current"), "page");
  assert.match(document.querySelector("#pr-count-round-badge").textContent, /Count 2/);
  assert.equal(document.querySelectorAll("#pr-count-candidates-chart .pr-candidate-row").length, 2);
  assert.equal(document.querySelectorAll("#pr-count-movements .pr-transfer-row").length, 3);
  assert.match(document.querySelector("#pr-count-milestones").textContent, /Candidate A/);
  document.querySelector("#pr-count-previous").dispatchEvent(new Event("click", {bubbles: true}));
  assert.match(document.querySelector("#pr-count-round-badge").textContent, /Count 1/);
  assert.match(document.querySelector("#pr-evidence-list").textContent, /release_test/);
  assert.ok(document.querySelectorAll("#pr-downloads a").length >= 7);
  assert.ok(requests.every((url) => url.includes("/api/public/v1/") || url.startsWith("/results/data/")));
  assert.equal(document.querySelectorAll("#pr-seat-legend button").length, 2);
  assert.equal(document.querySelector(".pr-tooltip").getAttribute("role"), "tooltip");
  assert.equal(document.querySelector('[data-view="senate"]').getAttribute("aria-current"), "page");
  document.querySelector('[data-view="analytics"]').dispatchEvent(new Event("click", {bubbles: true}));
  assert.equal(document.querySelector('[data-view="analytics"]').getAttribute("aria-current"), "page");
  assert.equal(document.querySelectorAll("#pr-margin-spectrum .pr-pendulum-seat").length, 2);
  assert.match(document.querySelector("#pr-swing-ranking").textContent, /Test/);
  assert.match(document.querySelector("#pr-vote-seat").textContent, /Labor/);

  const search = document.querySelector("#pr-house-search");
  search.value = "Farrer";
  search.dispatchEvent(new Event("input", {bubbles: true}));
  assert.equal(document.querySelectorAll("#pr-seat-cards .pr-result-card").length, 1);
  document.querySelector("#pr-seat-cards .pr-result-card").dispatchEvent(new Event("click", {bubbles: true}));
  assert.match(document.querySelector("#pr-seat-detail").textContent, /Sussan Ley/);
  assert.match(document.querySelector("#pr-seat-detail").textContent, /Two-candidate preferred/);
  assert.match(document.querySelector("#pr-seat-detail").textContent, /Two-party preferred/);
  assert.match(document.querySelector("#pr-seat-detail").textContent, /Count metadata/);
  assert.equal(document.querySelector("#pr-seat-detail details").open, false);
  assert.equal(dom.window.location.search.includes("electorate=Farrer"), true);

  dom.window.close();
});

test("static publication runtime reads only packaged relative files", async () => {
  const dom = new JSDOM("<!doctype html><div id='mount'></div>", {url: "https://results.example.test/"});
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.location = dom.window.location;
  globalThis.Event = dom.window.Event;
  globalThis.KeyboardEvent = dom.window.KeyboardEvent;

  const requests = [];
  const fetchImpl = async (url) => {
    requests.push(String(url));
    if (String(url) === "./data/catalogue.json") {
      return {
        ok: true,
        json: async () => ({
          static_publication: true,
          feed_version: "1.5.0",
          default_election_id: electionId,
          release: {release_id: "release_static", database_sha256: "b".repeat(64), application_version: "1.5.0", schema_version: "0.2.0"},
          supplemental_contracts: {parliamentary_composition: {snapshot_as_at: "2026-05-14", seat_count: 76, contract_sha256: "d".repeat(64)}},
          elections: [{election_id: electionId, election_name: "2025 Australian federal election", election_date: "2025-05-03"}],
          feeds: Object.keys(feeds).map((feed_id) => ({feed_id, title: feed_id.replaceAll("_", " ")}))
        })
      };
    }
    if (String(url) === `./data/visualisations/${electionId}.json`) {
      return {ok: true, json: async () => ({...visualisationContract, static_publication: true, release: {release_id: "release_static", database_sha256: "b".repeat(64)}})};
    }
    if (String(url) === "./data/boundaries/house_electorates_2025.geojson") {
      return {ok: true, json: async () => boundaries};
    }
    const match = String(url).match(/^\.\/data\/feeds\/[^/]+\/([a-z_]+)\.json$/);
    if (match && feeds[match[1]]) return {ok: true, json: async () => ({manifest: {}, data: feeds[match[1]]})};
    return {ok: false, status: 404, statusText: "Not Found", json: async () => ({detail: "missing fixture"})};
  };

  const app = createResultsApp(document.querySelector("#mount"), {fetchImpl, staticBase: "./data"});
  await app.ready;
  assert.equal(app.dataset.ready, "true");
  assert.equal(document.querySelector(".pr-verified").textContent, "Verified static release");
  assert.equal(document.querySelector(".pr-brand").getAttribute("href"), "./");
  assert.ok(requests.length >= 7);
  assert.ok(requests.every((url) => url.startsWith("./data/")));
  const downloads = [...document.querySelectorAll("#pr-downloads a")];
  assert.ok(downloads.filter((link) => /Download CSV/.test(link.textContent)).every((link) => link.getAttribute("href").startsWith("./data/feeds/")));
  assert.ok(downloads.some((link) => link.getAttribute("href") === "./data/boundaries/house_electorates_2025.geojson"));

  dom.window.close();
});
