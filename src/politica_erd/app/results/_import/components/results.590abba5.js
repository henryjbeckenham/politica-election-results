import {
  apiBaseFromLocation,
  clear,
  contractPath,
  element,
  fetchJson,
  publicPath,
  safeReleasePart,
  setText
} from "../foundation/dom.511e4b4e.js";
import {
  aggregateParticipation,
  formatNumber,
  formatPercent,
  normaliseState,
  STATES,
  stateForRow
} from "../foundation/format.e2a4a187.js";
import {FALLBACK_COLOURS, partyColour, partyKey} from "../foundation/party.3576021c.js";
import {createVisualisationRegistry} from "../foundation/registry.dbb5ddfd.js";
import {renderPartyLegend} from "../foundation/legend.88fe07cd.js";
import {createTooltip} from "../foundation/tooltip.dba578ce.js";
import {createUrlStateStore} from "../foundation/url-state.6b817b5a.js";
import {renderSourcePanel} from "../foundation/source-panel.334f66c8.js";
import {
  compositionSummary,
  parliamentaryBloc,
  semicircleLayout
} from "../foundation/chamber.96c59b93.js";
import {
  MAP_VIEWS,
  renderElectorateMap,
  renderMapViewThumbnails
} from "../foundation/map.c859ffd8.js";
import {
  closestContestRows,
  marginSpectrumRows,
  partyGainLossRows,
  stateComparisonRows,
  tcpSwingRows,
  voteSeatRows,
  voteTypeRows
} from "../foundation/analytics.4fc3156f.js";
import {
  senateDelegationSummary,
  senateGroupQuotaRows,
  senateMilestones,
  senateMovementRows,
  senateRoundSnapshot,
  senateRounds
} from "../foundation/senate.f42848df.js";

export {formatNumber, formatPercent, normaliseState, partyColour, stateForRow};

export const FEED_IDS = Object.freeze([
  "house_candidate_results",
  "house_seat_results",
  "house_party_summary",
  "senate_group_results",
  "turnout_informality",
  "declared_members",
  "senate_count_progress",
  "senate_count_movements",
  "senate_composition"
]);

const SENATE_DETAIL_FEEDS = Object.freeze(["senate_count_progress", "senate_count_movements"]);
const INITIAL_FEEDS = FEED_IDS.filter((feed) => !SENATE_DETAIL_FEEDS.includes(feed));
export function filterHouseSeats(rows, state, search, party = "ALL") {
  const selectedState = normaliseState(state);
  const query = String(search || "").trim().toLowerCase();
  return rows.filter((row) => {
    if (selectedState && selectedState !== "ALL" && stateForRow(row) !== selectedState) {
      return false;
    }
    if (party && party !== "ALL" && partyKey(row) !== party) return false;
    if (!query) return true;
    return [row.contest_name, row.person_name, row.candidate_name, row.party_name, row.state]
      .some((value) => String(value || "").toLowerCase().includes(query));
  });
}

export function seatChangeLabel(row) {
  if (row?.seat_change_label) return row.seat_change_label;
  if (row?.seat_change_type === "retained") return "Retained";
  if (row?.seat_change_type === "gained") {
    return row.defeated_incumbent_party_name
      ? `Gained from ${row.defeated_incumbent_party_name}`
      : "Gained";
  }
  return "New member";
}

export function candidateRowsForResult(rows, contestId, resultType) {
  return rows
    .filter((row) => row.contest_id === contestId && row.result_type === resultType)
    .sort((a, b) => Number(b.votes || 0) - Number(a.votes || 0));
}

function shell(homeHref = "/results/", verificationLabel = "Verified local release") {
  return `
    <a class="pr-skip" href="#results-main">Skip to results</a>
    <div class="pr-app">
      <header class="pr-header">
        <div class="pr-header-inner">
          <a class="pr-brand" href="${homeHref}" aria-label="Politica election results home">
            <span class="pr-mark" aria-hidden="true">P</span>
            <span class="pr-brand-copy"><strong>Politica</strong><span>Election results</span></span>
          </a>
          <nav class="pr-header-nav" aria-label="Results sections">
            <a href="?view=overview" data-view="overview">Overview</a><a href="?view=house" data-view="house">House</a><a href="?view=senate" data-view="senate">Senate</a><a href="?view=analytics" data-view="analytics">Analysis</a><a href="?view=sources" data-view="sources">Sources</a>
          </nav>
          <span class="pr-verified">${verificationLabel}</span>
        </div>
      </header>

      <main class="pr-main" id="results-main" tabindex="-1">
        <div class="pr-status" id="pr-status" role="status">Loading the verified publication feeds…</div>

        <form class="pr-filter-bar" id="pr-global-filters" aria-label="Result filters">
          <div class="pr-control"><label for="pr-global-election">Election</label><select id="pr-global-election"><option value="">Loading elections…</option></select></div>
          <div class="pr-control"><label for="pr-global-state">State</label><select id="pr-global-state"><option value="ALL">All states</option></select></div>
          <div class="pr-control"><label for="pr-global-party">Party</label><select id="pr-global-party"><option value="ALL">All parties</option></select></div>
          <div class="pr-control pr-control-wide"><label for="pr-global-electorate">Electorate or person</label><input id="pr-global-electorate" type="search" placeholder="Search this release"></div>
          <button class="pr-clear-filters" id="pr-clear-filters" type="button">Clear filters</button>
        </form>

        <section class="pr-hero" data-route-section="overview" aria-labelledby="pr-title">
          <div>
            <p class="pr-eyebrow">Australian federal election</p>
            <h1 id="pr-title">Election results, clearly evidenced.</h1>
            <p class="pr-hero-copy">Explore the current governed result release. Every number shown here comes from a fixed, versioned Politica publication feed and can be downloaded with its release evidence.</p>
          </div>
          <aside class="pr-release-card" aria-label="Release identity">
            <span>Current governed release</span>
            <strong id="pr-release-id">Loading…</strong>
            <small id="pr-election-label">Reading election catalogue…</small>
          </aside>
        </section>

        <section class="pr-metrics" data-route-section="overview" aria-label="Election summary">
          <article class="pr-metric"><span>House seats declared</span><strong id="pr-metric-seats">—</strong><small>Current declared outcomes</small></article>
          <article class="pr-metric"><span>Largest House party</span><strong id="pr-metric-leading">—</strong><small id="pr-metric-leading-note">By declared seats</small></article>
          <article class="pr-metric"><span>Senate representation</span><strong id="pr-metric-senators">—</strong><small id="pr-metric-senators-note">Selected election view</small></article>
          <article class="pr-metric"><span>Governed feeds</span><strong id="pr-metric-feeds">9</strong><small>Plus governed AEC boundary geometry</small></article>
        </section>

        <section class="pr-section" id="house" data-route-section="overview house" data-visualisation-id="house_composition" aria-labelledby="house-title">
          <div class="pr-section-heading"><div><p class="pr-eyebrow">House of Representatives</p><h2 id="house-title">National result</h2><p>Declared seats and national first-preference performance, followed by an electorate-level result finder.</p></div></div>
          <div class="pr-composition-layout">
            <article class="pr-card pr-composition-card"><div class="pr-card-head"><h3>House composition</h3><p>Each selectable seat represents one declared electorate. Positions show political grouping, not physical desks.</p></div><div class="pr-card-body"><div class="pr-chamber" id="pr-house-chamber" role="group" aria-label="House of Representatives composition"></div><div class="pr-bloc-labels" aria-hidden="true"><span>Government</span><span>Crossbench</span><span>Opposition</span></div><div class="pr-legend" id="pr-seat-legend"></div></div></article>
            <article class="pr-card" data-visualisation-id="house_member_chamber"><div class="pr-card-head"><h3>Selected House member</h3><p>Choose a seat using a pointer or keyboard.</p></div><div class="pr-card-body pr-composition-detail" id="pr-house-member-detail"><div class="pr-empty">Select a House seat to inspect its member and electorate.</div></div></article>
          </div>
          <article class="pr-card pr-party-result-card" data-visualisation-id="house_party_result"><div class="pr-card-head"><h3>Party result</h3><p>National first-preference vote share and declared seats.</p></div><div class="pr-card-body"><div class="pr-bar-list" id="pr-party-chart"></div></div></article>
        </section>

        <section class="pr-section" id="house-map" data-route-section="house" data-visualisation-id="electorate_map" aria-labelledby="house-map-title">
          <div class="pr-section-heading">
            <div><p class="pr-eyebrow">Geographic result</p><h2 id="house-map-title">Electorate map</h2><p>Every division is coloured by its declared winning party. Choose Australia, a state or a capital-city close-up, then select a division to open its result.</p></div>
            <div class="pr-controls">
              <div class="pr-control"><label for="pr-map-state">Map view</label><select id="pr-map-state"><option value="ALL">Australia</option></select></div>
              <div class="pr-control"><label for="pr-map-electorate-focus">Find electorate</label><select id="pr-map-electorate-focus"><option value="">Choose an electorate</option></select></div>
            </div>
          </div>
          <div class="pr-map-inset-section"><div><strong>Capital-city inset maps</strong><span>Select a miniature map to open its metropolitan electorates.</span></div><div id="pr-map-insets" class="pr-map-insets" aria-label="Capital-city inset maps"></div></div>
          <div class="pr-map-layout">
            <article class="pr-card pr-map-card">
              <div class="pr-card-body"><div id="pr-house-map" class="pr-map-stage" aria-live="polite"></div><div class="pr-table-meta" id="pr-map-count">Loading boundaries…</div><div class="pr-legend" id="pr-map-legend"></div><p class="pr-map-note" id="pr-map-note">Zoom from 100% to 4,000% using the slider, + and −, a mouse wheel, trackpad, double-click or pinch. Drag to pan. Selecting an electorate centres and enlarges it; labels appear as space permits. The map uses the governed AEC boundaries for the selected election.</p></div>
            </article>
            <article class="pr-card"><div class="pr-card-head"><h3>Selected electorate</h3><p>Winner, party, margin and result status.</p></div><div class="pr-card-body pr-composition-detail" id="pr-map-detail"><div class="pr-empty">Select an electorate on the map.</div></div></article>
          </div>
        </section>

        <section class="pr-section" id="house-analysis" data-route-section="analytics" aria-labelledby="house-analysis-title">
          <div class="pr-section-heading">
            <div><p class="pr-eyebrow">House analysis</p><h2 id="house-analysis-title">Margins, movement and representation</h2><p>Compare the selected House result using the current State, Party and search filters. Seat swings shown here are the AEC-reported TCP figures in this release; they are not a recalculation against redistributed historical boundaries.</p></div>
          </div>
          <div class="pr-analysis-summary" id="pr-analysis-summary" aria-live="polite"></div>
          <div class="pr-analytics-grid">
            <article class="pr-card pr-analytics-wide" data-visualisation-id="electoral_pendulum"><div class="pr-card-head"><h3>Electoral pendulum</h3><p>Declared seats from the narrowest to the largest TCP winning margin. Select a seat to open its full result.</p></div><div class="pr-card-body"><div id="pr-margin-spectrum" class="pr-margin-spectrum"></div></div></article>
            <article class="pr-card" data-visualisation-id="closest_contests"><div class="pr-card-head"><h3>Closest contests</h3><p>The twelve smallest TCP winning margins in the filtered result.</p></div><div class="pr-card-body"><div id="pr-closest-contests" class="pr-analysis-list"></div></div></article>
            <article class="pr-card" data-visualisation-id="house_swing_analysis"><div class="pr-card-head"><h3>Largest reported TCP swings</h3><p>Winner-level TCP swing supplied by the AEC, ranked by absolute movement.</p></div><div class="pr-card-body"><div id="pr-swing-ranking" class="pr-signed-list"></div></div></article>
            <article class="pr-card" data-visualisation-id="party_gains_losses"><div class="pr-card-head"><h3>Party gains and losses</h3><p>Governed incumbent-based changes. Open seats remain labelled new member.</p></div><div class="pr-card-body"><div id="pr-party-movement" class="pr-movement-list"></div></div></article>
            <article class="pr-card" data-visualisation-id="vote_seat_comparison"><div class="pr-card-head"><h3>Vote share and seat share</h3><p>First-preference share compared with the share of declared seats in scope.</p></div><div class="pr-card-body"><div id="pr-vote-seat" class="pr-share-list"></div></div></article>
            <article class="pr-card" data-visualisation-id="state_comparison"><div class="pr-card-head"><h3>State and territory comparison</h3><p>Declared seats, average TCP margin, turnout and incumbent-based gains.</p></div><div class="pr-card-body"><div id="pr-state-comparison" class="pr-state-grid"></div></div></article>
            <article class="pr-card pr-analytics-wide" data-visualisation-id="vote_type_comparison"><div class="pr-card-head"><h3 id="pr-vote-type-title">Electorate vote types</h3><p>First preferences, TCP and TPP for the selected electorate. TPP is shown only where it was reported.</p></div><div class="pr-card-body"><div id="pr-vote-types" class="pr-vote-type-grid"></div></div></article>
          </div>
        </section>

        <section class="pr-section" id="house-results" data-route-section="house" data-visualisation-id="house_electorate_results" aria-labelledby="seat-finder-title">
          <div class="pr-section-heading">
            <div><p class="pr-eyebrow">Electorate results</p><h2 id="seat-finder-title">All House seats</h2><p>Search every electorate, then open its primary vote, TCP, TPP and participation detail.</p></div>
            <div class="pr-controls">
              <div class="pr-control"><label for="pr-house-state">State</label><select id="pr-house-state"><option value="ALL">All states</option></select></div>
              <div class="pr-control"><label for="pr-house-search">Search</label><input id="pr-house-search" type="search" placeholder="Electorate, member or party"></div>
            </div>
          </div>
          <div class="pr-electorate-layout">
            <article class="pr-card pr-electorate-index"><div class="pr-result-card-grid" id="pr-seat-cards" aria-label="House electorate results"></div><div class="pr-table-meta" id="pr-seat-count">Loading seats…</div></article>
            <article class="pr-card pr-electorate-detail-card"><div class="pr-card-body pr-seat-detail" id="pr-seat-detail"><div class="pr-empty">Select an electorate to inspect its full result.</div></div></article>
          </div>
        </section>

        <section class="pr-section" id="senate-composition" data-route-section="senate" data-visualisation-id="senate_composition" aria-labelledby="senate-composition-title">
          <div class="pr-section-heading"><div><p class="pr-eyebrow" id="pr-senate-composition-eyebrow">Senate</p><h2 id="senate-composition-title">Senate representation</h2><p id="pr-senate-composition-copy">Select a state, party or individual member in the governed view for this election.</p></div><div class="pr-snapshot-badge" id="pr-senate-snapshot">Snapshot loading…</div></div>
          <div class="pr-senate-delegations" id="pr-senate-delegations" data-visualisation-id="senate_state_delegations" aria-label="Senate delegations by state and territory"></div>
          <div class="pr-composition-layout">
            <article class="pr-card pr-composition-card"><div class="pr-card-head"><h3 id="pr-senate-composition-card-title">Full Senate</h3><p>Government, crossbench and opposition grouping; not a physical seating plan.</p></div><div class="pr-card-body"><div class="pr-chamber pr-chamber-senate" id="pr-senate-chamber" role="group" aria-label="Senate composition"></div><div class="pr-bloc-labels" aria-hidden="true"><span>Government</span><span>Crossbench</span><span>Opposition</span></div><div class="pr-legend" id="pr-senate-legend"></div></div></article>
            <article class="pr-card" data-visualisation-id="senate_member_chamber"><div class="pr-card-head"><h3>Selected senator</h3><p>State, party, bloc and term information.</p></div><div class="pr-card-body pr-composition-detail" id="pr-senate-member-detail"><div class="pr-empty">Select a Senate seat to inspect its member.</div></div></article>
          </div>
        </section>

        <section class="pr-section" id="senate" data-route-section="senate" data-visualisation-id="senate_group_result" aria-labelledby="senate-title">
          <div class="pr-section-heading"><div><p class="pr-eyebrow" id="pr-senate-election-label">Senate election</p><h2 id="senate-title">State and territory results</h2><p>Compare group first preferences and members declared elected at the selected election.</p></div><div class="pr-tabs" id="pr-senate-tabs" role="tablist" aria-label="Senate state"></div></div>
          <div class="pr-two-column">
            <article class="pr-card"><div class="pr-card-head"><h3 id="pr-senate-chart-title">Group vote</h3><p>First-preference group totals reported by the AEC.</p></div><div class="pr-card-body"><div class="pr-bar-list" id="pr-senate-chart"></div></div></article>
            <article class="pr-card" data-visualisation-id="senate_declared_outcomes"><div class="pr-card-head"><h3 id="pr-senate-members-title">Declared Senators</h3><p>Canonical elected outcomes with stable person identities where available.</p></div><div class="pr-card-body"><div class="pr-member-list" id="pr-senate-members"></div></div></article>
          </div>
        </section>

        <section class="pr-section" id="senate-count-centre" data-route-section="senate" data-visualisation-id="senate_count_animation" aria-labelledby="senate-count-title">
          <div class="pr-section-heading">
            <div><p class="pr-eyebrow">Distribution of preferences</p><h2 id="senate-count-title">Senate count centre</h2><p>Replay every official count, compare candidates with the quota, follow election and exclusion milestones, and inspect reported vote movements.</p></div>
            <div class="pr-count-round-badge" id="pr-count-round-badge" aria-live="polite">Detailed count loading…</div>
          </div>
          <div class="pr-senate-count-summary" aria-label="Selected Senate contest summary">
            <article><span>Vacancies</span><strong id="pr-count-vacancies">—</strong></article>
            <article><span>Quota</span><strong id="pr-count-quota">—</strong></article>
            <article><span>Count rounds</span><strong id="pr-count-rounds">—</strong></article>
            <article><span>Candidates</span><strong id="pr-count-candidates">—</strong></article>
          </div>
          <div class="pr-count-player" role="group" aria-label="Senate count animation controls">
            <button id="pr-count-previous" type="button" aria-label="Previous count">Previous</button>
            <button id="pr-count-play" type="button" aria-label="Play count animation" aria-pressed="false">Play</button>
            <button id="pr-count-next" type="button" aria-label="Next count">Next</button>
            <label for="pr-count-round">Count <span id="pr-count-round-label">—</span></label>
            <input id="pr-count-round" type="range" min="1" max="1" value="1" step="1" aria-describedby="pr-count-round-note">
            <label for="pr-count-speed">Speed</label>
            <select id="pr-count-speed"><option value="1400">Slow</option><option value="850" selected>Normal</option><option value="450">Fast</option></select>
          </div>
          <p class="pr-count-note" id="pr-count-round-note">The count player uses the AEC distribution-of-preferences progression in the governed release.</p>
          <div class="pr-count-layout">
            <article class="pr-card" data-visualisation-id="senate_quota_progress"><div class="pr-card-head"><h3>Candidate progress</h3><p>Progressive totals at the selected count. The vertical marker is one quota.</p></div><div class="pr-card-body"><div id="pr-count-candidates-chart" class="pr-candidate-progress"></div></div></article>
            <article class="pr-card" data-visualisation-id="senate_transfer_movements"><div class="pr-card-head"><h3>Reported movements</h3><p>Candidate inflows, outflows and exhaustion in this count; no unreported candidate-to-candidate path is inferred.</p></div><div class="pr-card-body"><div id="pr-count-movements" class="pr-transfer-movements"></div></div></article>
            <article class="pr-card" data-visualisation-id="senate_candidate_milestones"><div class="pr-card-head"><h3>Election and exclusion timeline</h3><p>First reported elected or excluded status for each candidate.</p></div><div class="pr-card-body"><div id="pr-count-milestones" class="pr-count-timeline"></div></div></article>
            <article class="pr-card" data-visualisation-id="senate_elected_timeline"><div class="pr-card-head"><h3>Declared election order</h3><p>The official final elected-senator order for the selected state or territory.</p></div><div class="pr-card-body"><div id="pr-count-elected-timeline" class="pr-count-timeline"></div></div></article>
          </div>
        </section>

        <section class="pr-section" id="participation" data-route-section="overview" data-visualisation-id="participation" aria-labelledby="participation-title">
          <div class="pr-section-heading">
            <div><p class="pr-eyebrow">Participation</p><h2 id="participation-title">Turnout and informality</h2><p>Official percentage measures across both chambers. National view shows state averages; select a state for its reporting units.</p></div>
            <div class="pr-controls">
              <div class="pr-control"><label for="pr-participation-state">State</label><select id="pr-participation-state"><option value="ALL">National view</option></select></div>
              <div class="pr-control"><label for="pr-participation-chamber">Chamber</label><select id="pr-participation-chamber"><option value="ALL">Both chambers</option><option value="chamber_house">House</option><option value="chamber_senate">Senate</option></select></div>
            </div>
          </div>
          <article class="pr-card"><div class="pr-card-body"><div class="pr-bar-list" id="pr-participation-chart"></div></div></article>
        </section>

        <section class="pr-section" id="evidence" data-route-section="sources" data-visualisation-id="source_evidence" aria-labelledby="evidence-title">
          <div class="pr-section-heading"><div><p class="pr-eyebrow">Evidence and reuse</p><h2 id="evidence-title">Release-bound data</h2><p>Verify the exact database release behind this page or download any fixed feed for independent analysis.</p></div></div>
          <div class="pr-evidence">
            <article class="pr-card"><div class="pr-card-head"><h3>Governance identity</h3><p>The active pointer is checksum-verified before any feed is generated.</p></div><div class="pr-card-body"><div class="pr-evidence-list" id="pr-evidence-list"></div><p class="pr-note">This interface is read-only. It cannot ingest files, edit the Grand Database or modify an immutable election release.</p></div></article>
            <article class="pr-card"><div class="pr-card-head"><h3>Publication downloads</h3><p>CSV, JSON, boundary geometry and manifest files carry governed source identities.</p></div><div class="pr-card-body"><div class="pr-downloads" id="pr-downloads"></div><p class="pr-note">The electorate map uses the checksum-pinned AEC national boundary dataset applicable to the selected election. The web geometry is simplified without removing any division.</p></div></article>
          </div>
        </section>

        <footer class="pr-footer"><span>Politica Election Results · Application 1.8.0</span><span>Schema 0.2.0 · Feed contract 1.8.0 · Visualisation contract 2.0.0</span></footer>
      </main>
    </div>`;
}

function appendStates(select) {
  for (const state of STATES) {
    const option = element("option", "", state);
    option.value = state;
    select.append(option);
  }
}

function appendMapViews(select) {
  clear(select);
  const groups = new Map();
  for (const [value, definition] of Object.entries(MAP_VIEWS)) {
    const option = element("option", "", definition.shortLabel || definition.label);
    option.value = value;
    if (definition.group === "National") {
      select.append(option);
      continue;
    }
    if (!groups.has(definition.group)) {
      const group = document.createElement("optgroup");
      group.label = definition.group;
      groups.set(definition.group, group);
      select.append(group);
    }
    groups.get(definition.group).append(option);
  }
}

function makeBar(label, value, maximum, detail, colour) {
  const row = element("div", "pr-bar-row");
  row.setAttribute("role", "img");
  row.setAttribute("aria-label", `${label}: ${detail}`);
  row.append(element("span", "pr-bar-label", label));
  const track = element("div", "pr-bar-track");
  const bar = element("div", "pr-bar");
  const width = maximum > 0 ? Math.max(1.5, Math.min(100, 100 * Number(value || 0) / maximum)) : 0;
  bar.style.width = `${width}%`;
  bar.style.background = colour || FALLBACK_COLOURS.default;
  track.append(bar);
  row.append(track, element("span", "pr-bar-value", detail));
  return row;
}

function chamberLabel(value) {
  return String(value || "").includes("senate") ? "Senate" : "House";
}

export function createResultsApp(mount, options = {}) {
  if (!mount) throw new Error("A results mount element is required");
  const fetchImpl = options.fetchImpl || globalThis.fetch?.bind(globalThis);
  if (!fetchImpl) throw new Error("Fetch is unavailable");
  const apiBase = options.apiBase ?? apiBaseFromLocation();
  const staticBase = String(options.staticBase || "").replace(/\/$/, "");
  mount.innerHTML = shell(staticBase ? "./" : "/results/", staticBase ? "Verified static release" : "Verified local release");

  const app = mount.querySelector(".pr-app");
  const status = mount.querySelector("#pr-status");
  const houseState = mount.querySelector("#pr-house-state");
  const mapState = mount.querySelector("#pr-map-state");
  const mapElectorate = mount.querySelector("#pr-map-electorate-focus");
  const houseSearch = mount.querySelector("#pr-house-search");
  const participationState = mount.querySelector("#pr-participation-state");
  const participationChamber = mount.querySelector("#pr-participation-chamber");
  const globalElection = mount.querySelector("#pr-global-election");
  const globalState = mount.querySelector("#pr-global-state");
  const globalParty = mount.querySelector("#pr-global-party");
  const globalElectorate = mount.querySelector("#pr-global-electorate");
  const clearFilters = mount.querySelector("#pr-clear-filters");
  const countPrevious = mount.querySelector("#pr-count-previous");
  const countPlay = mount.querySelector("#pr-count-play");
  const countNext = mount.querySelector("#pr-count-next");
  const countRound = mount.querySelector("#pr-count-round");
  const countSpeed = mount.querySelector("#pr-count-speed");
  appendStates(houseState);
  appendMapViews(mapState);
  appendStates(participationState);
  appendStates(globalState);
  const tooltip = createTooltip(app);

  const state = {
    catalogue: null,
    electionId: "",
    visualisations: null,
    registry: null,
    urlStore: null,
    feeds: new Map(),
    boundaries: null,
    mapCamera: null,
    mapController: null,
    view: "overview",
    party: "ALL",
    houseState: "ALL",
    mapView: "ALL",
    houseSearch: "",
    selectedSeat: null,
    selectedHouseMember: null,
    selectedSenateMember: null,
    selectedMember: "",
    senateState: "NSW",
    senateRound: null,
    senateLoadPromise: null,
    senateAnimationTimer: null,
    participationState: "ALL",
    participationChamber: "ALL"
  };

  const rows = (feed) => state.feeds.get(feed)?.data || [];

  function showStatus(message, kind = "loading") {
    status.hidden = !message;
    status.dataset.kind = kind;
    status.textContent = message || "";
  }

  function renderMetrics() {
    const seats = filterHouseSeats(rows("house_seat_results"), state.houseState, "", state.party);
    const parties = rows("house_party_summary").filter((row) => state.party === "ALL" || partyKey(row) === state.party);
    const senators = rows("senate_composition").filter((row) => state.party === "ALL" || partyKey(row) === state.party);
    const leader = [...parties].sort((a, b) => Number(b.declared_seats || 0) - Number(a.declared_seats || 0))[0];
    setText(mount, "#pr-metric-seats", formatNumber(seats.length));
    setText(mount, "#pr-metric-leading", leader?.party_name || "—");
    setText(mount, "#pr-metric-leading-note", leader ? `${formatNumber(leader.declared_seats)} declared seats` : "By declared seats");
    setText(mount, "#pr-metric-senators", formatNumber(senators.length));
    setText(mount, "#pr-metric-feeds", formatNumber(state.catalogue?.feeds?.length || FEED_IDS.length));
  }

  function renderHouseComposition() {
    const grid = mount.querySelector("#pr-house-chamber");
    const legend = mount.querySelector("#pr-seat-legend");
    clear(grid);
    clear(legend);
    const seats = rows("house_seat_results");
    const positioned = semicircleLayout(seats, {rows: 6});
    for (const seat of positioned) {
      const dot = element("button", "pr-chamber-seat");
      dot.type = "button";
      dot.style.left = `${100 * seat.x / 1000}%`;
      dot.style.top = `${100 * seat.y / 530}%`;
      dot.style.background = partyColour(seat);
      dot.dataset.party = partyKey(seat);
      dot.dataset.bloc = parliamentaryBloc(seat);
      const name = seat.person_name || seat.candidate_name || "Declared member";
      const label = `${seat.contest_name}: ${name}, ${seat.party_name || "Independent"}`;
      dot.setAttribute("aria-label", label);
      dot.setAttribute("aria-pressed", String(state.selectedHouseMember?.contest_id === seat.contest_id));
      const matchesState = state.houseState === "ALL" || stateForRow(seat) === state.houseState;
      const matchesParty = state.party === "ALL" || partyKey(seat) === state.party;
      const query = state.houseSearch.trim().toLowerCase();
      const matchesSearch = !query || [seat.contest_name, name, seat.party_name]
        .some((value) => String(value || "").toLowerCase().includes(query));
      if (!matchesState || !matchesParty || !matchesSearch) dot.classList.add("is-muted");
      tooltip.attach(dot, label);
      dot.addEventListener("click", () => {
        state.selectedHouseMember = seat;
        state.selectedSeat = seat;
        updateFilters({view: "overview", electorate: seat.contest_name || "", member: name}, {push: true});
      });
      grid.append(dot);
    }
    const parties = compositionSummary(seats);
    renderPartyLegend(legend, parties, {selected: state.party, onSelect: (party) => updateFilters({party})});
    if (!seats.length) grid.append(element("div", "pr-empty", "No declared House seats are available in this release."));
    renderCompositionDetail(mount.querySelector("#pr-house-member-detail"), state.selectedHouseMember, "house");
  }

  function renderCompositionDetail(root, member, chamber) {
    clear(root);
    if (!member) {
      root.append(element("div", "pr-empty", `Select a ${chamber === "senate" ? "Senate" : "House"} seat to inspect its member.`));
      return;
    }
    const name = member.person_name || member.candidate_name || "Member";
    const swatch = element("span", "pr-detail-swatch");
    swatch.style.background = partyColour(member);
    const heading = element("div", "pr-detail-heading");
    heading.append(swatch, element("h4", "", name));
    root.append(heading);
    const facts = element("dl", "pr-member-facts");
    const entries = chamber === "senate"
      ? [
          ["State or territory", stateForRow(member)],
          ["Party", member.party_name || "Independent"],
          ["Parliamentary grouping", String(parliamentaryBloc(member)).replace(/^./, (value) => value.toUpperCase())],
          ["Term expiry", member.term_expiry === "next_house_election" ? "Before the next House election" : member.term_expiry],
          ["Snapshot", member.snapshot_as_at]
        ]
      : [
          ["Electorate", member.contest_name],
          ["State or territory", stateForRow(member)],
          ["Party", member.party_name || "Independent"],
          ["TCP winning margin", formatPercent(member.winning_margin_percentage_points)]
        ];
    for (const [label, value] of entries) {
      facts.append(element("dt", "", label), element("dd", "", value || "—"));
    }
    root.append(facts);
    if (chamber === "house") {
      const link = element("button", "pr-detail-action", "Open electorate result");
      link.type = "button";
      link.addEventListener("click", () => updateFilters({view: "house", electorate: member.contest_name || "", member: name}, {push: true}));
      root.append(link);
    }
  }

  function renderPartyChart() {
    const chart = mount.querySelector("#pr-party-chart");
    clear(chart);
    const parties = [...rows("house_party_summary")]
      .filter((row) => state.party === "ALL" || partyKey(row) === state.party)
      .sort((a, b) => Number(b.first_preference_votes || 0) - Number(a.first_preference_votes || 0))
      .slice(0, 10);
    const maximum = Math.max(1, ...parties.map((row) => Number(row.first_preference_vote_share || 0)));
    for (const party of parties) {
      const share = Number(party.first_preference_vote_share || 0);
      chart.append(makeBar(
        party.party_name || "Independent / ungrouped",
        share,
        maximum,
        `${formatPercent(share)} · ${formatNumber(party.declared_seats)} seats`,
        partyColour(party)
      ));
    }
    if (!parties.length) chart.append(element("div", "pr-empty", "No national party summary is available."));
  }

  function renderMapDetail(seat) {
    const detail = mount.querySelector("#pr-map-detail");
    clear(detail);
    if (!seat) {
      detail.append(element("div", "pr-empty", "Select an electorate on the map."));
      return;
    }
    const heading = element("div", "pr-detail-heading");
    const swatch = element("span", "pr-detail-swatch");
    swatch.style.background = partyColour(seat);
    heading.append(swatch, element("h4", "", seat.contest_name || "Electorate"));
    detail.append(heading);
    const facts = element("dl", "pr-member-facts");
    const entries = [
      ["Declared member", seat.person_name || seat.candidate_name || "—"],
      ["Party", seat.party_name || "Independent"],
      ["State or territory", stateForRow(seat)],
      ["Result", seatChangeLabel(seat)],
      ["TCP share", formatPercent(seat.tcp_vote_share)],
      ["TCP margin", formatPercent(seat.winning_margin_percentage_points)]
    ];
    for (const [label, value] of entries) facts.append(element("dt", "", label), element("dd", "", value || "—"));
    detail.append(facts);
    const actions = element("div", "pr-detail-actions");
    const focus = element("button", "pr-detail-action", "Enlarge on map");
    focus.type = "button";
    focus.addEventListener("click", () => state.mapController?.focusSeat(seat.contest_name, {announce: true}));
    const button = element("button", "pr-detail-action", "Open full electorate result");
    button.type = "button";
    button.addEventListener("click", () => updateFilters({view: "house", electorate: seat.contest_name || "", member: seat.person_name || seat.candidate_name || ""}, {push: true}));
    actions.append(focus, button);
    detail.append(actions);
  }

  function populateMapElectorates(seats) {
    clear(mapElectorate);
    const placeholder = element("option", "", "Choose an electorate");
    placeholder.value = "";
    mapElectorate.append(placeholder);
    for (const seat of [...seats].sort((a, b) => String(a.contest_name).localeCompare(String(b.contest_name)))) {
      const option = element("option", "", `${seat.contest_name} — ${stateForRow(seat)}`);
      option.value = seat.contest_name || "";
      mapElectorate.append(option);
    }
    mapElectorate.value = state.selectedSeat?.contest_name || "";
  }

  function renderHouseMap() {
    const map = mount.querySelector("#pr-house-map");
    const legend = mount.querySelector("#pr-map-legend");
    const insets = mount.querySelector("#pr-map-insets");
    const seats = rows("house_seat_results");
    populateMapElectorates(seats);
    renderMapViewThumbnails(insets, {
      features: state.boundaries?.features || [],
      seats,
      selected: state.mapView,
      onSelect: (view) => {
        state.selectedSeat = null;
        state.selectedHouseMember = null;
        state.mapCamera = null;
        updateFilters({view: "house", mapView: view, electorate: "", member: ""});
      }
    });
    const rendered = renderElectorateMap(map, {
      features: state.boundaries?.features || [],
      seats,
      selectedSeat: state.selectedSeat,
      view: state.mapView,
      party: state.party,
      search: state.houseSearch,
      tooltip,
      initialCamera: state.mapCamera,
      onCameraChange: (camera) => {
        state.mapCamera = camera;
      },
      onBackToAustralia: () => {
        state.selectedSeat = null;
        state.selectedHouseMember = null;
        state.mapCamera = null;
        updateFilters({view: "house", mapView: "ALL", electorate: "", member: ""});
      },
      onSelect: (seat) => {
        state.selectedSeat = seat;
        state.selectedHouseMember = seat;
        updateFilters({view: "house", electorate: seat.contest_name || "", member: seat.person_name || seat.candidate_name || ""}, {push: true});
      }
    });
    state.mapController = rendered;
    clear(legend);
    renderPartyLegend(legend, compositionSummary(seats), {
      selected: state.party,
      onSelect: (party) => updateFilters({party})
    });
    const mapLabel = MAP_VIEWS[state.mapView]?.label || MAP_VIEWS.ALL.label;
    setText(mount, "#pr-map-count", `${mapLabel} · ${formatNumber(rendered.matchedCount)} electorates available · ${formatNumber(rendered.featureCount)} governed boundary features`);
    renderMapDetail(state.selectedSeat);
  }

  function selectAnalyticSeat(seat) {
    state.selectedSeat = seat;
    state.selectedHouseMember = seat;
    updateFilters({
      view: "house",
      electorate: seat.contest_name || "",
      member: seat.person_name || seat.candidate_name || ""
    }, {push: true});
  }

  function analyticSeatButton(seat, detail) {
    const button = element("button", "pr-analysis-seat");
    button.type = "button";
    button.style.setProperty("--party-colour", partyColour(seat));
    const copy = element("span", "pr-analysis-seat-copy");
    copy.append(
      element("strong", "", seat.contest_name || "Electorate"),
      element("span", "", `${seat.party_name || "Independent"} · ${stateForRow(seat)}`)
    );
    button.append(copy, element("span", "pr-analysis-seat-value", detail));
    button.addEventListener("click", () => selectAnalyticSeat(seat));
    return button;
  }

  function renderAnalytics() {
    const allSeats = rows("house_seat_results");
    const scope = filterHouseSeats(allSeats, state.houseState, state.houseSearch, "ALL");
    const winnerScope = state.party === "ALL" ? scope : scope.filter((seat) => partyKey(seat) === state.party);
    const spectrum = marginSpectrumRows(winnerScope);
    const selectedParty = rows("house_party_summary").find((row) => partyKey(row) === state.party);
    const scopeLabel = [
      state.houseState === "ALL" ? "Australia" : state.houseState,
      state.party === "ALL" ? "all winning parties" : (selectedParty?.party_name || "selected party"),
      state.houseSearch ? `matching “${state.houseSearch}”` : ""
    ].filter(Boolean).join(" · ");
    setText(mount, "#pr-analysis-summary", `${formatNumber(winnerScope.length)} declared seats in scope · ${scopeLabel}`);

    const pendulum = mount.querySelector("#pr-margin-spectrum");
    clear(pendulum);
    const maximumMargin = Math.max(1, ...spectrum.map((seat) => seat.margin));
    for (const seat of spectrum) {
      const button = element("button", "pr-pendulum-seat");
      button.type = "button";
      button.style.setProperty("--party-colour", partyColour(seat));
      const label = element("span", "pr-pendulum-label");
      label.append(element("strong", "", seat.contest_name || "Electorate"), element("span", "", seat.party_name || "Independent"));
      const track = element("span", "pr-pendulum-track");
      const bar = element("span", "pr-pendulum-bar");
      bar.style.width = `${Math.max(1.5, 100 * seat.margin / maximumMargin)}%`;
      track.append(bar);
      button.append(label, track, element("span", "pr-pendulum-value", formatPercent(seat.margin)));
      button.setAttribute("aria-label", `${seat.contest_name}, ${seat.party_name || "Independent"}, TCP margin ${formatPercent(seat.margin)}`);
      button.addEventListener("click", () => selectAnalyticSeat(seat));
      pendulum.append(button);
    }
    if (!spectrum.length) pendulum.append(element("div", "pr-empty", "No TCP margins match these filters."));

    const closest = mount.querySelector("#pr-closest-contests");
    clear(closest);
    for (const seat of closestContestRows(winnerScope, 12)) closest.append(analyticSeatButton(seat, formatPercent(seat.margin)));
    if (!closest.children.length) closest.append(element("div", "pr-empty", "No closest-contest ranking is available for this scope."));

    const swings = mount.querySelector("#pr-swing-ranking");
    clear(swings);
    const swingRows = tcpSwingRows(winnerScope, 14);
    const maximumSwing = Math.max(1, ...swingRows.map((seat) => Math.abs(seat.tcpSwing)));
    for (const seat of swingRows) {
      const button = element("button", "pr-signed-row");
      button.type = "button";
      const label = element("span", "pr-signed-label", seat.contest_name || "Electorate");
      const track = element("span", "pr-signed-track");
      const bar = element("span", "pr-signed-bar");
      const width = 50 * Math.abs(seat.tcpSwing) / maximumSwing;
      bar.style.width = `${width}%`;
      bar.style.left = `${seat.tcpSwing >= 0 ? 50 : 50 - width}%`;
      bar.style.background = partyColour(seat);
      track.append(bar);
      const value = `${seat.tcpSwing >= 0 ? "+" : ""}${formatPercent(seat.tcpSwing)}`;
      button.append(label, track, element("span", "pr-signed-value", value));
      button.setAttribute("aria-label", `${seat.contest_name}: AEC-reported winner TCP swing ${value}`);
      button.addEventListener("click", () => selectAnalyticSeat(seat));
      swings.append(button);
    }
    if (!swingRows.length) swings.append(element("div", "pr-empty", "No AEC-reported TCP swing values match these filters."));

    const movement = mount.querySelector("#pr-party-movement");
    clear(movement);
    const selectedPartyName = String(selectedParty?.party_name || "").toLowerCase();
    const movementRows = partyGainLossRows(scope).filter((row) => state.party === "ALL" || row.partyKey === state.party || row.partyName.toLowerCase() === selectedPartyName);
    for (const row of movementRows) {
      const item = element("div", "pr-movement-row");
      const label = element("span", "pr-movement-party");
      const swatch = element("span", "pr-swatch");
      swatch.style.background = row.colour || FALLBACK_COLOURS.default;
      label.append(swatch, element("strong", "", row.partyName));
      const values = element("span", "pr-movement-values");
      values.append(
        element("span", "is-gain", `+${formatNumber(row.gains)} gains`),
        element("span", "is-loss", `−${formatNumber(row.losses)} losses`),
        element("span", "", `${formatNumber(row.retained)} retained`),
        element("span", "", `${formatNumber(row.newMembers)} new member`)
      );
      item.append(label, values);
      movement.append(item);
    }
    if (!movementRows.length) movement.append(element("div", "pr-empty", "No governed party movements match these filters."));

    const voteSeat = mount.querySelector("#pr-vote-seat");
    clear(voteSeat);
    const comparisons = voteSeatRows(rows("house_party_summary"), scope, rows("house_candidate_results"))
      .filter((row) => state.party === "ALL" || row.partyKey === state.party)
      .slice(0, 14);
    for (const row of comparisons) {
      const item = element("div", "pr-share-row");
      const title = element("div", "pr-share-title");
      const swatch = element("span", "pr-swatch");
      swatch.style.background = row.colour;
      title.append(swatch, element("strong", "", row.partyName));
      const tracks = element("div", "pr-share-tracks");
      for (const [labelText, value] of [["Vote", row.voteShare], ["Seats", row.seatShare]]) {
        const line = element("div", "pr-share-track-row");
        const track = element("span", "pr-share-track");
        const bar = element("span", `pr-share-bar ${labelText === "Seats" ? "is-seats" : ""}`);
        bar.style.width = `${Math.max(0, Math.min(100, value))}%`;
        bar.style.background = row.colour;
        track.append(bar);
        line.append(element("span", "", labelText), track, element("span", "", formatPercent(value)));
        tracks.append(line);
      }
      item.append(title, tracks);
      voteSeat.append(item);
    }
    if (!comparisons.length) voteSeat.append(element("div", "pr-empty", "No vote-to-seat comparison matches these filters."));

    const states = mount.querySelector("#pr-state-comparison");
    clear(states);
    const stateRows = stateComparisonRows(winnerScope);
    for (const row of stateRows) {
      const item = element("article", "pr-state-card");
      item.append(
        element("strong", "", row.state),
        element("span", "", `${formatNumber(row.seats)} seats`),
        element("span", "", `Average margin ${formatPercent(row.averageMargin)}`),
        element("span", "", `Average turnout ${formatPercent(row.averageTurnout)}`),
        element("span", "", `${formatNumber(row.gains)} incumbent-based gains`)
      );
      states.append(item);
    }
    if (!stateRows.length) states.append(element("div", "pr-empty", "No state comparison matches these filters."));

    const voteTypes = mount.querySelector("#pr-vote-types");
    clear(voteTypes);
    const comparisonSeat = state.selectedSeat && winnerScope.some((seat) => seat.contest_id === state.selectedSeat.contest_id)
      ? state.selectedSeat
      : closestContestRows(winnerScope, 1)[0] || spectrum[0];
    setText(mount, "#pr-vote-type-title", comparisonSeat ? `${comparisonSeat.contest_name} vote types` : "Electorate vote types");
    if (comparisonSeat) {
      const typeRows = voteTypeRows(rows("house_candidate_results"), comparisonSeat.contest_id);
      for (const [type, title, limit] of [["first_preference", "First preferences", 8], ["tcp", "TCP", 2], ["tpp", "TPP", 2]]) {
        const section = element("section", "pr-vote-type-card");
        section.append(element("h4", "", title));
        const candidates = typeRows.filter((row) => row.result_type === type).slice(0, limit);
        if (!candidates.length) {
          section.append(element("p", "pr-result-unavailable", `${title} was not reported for this electorate.`));
        } else {
          const chart = element("div", "pr-bar-list");
          const maximum = Math.max(1, ...candidates.map((row) => row.votesNumber));
          for (const candidate of candidates) chart.append(makeBar(
            candidate.subject_name || candidate.party_name || "Candidate",
            candidate.votesNumber,
            maximum,
            `${formatNumber(candidate.votesNumber)} · ${formatPercent(candidate.voteShareNumber)}`,
            partyColour(candidate)
          ));
          section.append(chart);
        }
        voteTypes.append(section);
      }
    } else {
      voteTypes.append(element("div", "pr-empty", "Select or search for an electorate to compare its vote types."));
    }
  }

  function renderSeatDetail(seat) {
    const detail = mount.querySelector("#pr-seat-detail");
    clear(detail);
    if (!seat) {
      detail.append(element("div", "pr-empty", "Select an electorate to inspect its full result."));
      return;
    }

    const header = element("div", "pr-result-header");
    const heading = element("div");
    heading.append(
      element("span", `pr-seat-status pr-seat-status-${seat.seat_change_type || "new_member"}`, seatChangeLabel(seat)),
      element("h3", "", seat.contest_name || "Electorate"),
      element("p", "", `${stateForRow(seat)} · ${seat.person_name || seat.candidate_name || "Declared member"} · ${seat.party_name || "Independent"}`)
    );
    const swatch = element("span", "pr-result-party-swatch");
    swatch.style.background = partyColour(seat);
    header.append(heading, swatch);
    detail.append(header);

    const statistics = element("dl", "pr-result-stats");
    const entries = [
      ["TCP margin", formatPercent(seat.winning_margin_percentage_points)],
      ["Enrolment", formatNumber(seat.enrolment)],
      ["Votes counted", formatNumber(seat.votes_counted)],
      ["Turnout", formatPercent(seat.turnout_percentage)],
      ["Informal", formatPercent(seat.informality_percentage)],
      ["Count / enrolment", formatPercent(seat.counted_percentage_of_enrolment)]
    ];
    for (const [label, value] of entries) {
      const item = element("div", "pr-result-stat");
      item.append(element("dt", "", label), element("dd", "", value));
      statistics.append(item);
    }
    detail.append(statistics);

    const all = rows("house_candidate_results").filter((row) => row.contest_id === seat.contest_id);

    const resultSection = (resultType, title, emptyMessage) => {
      const candidates = candidateRowsForResult(all, seat.contest_id, resultType);
      const section = element("section", "pr-result-section");
      section.append(element("h4", "", title));
      if (!candidates.length) {
        section.append(element("p", "pr-result-unavailable", emptyMessage));
        return section;
      }
      const total = candidates.reduce((sum, row) => sum + Number(row.votes || 0), 0);
      const maximum = Math.max(1, ...candidates.map((row) => Number(row.votes || 0)));
      const chart = element("div", "pr-bar-list");
      for (const candidate of candidates) {
        const share = candidate.vote_share === null || candidate.vote_share === undefined
          ? (total ? 100 * Number(candidate.votes || 0) / total : null)
          : Number(candidate.vote_share);
        const swing = candidate.swing === null || candidate.swing === undefined
          ? ""
          : ` · swing ${Number(candidate.swing) >= 0 ? "+" : ""}${formatPercent(candidate.swing)}`;
        chart.append(makeBar(
          `${candidate.subject_name || "Candidate"}${candidate.party_name ? ` · ${candidate.party_name}` : ""}`,
          Number(candidate.votes || 0),
          maximum,
          `${formatNumber(candidate.votes)} · ${formatPercent(share)}${swing}`,
          partyColour(candidate)
        ));
      }
      section.append(chart);
      return section;
    };

    detail.append(
      resultSection("tcp", "Two-candidate preferred", "TCP totals are not available for this electorate."),
      resultSection("tpp", "Two-party preferred", "TPP is not available for this electorate or did not form the final candidate pairing.")
    );

    const primary = element("details", "pr-primary-expander");
    const primarySummary = element("summary", "", "Show primary votes");
    primary.append(primarySummary);
    const primaryRows = candidateRowsForResult(all, seat.contest_id, "first_preference");
    if (primaryRows.length) {
      primary.append(resultSection("first_preference", "First preferences", "Primary votes are unavailable."));
    } else {
      primary.append(element("p", "pr-result-unavailable", "Primary votes are not available for this electorate."));
    }
    detail.append(primary);

    const metadata = element("div", "pr-count-metadata");
    metadata.append(
      element("h4", "", "Count metadata"),
      element("p", "", `${formatNumber(seat.formal_votes)} formal votes and ${formatNumber(seat.informal_votes)} informal votes are recorded in this final governed release.`)
    );
    detail.append(metadata);
  }

  function renderSeatCards() {
    const cards = mount.querySelector("#pr-seat-cards");
    clear(cards);
    const filtered = filterHouseSeats(rows("house_seat_results"), state.houseState, state.houseSearch, state.party);
    for (const seat of filtered) {
      const card = element("button", "pr-result-card");
      card.type = "button";
      card.setAttribute("aria-pressed", String(state.selectedSeat?.contest_id === seat.contest_id));
      card.style.setProperty("--party-colour", partyColour(seat));
      const top = element("span", "pr-result-card-top");
      top.append(
        element("strong", "", seat.contest_name || "—"),
        element("span", `pr-seat-status pr-seat-status-${seat.seat_change_type || "new_member"}`, seatChangeLabel(seat))
      );
      const member = element("span", "pr-result-card-member", seat.person_name || seat.candidate_name || "Declared member");
      const party = element("span", "pr-result-card-party", seat.party_name || "Independent");
      const numbers = element("span", "pr-result-card-numbers");
      numbers.append(
        element("span", "", `TCP ${formatPercent(seat.tcp_vote_share)}`),
        element("span", "", `Turnout ${formatPercent(seat.turnout_percentage)}`)
      );
      card.append(top, member, party, numbers);
      const select = () => {
        state.selectedSeat = seat;
        state.selectedHouseMember = seat;
        updateFilters({view: "house", electorate: seat.contest_name || "", member: seat.person_name || seat.candidate_name || ""}, {push: true});
      };
      card.addEventListener("click", select);
      cards.append(card);
    }
    setText(mount, "#pr-seat-count", `${formatNumber(filtered.length)} of ${formatNumber(rows("house_seat_results").length)} declared seats`);
    if (!filtered.length) {
      cards.append(element("div", "pr-empty", "No electorates match these filters."));
    }
  }

  function renderSenateTabs() {
    const tabs = mount.querySelector("#pr-senate-tabs");
    clear(tabs);
    for (const territory of STATES) {
      const button = element("button", "pr-tab", territory);
      button.type = "button";
      button.setAttribute("role", "tab");
      button.setAttribute("aria-selected", String(territory === state.senateState));
      button.addEventListener("click", () => {
        updateFilters({senateState: territory});
      });
      tabs.append(button);
    }
  }

  function renderSenateComposition() {
    const chamber = mount.querySelector("#pr-senate-chamber");
    const legend = mount.querySelector("#pr-senate-legend");
    clear(chamber);
    clear(legend);
    const members = rows("senate_composition");
    const positioned = semicircleLayout(members, {rows: 5});
    const query = state.houseSearch.trim().toLowerCase();
    for (const member of positioned) {
      const dot = element("button", "pr-chamber-seat pr-chamber-seat-senate");
      dot.type = "button";
      dot.style.left = `${100 * member.x / 1000}%`;
      dot.style.top = `${100 * member.y / 530}%`;
      dot.style.background = partyColour(member);
      dot.dataset.party = partyKey(member);
      dot.dataset.bloc = parliamentaryBloc(member);
      const label = `${member.person_name}, ${member.state}, ${member.party_name}`;
      dot.setAttribute("aria-label", label);
      dot.setAttribute("aria-pressed", String(state.selectedSenateMember?.person_id === member.person_id));
      const matchesState = state.houseState === "ALL" || stateForRow(member) === state.houseState;
      const matchesParty = state.party === "ALL" || partyKey(member) === state.party;
      const matchesSearch = !query || [member.person_name, member.party_name, member.state]
        .some((value) => String(value || "").toLowerCase().includes(query));
      if (!matchesState || !matchesParty || !matchesSearch) dot.classList.add("is-muted");
      tooltip.attach(dot, label);
      dot.addEventListener("click", () => {
        state.selectedSenateMember = member;
        updateFilters({
          view: "senate",
          state: member.state,
          senateState: member.state,
          member: member.person_name
        }, {push: true});
      });
      chamber.append(dot);
    }
    renderPartyLegend(legend, compositionSummary(members), {
      selected: state.party,
      onSelect: (party) => updateFilters({party})
    });
    if (!members.length) chamber.append(element("div", "pr-empty", "The governed Senate composition snapshot is unavailable."));
    renderCompositionDetail(mount.querySelector("#pr-senate-member-detail"), state.selectedSenateMember, "senate");
    const snapshot = members[0];
    const isElectionResult = snapshot?.membership_basis === "declared_elected_at_selected_election";
    setText(
      mount,
      "#pr-senate-snapshot",
      snapshot
        ? `${isElectionResult ? "Declared" : "As at"} ${snapshot.snapshot_as_at} · ${formatNumber(members.length)} ${isElectionResult ? "senators" : "seats"}`
        : "Snapshot unavailable"
    );
  }

  function renderSenateDelegations() {
    const root = mount.querySelector("#pr-senate-delegations");
    clear(root);
    for (const delegation of senateDelegationSummary(rows("senate_composition"))) {
      const button = element("button", "pr-delegation-card");
      button.type = "button";
      button.setAttribute("aria-pressed", String(delegation.state === state.senateState));
      button.setAttribute("aria-label", `${delegation.state} Senate delegation: ${delegation.members.length} senators`);
      const heading = element("div", "pr-delegation-heading");
      heading.append(element("strong", "", delegation.state), element("span", "", `${delegation.members.length} senators`));
      const dots = element("div", "pr-delegation-dots");
      for (const member of delegation.members) {
        const dot = element("span", "pr-delegation-dot");
        dot.style.background = partyColour(member);
        dot.title = `${member.person_name} · ${member.party_name}`;
        if (state.party !== "ALL" && partyKey(member) !== state.party) dot.classList.add("is-muted");
        dots.append(dot);
      }
      const summary = element("span", "pr-delegation-parties", delegation.parties.map((party) => `${party.partyName} ${party.count}`).join(" · "));
      button.append(heading, dots, summary);
      button.addEventListener("click", () => updateFilters({view: "senate", senateState: delegation.state}, {push: true}));
      root.append(button);
    }
  }

  function selectedSenateRounds() {
    return senateRounds(rows("senate_count_progress"), state.senateState);
  }

  function stopCountAnimation() {
    const environment = mount.ownerDocument?.defaultView || globalThis;
    if (state.senateAnimationTimer) environment.clearInterval(state.senateAnimationTimer);
    state.senateAnimationTimer = null;
    countPlay.textContent = "Play";
    countPlay.setAttribute("aria-pressed", "false");
  }

  function setSenateRound(round) {
    const rounds = selectedSenateRounds();
    if (!rounds.length) return;
    const requested = Number(round);
    state.senateRound = rounds.includes(requested) ? requested : rounds[rounds.length - 1];
    renderSenateCount();
  }

  function stepSenateRound(direction) {
    const rounds = selectedSenateRounds();
    if (!rounds.length) return;
    const currentIndex = Math.max(0, rounds.indexOf(state.senateRound));
    const nextIndex = Math.max(0, Math.min(rounds.length - 1, currentIndex + direction));
    setSenateRound(rounds[nextIndex]);
    if (nextIndex === rounds.length - 1 && direction > 0) stopCountAnimation();
  }

  function renderSenateCount() {
    const candidateRoot = mount.querySelector("#pr-count-candidates-chart");
    const movementRoot = mount.querySelector("#pr-count-movements");
    const milestoneRoot = mount.querySelector("#pr-count-milestones");
    const electedRoot = mount.querySelector("#pr-count-elected-timeline");
    for (const root of [candidateRoot, movementRoot, milestoneRoot, electedRoot]) clear(root);
    const progress = rows("senate_count_progress");
    const movementFeed = rows("senate_count_movements");
    const rounds = selectedSenateRounds();
    if (!rounds.length) {
      const pending = state.senateLoadPromise ? "Loading the detailed Senate count…" : "Detailed Senate count data is unavailable.";
      candidateRoot.append(element("div", "pr-empty", pending));
      movementRoot.append(element("div", "pr-empty", pending));
      milestoneRoot.append(element("div", "pr-empty", pending));
      electedRoot.append(element("div", "pr-empty", pending));
      setText(mount, "#pr-count-round-badge", pending);
      return;
    }
    if (!rounds.includes(state.senateRound)) state.senateRound = rounds[rounds.length - 1];
    const snapshot = senateRoundSnapshot(progress, state.senateState, state.senateRound);
    const metadata = snapshot[0] || {};
    const quota = Number(metadata.quota_value || 0);
    const candidates = new Set(progress.filter((row) => stateForRow(row) === state.senateState).map((row) => row.candidacy_id));
    const elected = rows("declared_members")
      .filter((row) => String(row.chamber_id).includes("senate") && stateForRow(row) === state.senateState)
      .sort((a, b) => Number(a.elected_order || 999) - Number(b.elected_order || 999));
    setText(mount, "#pr-count-vacancies", formatNumber(elected.length));
    setText(mount, "#pr-count-quota", quota ? formatNumber(quota) : "—");
    setText(mount, "#pr-count-rounds", formatNumber(rounds.length));
    setText(mount, "#pr-count-candidates", formatNumber(candidates.size));
    setText(mount, "#pr-count-round-label", `${state.senateRound} of ${rounds[rounds.length - 1]}`);
    const action = String(metadata.action_type || "count").replaceAll("_", " ");
    setText(mount, "#pr-count-round-badge", `${state.senateState} · Count ${state.senateRound} · ${action}`);
    countRound.min = String(rounds[0]);
    countRound.max = String(rounds[rounds.length - 1]);
    countRound.value = String(state.senateRound);
    countPrevious.disabled = state.senateRound === rounds[0];
    countNext.disabled = state.senateRound === rounds[rounds.length - 1];
    const maximum = Math.max(quota || 1, ...snapshot.map((row) => row.total));
    for (const candidate of snapshot) {
      const row = element("div", `pr-candidate-row pr-candidate-${candidate.status.replace(/[^a-z]+/g, "-")}`);
      row.setAttribute("role", "img");
      const change = candidate.change;
      const detail = `${formatNumber(candidate.total)} votes; ${change > 0 ? "+" : ""}${formatNumber(change)} this count; ${candidate.status}`;
      row.setAttribute("aria-label", `${candidate.candidate_name}: ${detail}`);
      const label = element("div", "pr-candidate-label");
      const swatch = element("span", "pr-swatch");
      swatch.style.background = partyColour(candidate);
      label.append(swatch, element("span", "", candidate.candidate_name || "Candidate"));
      const track = element("div", "pr-candidate-track");
      const bar = element("span", "pr-candidate-bar");
      bar.style.width = `${Math.max(0, Math.min(100, 100 * candidate.total / maximum))}%`;
      bar.style.background = partyColour(candidate);
      if (quota) {
        const marker = element("span", "pr-quota-marker");
        marker.style.left = `${Math.min(100, 100 * quota / maximum)}%`;
        marker.title = `Quota ${formatNumber(quota)}`;
        track.append(marker);
      }
      track.append(bar);
      const value = element("div", "pr-candidate-value", `${formatNumber(candidate.total)} · ${change > 0 ? "+" : ""}${formatNumber(change)}`);
      row.append(label, track, value, element("span", "pr-candidate-status", candidate.status));
      candidateRoot.append(row);
    }
    if (!snapshot.length) candidateRoot.append(element("div", "pr-empty", "No candidate totals are available for this count."));

    const movements = senateMovementRows(movementFeed, state.senateState, state.senateRound);
    const movementMaximum = Math.max(1, ...movements.map((row) => Math.abs(row.movement)));
    for (const movement of movements) {
      const row = element("div", "pr-transfer-row");
      const label = element("div", "pr-transfer-label");
      const swatch = element("span", "pr-swatch");
      swatch.style.background = movement.colour;
      label.append(swatch, element("span", "", movement.candidateName));
      const track = element("div", "pr-transfer-track");
      const zero = element("span", "pr-transfer-zero");
      const bar = element("span", `pr-transfer-bar ${movement.movement >= 0 ? "is-inflow" : "is-outflow"}`);
      bar.style.width = `${50 * Math.abs(movement.movement) / movementMaximum}%`;
      bar.style.background = movement.colour;
      track.append(zero, bar);
      row.append(label, track, element("span", "pr-transfer-value", `${movement.movement > 0 ? "+" : ""}${formatNumber(movement.movement)}`));
      movementRoot.append(row);
    }
    if (!movements.length) movementRoot.append(element("div", "pr-empty", "No reported movement occurred in this count."));

    for (const milestone of senateMilestones(progress, state.senateState)) {
      const item = element("div", `pr-timeline-item is-${milestone.type}`);
      const marker = element("span", "pr-timeline-marker");
      marker.style.background = milestone.colour;
      const copy = element("div");
      copy.append(element("strong", "", milestone.candidateName), element("span", "", `${milestone.partyName} · Count ${milestone.round} · ${milestone.type}`));
      item.append(marker, copy, element("span", "pr-timeline-value", formatNumber(milestone.total)));
      milestoneRoot.append(item);
    }
    if (!milestoneRoot.children.length) milestoneRoot.append(element("div", "pr-empty", "No elected or excluded milestones are reported."));

    for (const member of elected) {
      const item = element("div", "pr-timeline-item is-elected");
      const marker = element("span", "pr-timeline-order", String(member.elected_order || "✓"));
      const copy = element("div");
      copy.append(element("strong", "", member.person_name || member.candidate_name || "Declared candidate"), element("span", "", member.party_name || "Independent / ungrouped"));
      item.append(marker, copy);
      electedRoot.append(item);
    }
    if (!elected.length) electedRoot.append(element("div", "pr-empty", "No declared Senate outcomes are available."));
  }

  function renderSenate() {
    const groupChart = mount.querySelector("#pr-senate-chart");
    const memberList = mount.querySelector("#pr-senate-members");
    clear(groupChart);
    clear(memberList);
    setText(mount, "#pr-senate-chart-title", `${state.senateState} group vote`);
    setText(mount, "#pr-senate-members-title", `${state.senateState} declared Senators`);
    const available = rows("senate_group_results").filter((row) => stateForRow(row) === state.senateState);
    const level = available.some((row) => row.reporting_level === "contest") ? "contest" : "state";
    const quotaRow = rows("senate_count_progress").find((row) => stateForRow(row) === state.senateState && Number(row.quota_value || 0) > 0);
    const quota = Number(quotaRow?.quota_value || 0);
    const groups = senateGroupQuotaRows(available
      .filter((row) => row.reporting_level === level)
      .filter((row) => state.party === "ALL" || partyKey(row) === state.party), quota)
      .slice(0, 20);
    const total = groups.reduce((sum, row) => sum + Number(row.votes || 0), 0);
    const maximum = Math.max(1, ...groups.map((row) => Number(row.votes || 0)));
    for (const group of groups) {
      const share = group.vote_share === null || group.vote_share === undefined
        ? (total ? 100 * Number(group.votes || 0) / total : null)
        : Number(group.vote_share);
      groupChart.append(makeBar(
        group.party_name || group.subject_name || "Ungrouped",
        Number(group.votes || 0),
        maximum,
        `${formatNumber(group.votes)} · ${formatPercent(share)}${group.quotaMultiple === null ? "" : ` · ${group.quotaMultiple.toFixed(2)} quotas`}`,
        partyColour(group)
      ));
    }
    if (!groups.length) groupChart.append(element("div", "pr-empty", `No ${state.senateState} Senate group totals are available.`));

    const members = rows("declared_members")
      .filter((row) => String(row.chamber_id).includes("senate") && stateForRow(row) === state.senateState)
      .filter((row) => state.party === "ALL" || partyKey(row) === state.party)
      .sort((a, b) => Number(a.elected_order || 999) - Number(b.elected_order || 999));
    for (const member of members) {
      const item = element("div", "pr-member");
      const copy = element("div");
      copy.append(element("strong", "", member.person_name || member.candidate_name || "Declared candidate"), element("span", "", member.party_name || "Independent / ungrouped"));
      item.append(copy, element("span", "", member.elected_order ? `Elected ${member.elected_order}` : "Declared"));
      memberList.append(item);
    }
    if (!members.length) memberList.append(element("div", "pr-empty", `No ${state.senateState} declared outcomes are available.`));
  }

  function renderParticipation() {
    const chart = mount.querySelector("#pr-participation-chart");
    clear(chart);
    const aggregates = aggregateParticipation(rows("turnout_informality"), state.participationState, state.participationChamber);
    for (const item of aggregates) {
      const measure = item.measure === "turnout_percentage" ? "Turnout" : "Informality";
      const colour = item.measure === "turnout_percentage" ? FALLBACK_COLOURS.liberal : "#b06b22";
      chart.append(makeBar(
        `${item.place} · ${chamberLabel(item.chamber)} ${measure}`,
        item.value,
        100,
        formatPercent(item.value),
        colour
      ));
    }
    if (!aggregates.length) chart.append(element("div", "pr-empty", "No percentage measures match these filters."));
  }

  function renderEvidence() {
    renderSourcePanel(mount, {
      catalogue: state.catalogue,
      visualisations: state.visualisations,
      electionId: state.electionId,
      apiBase,
      staticBase
    });
  }

  function renderRoute() {
    for (const section of mount.querySelectorAll("[data-route-section]")) {
      section.hidden = !section.dataset.routeSection.split(/\s+/).includes(state.view);
    }
    for (const link of mount.querySelectorAll("[data-view]")) {
      if (link.dataset.view === state.view) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    app.dataset.route = state.view;
  }

  function populatePartyOptions() {
    const selected = state.party;
    clear(globalParty);
    const all = element("option", "", "All parties");
    all.value = "ALL";
    globalParty.append(all);
    const seen = new Set();
    const parties = [...rows("house_party_summary"), ...compositionSummary(rows("senate_composition"))]
      .sort((a, b) => String(a.party_name).localeCompare(String(b.party_name)));
    for (const party of parties) {
      const key = partyKey(party);
      if (seen.has(key)) continue;
      seen.add(key);
      const option = element("option", "", party.party_name || "Independent / ungrouped");
      option.value = key;
      globalParty.append(option);
    }
    globalParty.value = seen.has(selected) ? selected : "ALL";
    if (globalParty.value !== selected) state.party = "ALL";
  }

  function populateElectionOptions() {
    clear(globalElection);
    for (const election of state.catalogue?.elections || []) {
      const option = element(
        "option",
        "",
        `${String(election.election_date || "").slice(0, 4)} federal election`
      );
      option.value = election.election_id;
      globalElection.append(option);
    }
    globalElection.value = state.electionId;
  }

  function syncControls() {
    globalElection.value = state.electionId;
    houseState.value = state.houseState;
    mapState.value = state.mapView;
    houseSearch.value = state.houseSearch;
    participationState.value = state.participationState;
    participationChamber.value = state.participationChamber;
    globalState.value = state.houseState;
    globalParty.value = state.party;
    globalElectorate.value = state.houseSearch;
  }

  async function ensureSenateDetails() {
    if (SENATE_DETAIL_FEEDS.every((feed) => state.feeds.has(feed))) return state.feeds;
    if (state.senateLoadPromise) return state.senateLoadPromise;
    if (!state.electionId) return state.feeds;
    setText(mount, "#pr-count-round-badge", "Loading detailed Senate count…");
    const electionId = state.electionId;
    state.senateLoadPromise = Promise.all(SENATE_DETAIL_FEEDS.map(async (feed) => [
      feed,
      await fetchJson(fetchImpl, publicPath(feed, "json", electionId, apiBase, staticBase))
    ])).then((representations) => {
      for (const [feed, representation] of representations) state.feeds.set(feed, representation);
      state.senateLoadPromise = null;
      const rounds = selectedSenateRounds();
      state.senateRound = rounds.length ? rounds[rounds.length - 1] : null;
      renderSenate();
      renderSenateCount();
      renderEvidence();
      return state.feeds;
    }).catch((error) => {
      state.senateLoadPromise = null;
      setText(mount, "#pr-count-round-badge", "Detailed count unavailable");
      showStatus(`The detailed Senate count could not be loaded: ${error.message}`, "error");
      throw error;
    });
    renderSenateCount();
    return state.senateLoadPromise;
  }

  function applyFilterState(next) {
    const requestedElection = next.election || state.catalogue?.default_election_id;
    if (
      requestedElection
      && requestedElection !== state.electionId
      && state.catalogue?.elections?.some((item) => item.election_id === requestedElection)
    ) {
      void loadElection(requestedElection).then(() => applyFilterState({...next, election: requestedElection}));
      return;
    }
    const previousMapView = state.mapView;
    const previousSenateState = state.senateState;
    state.view = next.view || "overview";
    state.party = next.party || "ALL";
    state.houseState = next.state || "ALL";
    state.mapView = next.mapView || "ALL";
    if (previousMapView && previousMapView !== state.mapView) state.mapCamera = null;
    state.houseSearch = next.search || next.electorate || "";
    state.participationState = next.state || "ALL";
    state.participationChamber = next.chamber || "ALL";
    state.senateState = next.senateState || (next.state !== "ALL" ? next.state : "NSW");
    if (previousSenateState && previousSenateState !== state.senateState) {
      stopCountAnimation();
      state.senateRound = null;
    }
    state.selectedMember = next.member || "";
    if (next.electorate) {
      state.selectedSeat = rows("house_seat_results").find(
        (row) => String(row.contest_name || "").toLowerCase() === String(next.electorate).toLowerCase()
      ) || state.selectedSeat;
      state.selectedHouseMember = state.selectedSeat;
    } else {
      state.selectedSeat = null;
    }
    if (next.member) {
      const selectedName = String(next.member).toLowerCase();
      state.selectedHouseMember = rows("house_seat_results").find((row) =>
        String(row.person_name || row.candidate_name || "").toLowerCase() === selectedName
      ) || state.selectedHouseMember;
      state.selectedSenateMember = rows("senate_composition").find((row) =>
        String(row.person_name || "").toLowerCase() === selectedName
      ) || state.selectedSenateMember;
    } else {
      state.selectedHouseMember = state.selectedSeat;
      state.selectedSenateMember = null;
    }
    populatePartyOptions();
    syncControls();
    if (state.catalogue && state.feeds.size) renderAll();
    renderRoute();
    if (state.view === "senate" && state.catalogue) void ensureSenateDetails();
  }

  function updateFilters(patch, options = {}) {
    if (state.urlStore) return state.urlStore.update(patch, options);
    applyFilterState({
      election: state.electionId,
      view: state.view,
      state: state.houseState,
      party: state.party,
      electorate: state.selectedSeat?.contest_name || "",
      member: state.selectedMember,
      search: state.houseSearch,
      chamber: state.participationChamber,
      senateState: state.senateState,
      mapView: state.mapView,
      ...patch
    });
    return null;
  }

  function renderAll() {
    const election = state.catalogue?.elections?.find((item) => item.election_id === state.electionId) || state.catalogue?.elections?.[0];
    const electionYear = String(election?.election_date || "").slice(0, 4) || "Selected";
    const fullSenateSnapshot = state.electionId === "election_fed_2025_05_03_general";
    const doubleDissolutionResult = state.electionId === "election_fed_2016_07_02_general";
    const voidedWaResult = state.electionId === "election_fed_2013_09_07_general";
    const limited2010BtlSource = state.electionId === "election_fed_2010_08_21_general";
    const electedSenatorCount = rows("senate_composition").length;
    const boundary = state.visualisations?.boundary_geometry || {};
    const release = state.catalogue?.release || {};
    setText(mount, "#pr-release-id", safeReleasePart(release.release_id, 48));
    setText(mount, "#pr-election-label", election ? `${election.election_name} · ${election.election_date}` : "Governed election release");
    setText(mount, "#house-map-title", `${electionYear} electorate map`);
    setText(mount, "#seat-finder-title", `All ${formatNumber(rows("house_seat_results").length)} House seats`);
    setText(mount, "#pr-senate-election-label", `${electionYear} Senate election`);
    setText(mount, "#pr-senate-composition-eyebrow", fullSenateSnapshot ? "48th Parliament" : `${electionYear} election`);
    setText(
      mount,
      "#pr-senate-composition-card-title",
      fullSenateSnapshot ? "Full Senate" : doubleDissolutionResult ? "Full Senate election result" : voidedWaResult ? "Published Senate result" : "Senators elected"
    );
    setText(
      mount,
      "#pr-senate-composition-copy",
      fullSenateSnapshot
        ? "All 76 senators in the governed parliamentary snapshot. Select a state, party or individual member."
        : doubleDissolutionResult
          ? "All 76 senators declared elected at this double-dissolution election. Select a state, party or individual senator."
          : voidedWaResult
            ? `The ${formatNumber(electedSenatorCount)} published 2013 outcomes are retained. Western Australia's six outcomes are marked as later voided; the 2014 WA special election is a separate election and is not substituted here.`
          : limited2010BtlSource
            ? `The ${formatNumber(electedSenatorCount)} senators declared elected at this election. The AEC paper matrix preserves 493,129 below-the-line papers; its official non-ticket aggregate is 493,142, so the 13 unavailable paper records are disclosed rather than fabricated.`
          : `The ${formatNumber(electedSenatorCount)} senators declared elected at this election. Continuing senators are not presented as a historical full-chamber snapshot.`
    );
    setText(
      mount,
      "#pr-metric-senators-note",
      fullSenateSnapshot
        ? "Governed 48th Parliament snapshot"
        : doubleDissolutionResult
          ? "All senators elected at the double dissolution"
          : voidedWaResult
            ? "Published outcomes; WA later voided"
          : "Senators elected at this election"
    );
    setText(
      mount,
      "#pr-map-note",
      `Zoom from 100% to 4,000% using the slider, + and −, a mouse wheel, trackpad, double-click or pinch. Drag to pan. Boundaries: Australian Electoral Commission, effective ${boundary.effective_as_at || "for the selected election"}; simplified for web display without removing a division.`
    );
    populateElectionOptions();
    renderMetrics();
    renderHouseComposition();
    renderPartyChart();
    renderHouseMap();
    renderAnalytics();
    renderSeatCards();
    renderSeatDetail(state.selectedSeat);
    renderSenateTabs();
    renderSenateComposition();
    renderSenateDelegations();
    renderSenate();
    renderSenateCount();
    renderParticipation();
    renderEvidence();
    renderRoute();
  }

  async function loadElection(electionId) {
    if (!state.catalogue?.elections?.some((item) => item.election_id === electionId)) {
      throw new Error(`The selected election is unavailable: ${electionId}`);
    }
    showStatus("Loading the selected governed election…");
    stopCountAnimation();
    const visualisations = await fetchJson(
      fetchImpl,
      contractPath(apiBase, staticBase, electionId)
    );
    const boundaryAsset = visualisations?.boundary_geometry?.derived_geometry?.public_asset_path;
    if (!boundaryAsset) throw new Error("The governed electorate-boundary asset is not registered");
    const [representations, boundaries] = await Promise.all([
      Promise.all(INITIAL_FEEDS.map(async (feed) => [
        feed,
        await fetchJson(fetchImpl, publicPath(feed, "json", electionId, apiBase, staticBase))
      ])),
      fetchJson(fetchImpl, staticBase ? `${staticBase}/${boundaryAsset}` : `/results/data/${boundaryAsset}`)
    ]);
    if (!Array.isArray(boundaries?.features) || !boundaries.features.length) {
      throw new Error("The governed electorate-boundary asset contains no features");
    }
    state.electionId = electionId;
    state.visualisations = visualisations;
    state.registry = createVisualisationRegistry(visualisations);
    for (const container of mount.querySelectorAll("[data-visualisation-id]")) {
      const definition = state.registry.get(container.dataset.visualisationId);
      if (!definition) throw new Error(`The page uses an unregistered visualisation: ${container.dataset.visualisationId}`);
      container.dataset.visualisationStatus = definition.status;
    }
    state.feeds = new Map(representations);
    state.boundaries = boundaries;
    state.mapCamera = null;
    state.selectedSeat = null;
    state.selectedHouseMember = null;
    state.selectedSenateMember = null;
    state.senateRound = null;
    state.senateLoadPromise = null;
    populateElectionOptions();
    showStatus("");
    return state;
  }

  globalElection.addEventListener("change", async () => {
    const electionId = globalElection.value;
    try {
      await loadElection(electionId);
      updateFilters({
        election: electionId,
        state: "ALL",
        party: "ALL",
        search: "",
        electorate: "",
        member: "",
        senateState: "NSW",
        mapView: "ALL"
      }, {push: true});
    } catch (error) {
      showStatus(`The selected election could not be loaded: ${error.message}`, "error");
    }
  });

  houseState.addEventListener("change", () => {
    state.selectedSeat = null;
    state.selectedHouseMember = null;
    state.mapCamera = null;
    updateFilters({state: houseState.value, mapView: houseState.value, electorate: "", member: ""});
  });
  mapState.addEventListener("change", () => {
    state.selectedSeat = null;
    state.selectedHouseMember = null;
    state.mapCamera = null;
    updateFilters({mapView: mapState.value, electorate: "", member: ""});
  });
  mapElectorate.addEventListener("change", () => {
    const seat = rows("house_seat_results").find((row) => row.contest_name === mapElectorate.value);
    if (!seat) return;
    state.selectedSeat = seat;
    state.selectedHouseMember = seat;
    state.mapCamera = null;
    updateFilters({
      view: "house",
      mapView: stateForRow(seat),
      search: "",
      electorate: seat.contest_name || "",
      member: seat.person_name || seat.candidate_name || ""
    }, {push: true});
  });
  houseSearch.addEventListener("input", () => {
    state.selectedSeat = null;
    updateFilters({search: houseSearch.value, electorate: ""});
  });
  participationState.addEventListener("change", () => {
    updateFilters({state: participationState.value});
  });
  participationChamber.addEventListener("change", () => {
    updateFilters({chamber: participationChamber.value});
  });
  globalState.addEventListener("change", () => {
    state.selectedSeat = null;
    state.selectedHouseMember = null;
    state.mapCamera = null;
    updateFilters({
      state: globalState.value,
      mapView: globalState.value,
      electorate: "",
      member: "",
      senateState: globalState.value === "ALL" ? state.senateState : globalState.value
    });
  });
  globalParty.addEventListener("change", () => updateFilters({party: globalParty.value}));
  globalElectorate.addEventListener("input", () => {
    state.selectedSeat = null;
    updateFilters({search: globalElectorate.value, electorate: ""});
  });
  clearFilters.addEventListener("click", () => {
    state.mapCamera = null;
    return updateFilters({election: state.electionId, state: "ALL", party: "ALL", search: "", electorate: "", member: "", chamber: "ALL", senateState: "NSW", mapView: "ALL"});
  });
  countPrevious.addEventListener("click", () => stepSenateRound(-1));
  countNext.addEventListener("click", () => stepSenateRound(1));
  countRound.addEventListener("input", () => {
    stopCountAnimation();
    setSenateRound(Number(countRound.value));
  });
  countPlay.addEventListener("click", () => {
    const environment = mount.ownerDocument?.defaultView || globalThis;
    if (state.senateAnimationTimer) {
      stopCountAnimation();
      return;
    }
    const rounds = selectedSenateRounds();
    if (!rounds.length) return;
    if (state.senateRound === rounds[rounds.length - 1]) setSenateRound(rounds[0]);
    countPlay.textContent = "Pause";
    countPlay.setAttribute("aria-pressed", "true");
    state.senateAnimationTimer = environment.setInterval(() => stepSenateRound(1), Number(countSpeed.value));
  });
  countSpeed.addEventListener("change", () => {
    if (!state.senateAnimationTimer) return;
    stopCountAnimation();
    countPlay.dispatchEvent(new Event("click", {bubbles: true}));
  });
  for (const link of mount.querySelectorAll("[data-view]")) {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      updateFilters({view: link.dataset.view}, {push: true});
      mount.querySelector("#results-main")?.focus({preventScroll: true});
    });
  }

  const ready = (async () => {
    try {
      const environment = options.environment || mount.ownerDocument?.defaultView || globalThis;
      state.catalogue = await fetchJson(
        fetchImpl,
        staticBase ? `${staticBase}/catalogue.json` : `${apiBase}/api/public/v1/feeds`
      );
      const requestedElection = new URL(
        environment.location?.href || "http://localhost/"
      ).searchParams.get("election");
      const electionId = state.catalogue.elections?.some(
        (item) => item.election_id === requestedElection
      ) ? requestedElection : state.catalogue.default_election_id;
      await loadElection(electionId);
      state.urlStore = createUrlStateStore(state.visualisations, environment);
      state.urlStore.subscribe(applyFilterState);
      applyFilterState({...state.urlStore.get(), election: electionId});
      if (state.view === "senate") await ensureSenateDetails();
      showStatus("");
      app.dataset.ready = "true";
      return state;
    } catch (error) {
      app.dataset.ready = "false";
      showStatus(`The public result feeds could not be loaded: ${error.message}`, "error");
      throw error;
    }
  })();

  app.ready = ready;
  app.resultsState = state;
  app.destroy = () => {
    stopCountAnimation();
    state.urlStore?.destroy();
    tooltip.destroy();
  };
  return app;
}
