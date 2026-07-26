(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const state = {
    route: "dashboard",
    status: null,
    jobs: [],
    file: null,
    uploadId: null,
    detection: null,
    currentJobId: null,
    jobPoll: null,
    mappings: [],
    mappingSummary: {},
    mappingFilter: "open",
    mappingType: "all",
    mappingQuery: "",
    mappingPage: 1,
    mappingPageSize: 12,
    activeMapping: null,
    selectedCanonical: null,
    canonicalResults: [],
    validation: null,
    validationFilter: "all",
    syncPreview: null,
    bootstrapJobId: null,
    bootstrapPreview: null,
    explorerCatalogue: null,
    explorerResult: null,
    explorerPage: 1,
    explorerLoading: false,
    explorerAppliedParams: null,
    feedCatalogue: null,
    websitePublication: null,
    serviceOnline: true,
  };

  class ApiError extends Error {
    constructor(message, status = 0, data = null) {
      super(message);
      this.name = "ApiError";
      this.status = status;
      this.data = data;
    }
  }

  const api = {
    async request(path, options = {}) {
      const headers = new Headers(options.headers || {});
      const body = options.body;
      if (body && !(body instanceof FormData) && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
      }
      const response = await fetch(path, {
        credentials: "same-origin",
        ...options,
        headers,
      });
      const contentType = response.headers.get("content-type") || "";
      let data = null;
      if (response.status !== 204) {
        data = contentType.includes("application/json")
          ? await response.json().catch(() => null)
          : await response.text().catch(() => "");
      }
      if (!response.ok) {
        const detail = data?.detail || data?.message || data?.error || (typeof data === "string" ? data : "");
        throw new ApiError(detail || `Request failed (${response.status})`, response.status, data);
      }
      return data;
    },
    get: (path) => api.request(path),
    post: (path, payload) => api.request(path, {
      method: "POST",
      body: payload instanceof FormData ? payload : JSON.stringify(payload || {}),
    }),
    patch: (path, payload) => api.request(path, { method: "PATCH", body: JSON.stringify(payload || {}) }),
    put: (path, payload) => api.request(path, { method: "PUT", body: JSON.stringify(payload || {}) }),
  };

  const icons = {
    check: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m5 12 4 4L19 6"/></svg>',
    warning: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 9v4m0 4h.01M10.3 4.9 2.8 18a1.4 1.4 0 0 0 1.2 2h16a1.4 1.4 0 0 0 1.2-2L13.7 4.9a2 2 0 0 0-3.4 0Z"/></svg>',
    error: '<svg aria-hidden="true" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="m9 9 6 6m0-6-6 6"/></svg>',
    info: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="M12 11v6m0-10h.01M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z"/></svg>',
    arrow: '<svg aria-hidden="true" viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg>',
  };

  const pageLabels = {
    dashboard: "Overview",
    explorer: "Explore & export",
    feeds: "Visualisation feeds",
    website: "Website publication",
    ingest: "Ingest data",
    mappings: "Mapping review",
    validation: "Validate & publish",
    sync: "Google Sheets sync",
  };

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatNumber(value) {
    if (value === null || value === undefined || value === "") return "—";
    const number = Number(value);
    return Number.isFinite(number) ? new Intl.NumberFormat("en-AU").format(number) : String(value);
  }

  function formatBytes(bytes) {
    const number = Number(bytes);
    if (!Number.isFinite(number)) return bytes || "—";
    if (number === 0) return "0 bytes";
    const units = ["bytes", "KB", "MB", "GB", "TB"];
    const index = Math.min(Math.floor(Math.log(number) / Math.log(1024)), units.length - 1);
    const digits = index > 1 ? 1 : 0;
    return `${(number / (1024 ** index)).toFixed(digits)} ${units[index]}`;
  }

  function formatDate(value, includeTime = false) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat("en-AU", includeTime
      ? { dateStyle: "medium", timeStyle: "short" }
      : { dateStyle: "medium" }).format(date);
  }

  function relativeDate(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const seconds = Math.round((date.getTime() - Date.now()) / 1000);
    const unit = Math.abs(seconds) < 60 ? "second"
      : Math.abs(seconds) < 3600 ? "minute"
      : Math.abs(seconds) < 86400 ? "hour" : "day";
    const divisor = unit === "second" ? 1 : unit === "minute" ? 60 : unit === "hour" ? 3600 : 86400;
    return new Intl.RelativeTimeFormat("en", { numeric: "auto" }).format(Math.round(seconds / divisor), unit);
  }

  function labelize(value) {
    return String(value || "")
      .replace(/^adapter_/, "")
      .replaceAll("_", " ")
      .replace(/\b\w/g, (letter) => letter.toUpperCase());
  }

  function setText(selector, value) {
    const element = $(selector);
    if (element) element.textContent = value ?? "—";
  }

  function setServiceStatus(online, message = "Ready") {
    state.serviceOnline = online;
    const dot = $("#sidebar-status-dot");
    dot.classList.toggle("online", online);
    dot.classList.toggle("error", !online);
    setText("#sidebar-status-text", online ? message : "Service unavailable");
    $("#offline-banner").classList.toggle("hidden", online);
  }

  function toast(title, message = "", type = "info", duration = 5000) {
    const element = document.createElement("div");
    element.className = `toast ${type}`;
    element.innerHTML = `${icons[type] || icons.info}<div><strong>${escapeHtml(title)}</strong>${message ? `<small>${escapeHtml(message)}</small>` : ""}</div><button type="button" aria-label="Dismiss">×</button>`;
    $("#toast-region").append(element);
    const close = () => element.remove();
    $("button", element).addEventListener("click", close);
    if (duration) window.setTimeout(close, duration);
  }

  function describeError(error, fallback) {
    if (error instanceof ApiError && error.message) return error.message;
    if (error instanceof TypeError) return "Could not reach the local application service.";
    return fallback || error?.message || "The request could not be completed.";
  }

  function openModal(id) {
    const modal = $(id);
    modal.classList.remove("hidden");
    document.body.style.overflow = "hidden";
    const focusable = $("button, input, textarea, select", modal);
    window.setTimeout(() => focusable?.focus(), 20);
  }

  function closeModal(modal) {
    (typeof modal === "string" ? $(modal) : modal)?.classList.add("hidden");
    if (!$(".modal-backdrop:not(.hidden)")) document.body.style.overflow = "";
  }

  function navigate(route, options = {}) {
    if (!pageLabels[route]) route = "dashboard";
    state.route = route;
    $$(".view").forEach((view) => view.classList.toggle("active", view.dataset.view === route));
    $$(".nav-item").forEach((item) => {
      const active = item.dataset.route === route;
      item.classList.toggle("active", active);
      if (active) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
    setText("#page-label", pageLabels[route]);
    document.body.classList.remove("menu-open");
    $("#menu-button").setAttribute("aria-expanded", "false");
    if (!options.fromHash && location.hash !== `#${route}`) history.replaceState(null, "", `#${route}`);
    window.scrollTo({ top: 0, behavior: "auto" });
    loadRoute(route, options);
  }

  async function loadRoute(route, options = {}) {
    if (route === "dashboard") await loadDashboard(options.silent);
    if (route === "explorer") await loadExplorerRoute(options.silent);
    if (route === "feeds") await loadFeedsRoute(options.silent);
    if (route === "website") await loadWebsiteRoute(options.silent);
    if (route === "ingest") await loadReferenceOptions();
    if (route === "mappings") await loadMappingRoute(options.jobId);
    if (route === "validation") await loadValidationRoute(options.jobId);
    if (route === "sync") await loadSheetsStatus();
  }

  async function loadReferenceOptions() {
    try {
      const data = await api.get("/api/reference-options");
      const authorities = data?.authorities || [];
      const elections = data?.elections || [];
      const authoritySelect = $("#config-authority");
      const electionSelect = $("#config-election");
      const authorityValue = authoritySelect.value;
      const electionValue = electionSelect.value;
      if (authorities.length) authoritySelect.innerHTML = authorities.map((item) => `<option value="${escapeHtml(item.authority_id)}">${escapeHtml(item.authority_name || item.authority_code || item.authority_id)}</option>`).join("");
      if (elections.length) electionSelect.innerHTML = elections.map((item) => `<option value="${escapeHtml(item.election_id)}">${escapeHtml(item.election_name || item.election_id)}${item.election_date ? ` · ${escapeHtml(item.election_date)}` : ""}</option>`).join("");
      if ([...authoritySelect.options].some((item) => item.value === authorityValue)) authoritySelect.value = authorityValue;
      if ([...electionSelect.options].some((item) => item.value === electionValue)) electionSelect.value = electionValue;
    } catch (_) { /* retain the packaged Stage 2 defaults */ }
  }

  function normaliseStatus(data = {}) {
    const database = data.database || data.db || {};
    const metrics = data.metrics || data.counts || database.counts || {};
    const release = data.release || database.release || {};
    const validation = data.validation || release.validation || {};
    const mappings = data.mappings || {};
    return {
      ok: data.ok ?? (data.status ? data.status === "ok" : true),
      appVersion: data.app_version || data.application_version,
      version: database.schema_version || database.version || data.schema_version || data.version,
      path: database.path || data.database_path,
      size: database.size_bytes || database.bytes || data.database_size_bytes,
      sources: metrics.official_sources ?? metrics.sources ?? data.source_count,
      facts: metrics.result_facts ?? metrics.facts ?? data.fact_count,
      mappingOpen: mappings.open ?? metrics.open_mappings ?? data.open_mappings,
      checksPassed: validation.passed ?? validation.pass_count ?? metrics.checks_passed,
      checksTotal: validation.total ?? validation.check_count ?? metrics.checks_total,
      checksFailed: validation.failed ?? validation.fail_count ?? 0,
      warnings: validation.warnings ?? validation.warning_count ?? 0,
      releaseName: release.name || release.release_id || database.release_name,
      election: release.election_name || release.election || data.election_name,
      validatedAt: validation.completed_at || release.validated_at || data.validated_at,
      publication: release.publication_status || release.status || data.publication_status,
    };
  }

  function renderStatus(data) {
    const status = normaliseStatus(data);
    state.status = status;
    setText("#app-version", status.appVersion ? `Application ${status.appVersion}` : "Application ready");
    setText("#db-version", status.version ? `Schema ${status.version}` : "Database ready");
    setText("#db-path", status.path || "Local DuckDB");
    setText("#metric-sources", formatNumber(status.sources));
    setText("#metric-facts", formatNumber(status.facts));
    setText("#metric-checks", status.checksTotal != null ? `${formatNumber(status.checksPassed)} / ${formatNumber(status.checksTotal)}` : "—");
    setText("#metric-check-note", status.checksFailed ? `${formatNumber(status.checksFailed)} blocking failures` : "Blocking validations passed");
    setText("#metric-mappings", formatNumber(status.mappingOpen));
    setText("#metric-mapping-note", status.mappingOpen ? "Requires operator review" : "No unresolved identities");
    setText("#release-name", status.releaseName || (status.version ? `Database release ${status.version}` : "Current local database"));
    setText("#release-election", status.election || "—");
    setText("#release-validated", formatDate(status.validatedAt, true));
    setText("#release-publication", labelize(status.publication || "Unpublished"));
    setText("#release-size", formatBytes(status.size));
    const total = Number(status.checksTotal || 0);
    const pass = Number(status.checksPassed || 0);
    const percent = total ? Math.round((pass / total) * 100) : 0;
    $("#health-ring").style.setProperty("--progress", percent);
    setText("#health-percent", total ? `${percent}%` : "—");
    const badge = $("#release-badge");
    badge.className = `badge ${status.checksFailed ? "danger" : total && pass === total ? "success" : "neutral"}`;
    badge.textContent = status.checksFailed ? "Action required" : total && pass === total ? "Validated" : "Checking";
    const navCount = $("#mapping-nav-count");
    navCount.textContent = formatNumber(status.mappingOpen || 0);
    navCount.classList.toggle("hidden", !Number(status.mappingOpen));
  }

  function normaliseJobs(data) {
    const jobs = Array.isArray(data) ? data : data?.jobs || data?.items || [];
    return jobs.map((item) => ({
      ...item,
      id: item.id || item.job_id || item.import_run_id,
      name: item.name || item.job_name || item.file_name || item.source_file || "Ingestion job",
      source: item.source || item.authority_name || item.authority || item.adapter_id || "—",
      rows: item.rows ?? item.row_count ?? item.processed_rows,
      status: item.status || item.state || "unknown",
      updatedAt: item.updated_at || item.completed_at || item.created_at,
      openMappings: item.open_mappings ?? item.unresolved_mappings ?? 0,
    })).filter((item) => item.id);
  }

  function statusBadge(status) {
    const value = String(status || "unknown").toLowerCase();
    const className = ["completed", "published", "validated", "ready_to_publish"].includes(value) ? "success"
      : ["failed", "validation_failed", "cancelled"].includes(value) ? "danger"
      : ["review_required", "mapping_required", "needs_review"].includes(value) ? "warning"
      : ["running", "queued", "validating", "staging", "transforming"].includes(value) ? "info" : "neutral";
    return `<span class="badge ${className}">${escapeHtml(labelize(value))}</span>`;
  }

  function renderJobs() {
    const body = $("#jobs-table-body");
    if (!state.jobs.length) {
      body.innerHTML = '<tr class="empty-row"><td colspan="6"><div class="empty-state compact"><h3>No ingestion jobs yet</h3><p>Upload an official source file to create the first job.</p></div></td></tr>';
    } else {
      body.innerHTML = state.jobs.slice(0, 10).map((job) => `
        <tr>
          <td><strong>${escapeHtml(job.name)}</strong><br><small>${escapeHtml(String(job.id).slice(0, 14))}</small></td>
          <td>${escapeHtml(labelize(job.source))}</td>
          <td>${formatNumber(job.rows)}</td>
          <td>${statusBadge(job.status)}</td>
          <td title="${escapeHtml(formatDate(job.updatedAt, true))}">${escapeHtml(relativeDate(job.updatedAt))}</td>
          <td><button class="row-action" type="button" data-open-job="${escapeHtml(job.id)}" data-job-status="${escapeHtml(job.status)}">Open</button></td>
        </tr>`).join("");
      $$('[data-open-job]', body).forEach((button) => button.addEventListener("click", () => openJob(button.dataset.openJob, button.dataset.jobStatus)));
    }
    populateJobSelectors();
  }

  async function loadDashboard(silent = false) {
    const results = await Promise.allSettled([api.get("/api/status"), api.get("/api/jobs")]);
    const statusResult = results[0];
    const jobsResult = results[1];
    if (statusResult.status === "fulfilled") {
      renderStatus(statusResult.value || {});
      setServiceStatus(true, "Ready");
    } else {
      setServiceStatus(false);
      if (!silent) console.warn("Status endpoint unavailable", statusResult.reason);
    }
    if (jobsResult.status === "fulfilled") {
      state.jobs = normaliseJobs(jobsResult.value);
      renderJobs();
    } else {
      $("#jobs-table-body").innerHTML = '<tr class="empty-row"><td colspan="6"><div class="empty-state compact"><h3>Jobs unavailable</h3><p>Reconnect to the local application service to inspect ingestion activity.</p></div></td></tr>';
    }
  }

  const explorerColumns = {
    results: [
      { key: 'state', label: 'State' },
      { key: 'contest_name', label: 'Contest', primary: true, secondary: 'reporting_unit' },
      { key: 'subject_name', label: 'Candidate / subject', primary: true, secondary: 'subject_type' },
      { key: 'party_name', label: 'Party', colour: 'party_colour' },
      { key: 'result_type', label: 'Result' },
      { key: 'vote_type', label: 'Vote type' },
      { key: 'reporting_level', label: 'Level' },
      { key: 'votes', label: 'Votes', numeric: true, format: 'number' },
      { key: 'vote_share', label: 'Share', numeric: true, format: 'percent' },
      { key: 'swing', label: 'Swing', numeric: true, format: 'signed_percent' },
    ],
    outcomes: [
      { key: 'state', label: 'State' },
      { key: 'contest_name', label: 'Contest', primary: true, secondary: 'chamber_name' },
      { key: 'candidate_name', label: 'Elected candidate', primary: true, secondary: 'person_name' },
      { key: 'party_name', label: 'Party', colour: 'party_colour' },
      { key: 'outcome_type', label: 'Outcome' },
      { key: 'elected_order', label: 'Order', numeric: true, format: 'number' },
      { key: 'declared_at', label: 'Declared', format: 'date' },
    ],
    participation: [
      { key: 'state', label: 'State' },
      { key: 'contest_name', label: 'Contest', primary: true, secondary: 'reporting_unit' },
      { key: 'chamber_name', label: 'Chamber' },
      { key: 'reporting_level', label: 'Level' },
      { key: 'vote_type', label: 'Vote type' },
      { key: 'measure_type', label: 'Measure' },
      { key: 'integer_value', label: 'Integer value', numeric: true, format: 'number' },
      { key: 'decimal_value', label: 'Decimal value', numeric: true, format: 'decimal' },
      { key: 'value_status', label: 'Status' },
    ],
    count_rounds: [
      { key: 'state', label: 'State' },
      { key: 'contest_name', label: 'Contest', primary: true, secondary: 'round_label' },
      { key: 'round_number', label: 'Round', numeric: true, format: 'number' },
      { key: 'action_type', label: 'Action' },
      { key: 'quota_value', label: 'Quota', numeric: true, format: 'decimal' },
      { key: 'transfer_value', label: 'Transfer value', numeric: true, format: 'decimal' },
      { key: 'candidate_total_rows', label: 'Candidates', numeric: true, format: 'number' },
      { key: 'transfer_rows', label: 'Transfers', numeric: true, format: 'number' },
      { key: 'exhausted_rows', label: 'Exhausted', numeric: true, format: 'number' },
    ],
    count_totals: [
      { key: 'state', label: 'State' },
      { key: 'contest_name', label: 'Contest', primary: true, secondary: 'round_label' },
      { key: 'round_number', label: 'Round', numeric: true, format: 'number' },
      { key: 'candidate_name', label: 'Candidate', primary: true, secondary: 'candidate_count_status' },
      { key: 'party_name', label: 'Party', colour: 'party_colour' },
      { key: 'papers_value', label: 'Papers', numeric: true, format: 'number' },
      { key: 'votes_value', label: 'Votes', numeric: true, format: 'decimal' },
      { key: 'progressive_total', label: 'Progressive total', numeric: true, format: 'decimal' },
    ],
    ballot_datasets: [
      { key: 'state', label: 'State' },
      { key: 'contest_name', label: 'Contest', primary: true, secondary: 'dataset_scope' },
      { key: 'ballot_channel', label: 'Channel' },
      { key: 'row_count', label: 'Anonymous ballots', numeric: true, format: 'number' },
      { key: 'anonymisation_method', label: 'Anonymisation' },
      { key: 'schema_version', label: 'Schema' },
    ],
    contests: [
      { key: 'state', label: 'State' },
      { key: 'contest_name', label: 'Contest', primary: true, secondary: 'official_contest_id' },
      { key: 'chamber_name', label: 'Chamber' },
      { key: 'vacancies', label: 'Vacancies', numeric: true, format: 'number' },
      { key: 'candidacy_count', label: 'Candidates', numeric: true, format: 'number' },
      { key: 'contest_status', label: 'Status' },
      { key: 'uncontested', label: 'Uncontested', format: 'boolean' },
    ],
  };

  function selectOptions(selector, items, valueKey, label, emptyLabel = null) {
    const select = $(selector);
    const prior = select.value;
    const empty = emptyLabel === null ? '' : '<option value="">' + escapeHtml(emptyLabel) + '</option>';
    select.innerHTML = empty + items.map((item) => {
      const value = typeof item === 'string' ? item : item[valueKey];
      const text = typeof item === 'string' ? labelize(item) : label(item);
      return '<option value="' + escapeHtml(value) + '">' + escapeHtml(text) + '</option>';
    }).join('');
    if ([...select.options].some((item) => item.value === prior)) select.value = prior;
  }

  function renderExplorerCatalogue(catalogue) {
    state.explorerCatalogue = catalogue;
    const counts = catalogue.counts || {};
    setText('#explorer-metric-contests', formatNumber(counts.contests));
    setText('#explorer-metric-candidacies', formatNumber(counts.candidacies));
    setText('#explorer-metric-results', formatNumber(counts.results));
    setText('#explorer-metric-ballots', formatNumber(counts.formal_ballots));
    const metadata = catalogue.database || {};
    setText('#app-version', catalogue.application_version ? 'Application ' + catalogue.application_version : 'Application ready');
    setText('#db-version', metadata.schema_version ? 'Schema ' + metadata.schema_version : 'Database ready');
    setText('#db-path', metadata.path || 'Local DuckDB');
    setText('#explorer-release-badge', (catalogue.application_version || 'App') + ' · Schema ' + (metadata.schema_version || '—') + ' · ' + (metadata.release_id || 'active release'));
    selectOptions('#explorer-dataset', catalogue.datasets || [], 'dataset', (item) => item.label);
    selectOptions('#explorer-election', catalogue.elections || [], 'election_id', (item) => item.election_name + ' · ' + item.election_date);
    selectOptions('#explorer-state', catalogue.states || [], null, (item) => item, 'All states and territories');
    selectOptions('#explorer-result-type', catalogue.result_types || [], null, (item) => labelize(item), 'All result types');
    selectOptions('#explorer-vote-type', catalogue.vote_types || [], null, (item) => labelize(item), 'All vote types');
    updateExplorerDependentOptions();
    $('#explorer-export-button').disabled = false;
  }

  function updateExplorerDependentOptions() {
    const catalogue = state.explorerCatalogue;
    if (!catalogue) return;
    const electionId = $('#explorer-election').value;
    const chamberPrior = $('#explorer-chamber').value;
    const chambers = (catalogue.chambers || []).filter((item) => !electionId || item.election_id === electionId);
    selectOptions('#explorer-chamber', chambers, 'chamber_id', (item) => item.chamber_name, 'All chambers');
    if ([...$('#explorer-chamber').options].some((item) => item.value === chamberPrior)) $('#explorer-chamber').value = chamberPrior;
    const chamberId = $('#explorer-chamber').value;
    const stateValue = $('#explorer-state').value;
    const contestPrior = $('#explorer-contest').value;
    const contests = (catalogue.contests || []).filter((item) =>
      (!electionId || item.election_id === electionId)
      && (!chamberId || item.chamber_id === chamberId)
      && (!stateValue || item.state === stateValue));
    selectOptions('#explorer-contest', contests, 'contest_id', (item) => item.contest_name + (item.state ? ' · ' + item.state : ''), 'All contests');
    if ([...$('#explorer-contest').options].some((item) => item.value === contestPrior)) $('#explorer-contest').value = contestPrior;
    const resultsDataset = $('#explorer-dataset').value === 'results';
    $$('[data-explorer-result-filter]').forEach((field) => field.classList.toggle('hidden', !resultsDataset));
  }

  function explorerParams(includePage = true) {
    const params = new URLSearchParams();
    const dataset = $('#explorer-dataset').value || 'results';
    params.set('dataset', dataset);
    for (const pair of [
      ['#explorer-election', 'election_id'],
      ['#explorer-chamber', 'chamber_id'],
      ['#explorer-state', 'state'],
      ['#explorer-contest', 'contest_id'],
    ]) {
      const value = $(pair[0]).value;
      if (value) params.set(pair[1], value);
    }
    if (dataset === 'results') {
      for (const pair of [
        ['#explorer-result-type', 'result_type'],
        ['#explorer-vote-type', 'vote_type'],
        ['#explorer-reporting-level', 'reporting_level'],
      ]) {
        const value = $(pair[0]).value;
        if (value) params.set(pair[1], value);
      }
    }
    const query = $('#explorer-search').value.trim();
    if (query) params.set('q', query);
    if (includePage) {
      params.set('page', String(state.explorerPage));
      params.set('page_size', $('#explorer-page-size').value || '50');
    }
    return params;
  }

  function explorerCell(column, row) {
    const value = row[column.key];
    let displayed = value;
    if (value === null || value === undefined || value === '') displayed = '—';
    else if (column.format === 'number') displayed = formatNumber(value);
    else if (column.format === 'percent') displayed = Number(value).toFixed(2) + '%';
    else if (column.format === 'signed_percent') displayed = (Number(value) > 0 ? '+' : '') + Number(value).toFixed(2) + '%';
    else if (column.format === 'decimal') displayed = new Intl.NumberFormat('en-AU', { maximumFractionDigits: 6 }).format(Number(value));
    else if (column.format === 'date') displayed = formatDate(value, true);
    else if (column.format === 'boolean') displayed = value ? 'Yes' : 'No';
    else if (['result_type', 'vote_type', 'reporting_level', 'subject_type', 'action_type', 'outcome_type', 'contest_status', 'value_status', 'dataset_scope'].includes(column.key)) displayed = labelize(value);
    const className = ((column.numeric ? 'numeric' : '') + (column.primary ? ' primary-cell' : '')).trim();
    if (column.primary) {
      const secondary = row[column.secondary];
      return '<td class="' + className + '" title="' + escapeHtml(value || '') + '"><strong>' + escapeHtml(displayed) + '</strong>' + (secondary ? '<small>' + escapeHtml(labelize(secondary)) + '</small>' : '') + '</td>';
    }
    if (column.colour && row[column.colour]) {
      const candidate = String(row[column.colour]);
      const colour = /^#[0-9a-f]{6}$/i.test(candidate) ? candidate : '#9aa5b1';
      return '<td class="' + className + '"><span class="party-label"><i style="background:' + escapeHtml(colour) + '"></i>' + escapeHtml(displayed) + '</span></td>';
    }
    return '<td class="' + className + '">' + escapeHtml(displayed) + '</td>';
  }

  function renderExplorerRows(result) {
    state.explorerResult = result;
    const columns = explorerColumns[result.dataset] || [];
    setText('#explorer-table-title', result.dataset_label || labelize(result.dataset));
    const first = result.total_rows ? ((result.page - 1) * result.page_size) + 1 : 0;
    const last = Math.min(result.page * result.page_size, result.total_rows);
    setText('#explorer-table-summary', result.total_rows
      ? 'Showing ' + formatNumber(first) + '–' + formatNumber(last) + ' of ' + formatNumber(result.total_rows) + ' current records.'
      : 'No current records match these filters.');
    $('#explorer-table-head').innerHTML = '<tr>' + columns.map((column) =>
      '<th class="' + (column.numeric ? 'numeric' : '') + '">' + escapeHtml(column.label) + '</th>'
    ).join('') + '</tr>';
    const body = $('#explorer-table-body');
    body.innerHTML = result.rows.length
      ? result.rows.map((row) => '<tr>' + columns.map((column) => explorerCell(column, row)).join('') + '</tr>').join('')
      : '<tr class="empty-row"><td colspan="' + Math.max(columns.length, 1) + '"><div class="empty-state compact"><h3>No matching records</h3><p>Broaden or reset the filters and try again.</p></div></td></tr>';
    setText('#explorer-page-label', 'Page ' + formatNumber(result.page) + ' of ' + formatNumber(result.total_pages));
    $('#explorer-prev-button').disabled = result.page <= 1;
    $('#explorer-next-button').disabled = result.page >= result.total_pages;
  }

  async function loadExplorerRows(silent = false) {
    if (!state.explorerCatalogue || state.explorerLoading) return;
    state.explorerLoading = true;
    const params = explorerParams(true);
    $('#explorer-apply-button').disabled = true;
    $('#explorer-table-body').innerHTML = '<tr class="empty-row"><td><div class="empty-inline"><span class="spinner"></span>Querying the active immutable release…</div></td></tr>';
    try {
      const result = await api.get('/api/explorer/query?' + params.toString());
      state.explorerAppliedParams = explorerParams(false);
      renderExplorerRows(result);
      setServiceStatus(true, 'Ready');
    } catch (error) {
      $('#explorer-table-body').innerHTML = '<tr class="empty-row"><td><div class="empty-state compact"><h3>Query unavailable</h3><p>The active release could not be read.</p></div></td></tr>';
      if (!silent) toast('Could not query election data', describeError(error), 'error');
    } finally {
      state.explorerLoading = false;
      $('#explorer-apply-button').disabled = false;
    }
  }

  async function loadExplorerRoute(silent = false) {
    try {
      const catalogue = await api.get('/api/explorer/catalogue');
      renderExplorerCatalogue(catalogue);
      if (!state.explorerResult) state.explorerPage = 1;
      await loadExplorerRows(silent);
    } catch (error) {
      setServiceStatus(false);
      if (!silent) toast('Explorer unavailable', describeError(error), 'error');
    }
  }

  function resetExplorer() {
    const catalogue = state.explorerCatalogue;
    if (!catalogue) return;
    $('#explorer-dataset').value = 'results';
    $('#explorer-election').selectedIndex = 0;
    $('#explorer-state').value = '';
    $('#explorer-result-type').value = '';
    $('#explorer-vote-type').value = '';
    $('#explorer-reporting-level').value = 'contest';
    $('#explorer-search').value = '';
    updateExplorerDependentOptions();
    $('#explorer-chamber').value = '';
    updateExplorerDependentOptions();
    $('#explorer-contest').value = '';
    state.explorerPage = 1;
    loadExplorerRows();
  }

  function exportExplorer() {
    if (!state.explorerAppliedParams) return;
    window.location.assign('/api/explorer/export.csv?' + state.explorerAppliedParams.toString());
    toast('CSV export started', 'The filtered active-release records are being downloaded.', 'success', 3500);
  }

  function feedQuery() {
    const params = new URLSearchParams();
    const election = $('#feeds-election').value;
    const stateValue = $('#feeds-state').value;
    if (election) params.set('election_id', election);
    if (stateValue) params.set('state', stateValue);
    const query = params.toString();
    return query ? '?' + query : '';
  }

  function renderFeedCards() {
    const catalogue = state.feedCatalogue;
    if (!catalogue) return;
    const query = feedQuery();
    $('#feed-cards').innerHTML = (catalogue.feeds || []).map((feed) => {
      const recommendations = (feed.recommended_for || []).map((item) => '<span>' + escapeHtml(item) + '</span>').join('');
      return '<article class="panel feed-card">'
        + '<div class="feed-card-heading"><div><p class="section-label">' + escapeHtml(feed.feed_id) + '</p><h2>' + escapeHtml(feed.title) + '</h2></div><span class="badge neutral">v' + escapeHtml(feed.feed_version) + '</span></div>'
        + '<p class="feed-description">' + escapeHtml(feed.description) + '</p>'
        + '<dl class="feed-meta"><div><dt>Grain</dt><dd>' + escapeHtml(feed.grain) + '</dd></div><div><dt>Fields</dt><dd>' + formatNumber((feed.fields || []).length) + '</dd></div></dl>'
        + '<div class="feed-tags">' + recommendations + '</div>'
        + '<div class="feed-actions">'
        + '<button class="button secondary compact" type="button" data-feed-action="copy" data-feed-url="' + escapeHtml(feed.urls.json + query) + '">Copy JSON URL</button>'
        + '<button class="button secondary compact" type="button" data-feed-action="open" data-feed-url="' + escapeHtml(feed.urls.json + query) + '">Open JSON</button>'
        + '<button class="button primary compact" type="button" data-feed-action="download" data-feed-url="' + escapeHtml(feed.urls.csv + query) + '">Download CSV</button>'
        + '<button class="text-button feed-manifest-link" type="button" data-feed-action="open" data-feed-url="' + escapeHtml(feed.urls.manifest + query) + '">Manifest →</button>'
        + '</div></article>';
    }).join('');
  }

  function renderFeedCatalogue(catalogue) {
    state.feedCatalogue = catalogue;
    const release = catalogue.release || {};
    setText('#feeds-version-badge', 'API ' + (catalogue.api_version || 'v1') + ' · feeds ' + (catalogue.feed_version || '—'));
    setText('#feeds-release-id', release.release_id || 'Active governed release');
    setText('#feeds-release-detail', (release.release_status ? labelize(release.release_status) + ' · ' : '') + (release.activated_at ? 'activated ' + formatDate(release.activated_at, true) : 'checksum-verified local release'));
    setText('#feeds-schema-badge', 'Schema ' + (release.schema_version || '—'));
    setText('#feeds-database-sha', release.database_sha256 || '—');
    const select = $('#feeds-election');
    const prior = select.value;
    select.innerHTML = (catalogue.elections || []).map((election) => '<option value="' + escapeHtml(election.election_id) + '">' + escapeHtml(election.election_name + ' · ' + election.election_date) + '</option>').join('');
    const preferred = prior || catalogue.default_election_id;
    if ([...select.options].some((item) => item.value === preferred)) select.value = preferred;
    renderFeedCards();
  }

  async function loadFeedsRoute(silent = false) {
    try {
      renderFeedCatalogue(await api.get('/api/public/v1/feeds'));
      setServiceStatus(true, 'Ready');
    } catch (error) {
      $('#feed-cards').innerHTML = '<article class="panel feed-card"><div class="empty-state compact"><h3>Publication feeds unavailable</h3><p>The active release or feed catalogue could not be verified.</p></div></article>';
      if (!silent) toast('Visualisation feeds unavailable', describeError(error), 'error');
    }
  }

  function renderWebsitePublication(document) {
    state.websitePublication = document;
    const ready = document?.status === "READY_TO_DEPLOY";
    const current = ready && document.matches_active_database !== false;
    const invalid = document?.status === "INVALID";
    const badge = $("#website-status-badge");
    const currentBadge = $("#website-current-badge");
    badge.className = `badge ${ready ? (current ? "success" : "warning") : invalid ? "warning" : "neutral"}`;
    badge.textContent = ready ? (current ? "Verified" : "Update required") : invalid ? "Invalid" : "Not built";
    currentBadge.className = badge.className;
    currentBadge.textContent = ready ? (current ? "Ready to deploy" : "Older database release") : invalid ? "Verification failed" : "Awaiting build";
    setText("#website-release-id", ready ? document.site_release_id : "No package has been built");
    setText("#website-release-detail", ready
      ? (current ? "The ZIP matches the currently active database release." : "A newer database release is active. Build a replacement website package.")
      : document?.message || "Build the first static package from the active release.");
    setText("#website-database-sha", document?.database_sha256 || "—");
    setText("#website-election-id", document?.election_id || "—");
    setText("#website-file-count", ready ? formatNumber(document.file_count) : "—");
    setText("#website-feed-count", ready ? formatNumber(document.feed_count) : "—");
    setText("#website-export-size", ready ? formatBytes(document.export_size_bytes) : "—");
    setText("#website-export-sha", document?.export_sha256 || "—");
    $("#preview-website-link").classList.toggle("hidden", !ready);
    $("#download-website-link").classList.toggle("hidden", !ready);
    $("#build-website-button").textContent = ready ? (current ? "Rebuild verified package" : "Build updated package") : "Build website package";
    setText("#website-verification-note", ready
      ? `Verified ${formatNumber(document.file_count)} files across ${formatNumber(document.feed_count)} publication feeds. Nothing has been uploaded.`
      : invalid ? "The previous package is not deployable. Rebuild it from the active database." : "No deployable package is currently selected.");
  }

  async function loadWebsiteRoute(silent = false) {
    try {
      renderWebsitePublication(await api.get("/api/site-publication/status"));
    } catch (error) {
      renderWebsitePublication({ status: "INVALID", message: describeError(error) });
      if (!silent) toast("Website publication unavailable", describeError(error), "error");
    }
  }

  async function buildWebsitePublication() {
    const button = $("#build-website-button");
    button.disabled = true;
    button.textContent = "Building and verifying…";
    setText("#website-build-help", "Creating seven fixed feeds, the static site and the checksum manifest. Keep Politica open.");
    try {
      const result = await api.post("/api/site-publication/build", {});
      renderWebsitePublication({ ...result, matches_active_database: true });
      toast("Website package ready", "The verified static website ZIP is ready to preview or download. Nothing was uploaded.", "success", 7000);
    } catch (error) {
      toast("Website package could not be built", describeError(error), "error", 7000);
      await loadWebsiteRoute(true);
    } finally {
      button.disabled = false;
      setText("#website-build-help", "The active database remained read only throughout this operation.");
    }
  }

  async function copyFeedUrl(path) {
    const absolute = new URL(path, window.location.origin).href;
    try {
      await navigator.clipboard.writeText(absolute);
      toast('JSON URL copied', 'Paste it into Observable or another data client.', 'success', 3200);
    } catch (_) {
      window.prompt('Copy this publication URL:', absolute);
    }
  }

  function openJob(jobId, status) {
    state.currentJobId = jobId;
    const value = String(status || "").toLowerCase();
    if (["review_required", "mapping_required", "needs_review"].includes(value)) navigate("mappings", { jobId });
    else if (["completed", "validated", "ready_to_publish", "validation_failed", "published"].includes(value)) navigate("validation", { jobId });
    else {
      navigate("ingest");
      showIngestStep(4);
      pollJob(jobId, true);
    }
  }

  function showIngestStep(step) {
    $$("[data-ingest-stage]").forEach((stage) => stage.classList.toggle("active", Number(stage.dataset.ingestStage) === step));
    $$("#ingest-stepper [data-step]").forEach((item) => {
      const number = Number(item.dataset.step);
      item.classList.toggle("active", number === step);
      item.classList.toggle("complete", number < step);
    });
  }

  async function handleFile(file) {
    if (!file) return;
    const extension = file.name.split(".").pop()?.toLowerCase();
    if (!["csv", "xlsx", "zip"].includes(extension)) {
      toast("Unsupported file", "Choose a CSV, XLSX or ZIP file.", "error");
      return;
    }
    if (file.size > 2 * 1024 ** 3) {
      toast("File is too large", "The maximum supported size is 2 GB.", "error");
      return;
    }
    state.file = file;
    showIngestStep(2);
    setText("#detected-file-name", file.name);
    setText("#detected-file-meta", `${formatBytes(file.size)} · Uploading for local inspection`);
    setText("#detected-file-type", extension.toUpperCase());
    setText("#detected-adapter", "Inspecting headers…");
    setText("#detected-dataset", "—");
    setText("#detected-destination", "—");
    setText("#detected-confidence", "—");
    setText("#detected-rows", "—");
    setText("#detected-columns", "—");
    setText("#detected-sheets", "—");
    $("#preview-table-head").innerHTML = "";
    $("#preview-table-body").innerHTML = '<tr class="empty-row"><td><span class="spinner"></span> Inspecting source…</td></tr>';
    $("#inspect-continue-button").disabled = true;
    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const data = await api.post("/api/imports/detect", form);
      renderDetection(data || {});
      setServiceStatus(true, "Ready");
    } catch (error) {
      $("#preview-table-body").innerHTML = `<tr class="empty-row"><td><div class="empty-state compact"><h3>Inspection failed</h3><p>${escapeHtml(describeError(error))}</p></div></td></tr>`;
      setText("#detected-adapter", "Format not detected");
      const warning = $("#detection-warning");
      warning.classList.remove("hidden");
      setText("#detection-warning-text", describeError(error));
      toast("Could not inspect file", describeError(error), "error");
    }
  }

  function normaliseDetection(data) {
    const detection = data.detection || data.adapter || data.match || data;
    const file = data.file || data.upload || {};
    const preview = data.preview || data.sample || {};
    const stats = data.stats || data.summary || {};
    let rows = preview.rows || preview.data || data.sample_rows || [];
    let columns = preview.columns || preview.headers || data.columns || [];
    if (rows.length && !columns.length && !Array.isArray(rows[0])) columns = Object.keys(rows[0]);
    return {
      uploadId: data.upload_id || file.id || data.id,
      adapterId: detection.adapter_id || detection.id || data.adapter_id,
      adapterName: detection.adapter_name || detection.name || data.adapter_name,
      dataset: detection.dataset_name || detection.dataset_id || detection.dataset || data.dataset,
      destination: detection.destination || detection.target_table || data.destination,
      confidence: detection.confidence ?? data.confidence,
      warnings: detection.warnings || data.warnings || [],
      rows: stats.row_count ?? stats.rows ?? data.row_count,
      columnsCount: stats.column_count ?? stats.columns ?? columns.length,
      itemCount: stats.sheet_count ?? stats.file_count ?? stats.items ?? (preview.sheet ? 1 : 1),
      previewSheet: preview.sheet || preview.file || preview.name,
      previewColumns: columns,
      previewRows: rows,
      encoding: detection.encoding || data.encoding,
      delimiter: detection.delimiter || data.delimiter,
      canonicalCapable: detection.canonical_capable ?? data.canonical_capable ?? false,
      executionMode: detection.execution_mode || data.execution_mode,
      duplicateSource: detection.duplicate_source ?? data.duplicate_source ?? false,
      duplicateRevisions: detection.duplicate_revisions || data.duplicate_revisions || [],
    };
  }

  function renderDetection(data) {
    const detection = normaliseDetection(data);
    state.detection = detection;
    state.uploadId = detection.uploadId;
    setText("#detected-file-meta", `${formatBytes(state.file?.size)}${detection.encoding ? ` · ${detection.encoding}` : ""}${detection.delimiter ? ` · delimiter “${detection.delimiter}”` : ""}`);
    setText("#detected-adapter", detection.adapterName || labelize(detection.adapterId) || "No matching adapter");
    setText("#detected-dataset", labelize(detection.dataset) || "—");
    setText("#detected-destination", detection.destination || "—");
    const confidence = Number(detection.confidence);
    setText("#detected-confidence", Number.isFinite(confidence) ? `${Math.round(confidence <= 1 ? confidence * 100 : confidence)}% match` : "Detected");
    setText("#detected-rows", formatNumber(detection.rows));
    setText("#detected-columns", formatNumber(detection.columnsCount));
    setText("#detected-sheets", formatNumber(detection.itemCount));
    setText("#preview-sheet-label", detection.previewSheet || "");
    renderPreviewTable(detection.previewColumns, detection.previewRows);
    const warnings = Array.isArray(detection.warnings) ? detection.warnings : [detection.warnings];
    const warning = $("#detection-warning");
    warning.classList.toggle("hidden", !warnings.filter(Boolean).length);
    setText("#detection-warning-text", warnings.filter(Boolean).map((item) => item.message || item).join(" "));
    const matched = Boolean(detection.adapterId || detection.adapterName);
    $("#inspect-continue-button").disabled = !detection.uploadId || detection.duplicateSource;
    if (!matched) {
      toast("No adapter matched", "Rows will be preserved in quarantine. Publication remains blocked until a compatible adapter and transformer are added.", "warning", 8000);
    } else if (detection.duplicateSource) {
      toast("Source already registered", "The uploaded bytes exactly match an existing immutable source revision. No duplicate ingestion job will be created.", "warning", 10000);
    } else if (!detection.canonicalCapable) {
      toast("Staging-only format", "This adapter can inspect and stage the file, but no individual-file canonical transformer is registered. Use the full 2025 reproduction action for the included canonical route.", "warning", 9000);
    }
  }

  function renderPreviewTable(columns, rows) {
    const safeRows = Array.isArray(rows) ? rows.slice(0, 20) : [];
    const safeColumns = Array.isArray(columns) ? columns : [];
    if (!safeColumns.length) {
      $("#preview-table-head").innerHTML = "";
      $("#preview-table-body").innerHTML = '<tr class="empty-row"><td><div class="empty-state compact"><h3>No preview available</h3><p>The adapter matched, but no sample rows were returned.</p></div></td></tr>';
      return;
    }
    $("#preview-table-head").innerHTML = `<tr>${safeColumns.map((column) => `<th>${escapeHtml(column)}</th>`).join("")}</tr>`;
    $("#preview-table-body").innerHTML = safeRows.map((row) => `<tr>${safeColumns.map((column, index) => `<td title="${escapeHtml(Array.isArray(row) ? row[index] : row[column])}">${escapeHtml(Array.isArray(row) ? row[index] : row[column])}</td>`).join("")}</tr>`).join("") || `<tr class="empty-row"><td colspan="${safeColumns.length}">No source rows found.</td></tr>`;
  }

  function resetIngestion() {
    if (state.jobPoll) window.clearTimeout(state.jobPoll);
    state.file = null;
    state.uploadId = null;
    state.detection = null;
    state.currentJobId = null;
    $("#file-input").value = "";
    $("#job-config-form").reset();
    $("#config-confirm").checked = false;
    showIngestStep(1);
  }

  async function createJob(event) {
    event.preventDefault();
    if (!state.uploadId) {
      toast("Upload missing", "Select and inspect a file first.", "error");
      showIngestStep(1);
      return;
    }
    if (!$("#config-confirm").checked) return;
    const button = $("#create-job-button");
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span>Creating job…';
    const payload = {
      authority_id: $("#config-authority").value,
      election_id: $("#config-election").value,
      publication_phase: $("#config-phase").value,
      source_url: $("#config-source-url").value.trim() || null,
      operator_note: $("#config-note").value.trim() || null,
      adapter_id: state.detection?.adapterId || null,
    };
    try {
      const response = await api.post(`/api/imports/${encodeURIComponent(state.uploadId)}/jobs`, payload);
      const job = response?.job || response;
      state.currentJobId = job?.id || job?.job_id || job?.import_run_id;
      if (!state.currentJobId) throw new Error("The service did not return a job identifier.");
      showIngestStep(4);
      setText("#run-job-name", job?.name || job?.job_name || state.file?.name || "Ingestion job");
      renderJob(job || { id: state.currentJobId, status: "queued", progress: 0 });
      try {
        const started = await api.post(`/api/jobs/${encodeURIComponent(state.currentJobId)}/run`, {});
        renderJob(started?.job || started || job);
      } catch (runError) {
        if (![409, 423].includes(runError.status)) throw runError;
      }
      pollJob(state.currentJobId);
      toast(
        state.detection?.canonicalCapable ? "Ingestion started" : "Staging started",
        state.detection?.canonicalCapable
          ? "The source is being registered and transformed locally."
          : "The source will be checksum-registered and preserved source-native; publication remains locked without a registered canonical transformer.",
        state.detection?.canonicalCapable ? "success" : "warning",
        8000,
      );
    } catch (error) {
      toast("Could not create job", describeError(error), "error");
    } finally {
      button.disabled = false;
      button.textContent = "Create ingestion job";
    }
  }

  async function reproduceGoverned2025() {
    const button = $("#confirm-reproduce-button");
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span>Creating job…';
    try {
      const response = await api.post("/api/jobs/reproduce-2025", { name: "Reproduce governed AEC 2025 release" });
      const job = response?.job || response;
      state.currentJobId = job?.id || job?.job_id;
      if (!state.currentJobId) throw new Error("The service did not return a reproduction job identifier.");
      closeModal("#reproduce-modal");
      navigate("ingest");
      showIngestStep(4);
      renderJob(job);
      toast("Full reproduction queued", "All 45 local AEC sources will be reprocessed. This may take a long time.", "info", 8000);
      try {
        const started = await api.post(`/api/jobs/${encodeURIComponent(state.currentJobId)}/run`, {});
        renderJob(started?.job || started || job);
        pollJob(state.currentJobId);
      } catch (runError) {
        toast("Job created but not started", `${describeError(runError)} Use “Start job” to try again.`, "error", 8000);
      }
    } catch (error) {
      toast("Could not create reproduction job", describeError(error), "error");
    } finally {
      button.disabled = false;
      button.textContent = "Start full reproduction";
    }
  }

  function resetBootstrapDialog() {
    state.bootstrapJobId = null;
    state.bootstrapPreview = null;
    $("#bootstrap-aec-form").reset();
    $("#bootstrap-state-vacancies").value = "6";
    $("#bootstrap-territory-vacancies").value = "2";
    $("#bootstrap-aec-form").classList.remove("hidden");
    $("#bootstrap-preview-panel").classList.add("hidden");
    $("#bootstrap-confirm").checked = false;
    $("#run-bootstrap-button").disabled = true;
  }

  function openBootstrapDialog() {
    resetBootstrapDialog();
    openModal("#bootstrap-aec-modal");
  }

  function renderBootstrapPreview(preview) {
    state.bootstrapPreview = preview;
    const matches = preview.reference_matches || {};
    const references = preview.reference_counts || {};
    setText("#bootstrap-preview-title", preview.election_name);
    setText("#bootstrap-preview-id", `AEC event ${preview.official_event_id} · ${preview.election_id}`);
    setText("#bootstrap-preview-chambers", (preview.chambers || []).join(" + ") || "—");
    setText("#bootstrap-preview-contests", formatNumber(preview.total_contests));
    setText("#bootstrap-preview-candidates", formatNumber(preview.total_candidates));
    setText("#bootstrap-preview-date", formatDate(preview.election_date));
    setText("#bootstrap-preview-people", formatNumber(matches.people_matched || 0));
    setText("#bootstrap-preview-unmatched-people", formatNumber((matches.people_unmatched || 0) + (matches.people_conflict || 0)));
    setText(
      "#bootstrap-preview-references",
      `${formatNumber(references.people)} people · ${formatNumber(references.parties)} parties · ${formatNumber(references.constituencies)} constituencies`,
    );
    $("#bootstrap-aec-form").classList.add("hidden");
    $("#bootstrap-preview-panel").classList.remove("hidden");
    $("#bootstrap-confirm").checked = false;
    $("#run-bootstrap-button").disabled = true;
  }

  async function previewAecBootstrap(event) {
    event.preventDefault();
    const files = [...$("#bootstrap-files").files];
    if (!files.length) {
      toast("Candidate files required", "Choose the official House and/or Senate Candidates CSV file.", "warning");
      return;
    }
    const button = $("#preview-bootstrap-button");
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span>Checking every row…';
    const form = new FormData();
    files.forEach((file) => form.append("files", file, file.name));
    form.append("election_name", $("#bootstrap-name").value.trim());
    form.append("official_event_id", $("#bootstrap-event").value.trim());
    form.append("election_date", $("#bootstrap-date").value);
    form.append("election_type_code", $("#bootstrap-type").value);
    form.append("publication_phase", $("#bootstrap-phase").value);
    form.append("contest_status", $("#bootstrap-contest-status").value);
    form.append("senate_state_vacancies", $("#bootstrap-state-vacancies").value);
    form.append("senate_territory_vacancies", $("#bootstrap-territory-vacancies").value);
    form.append("senate_whole_chamber", String($("#bootstrap-whole-senate").checked));
    form.append("source_url", $("#bootstrap-source-url").value.trim());
    form.append("operator_note", $("#bootstrap-note").value.trim());
    try {
      const response = await api.post("/api/jobs/bootstrap-aec-election", form);
      const job = response?.job || {};
      state.bootstrapJobId = job.id || job.job_id;
      if (!state.bootstrapJobId || !response?.preview) {
        throw new Error("The service did not return a complete Stage 6 preview.");
      }
      renderBootstrapPreview(response.preview);
      toast("Preview passed", "No database rows changed. Review the summary before starting registration.", "success", 7000);
    } catch (error) {
      toast("Could not preview registration", describeError(error), "error", 9000);
    } finally {
      button.disabled = false;
      button.textContent = "Preview registration";
    }
  }

  async function editAecBootstrap() {
    if (state.bootstrapJobId) {
      try { await api.post(`/api/jobs/${encodeURIComponent(state.bootstrapJobId)}/cancel`, {}); }
      catch (_) { /* A preview cancellation is best-effort and never touches the active release. */ }
    }
    state.bootstrapJobId = null;
    state.bootstrapPreview = null;
    $("#bootstrap-preview-panel").classList.add("hidden");
    $("#bootstrap-aec-form").classList.remove("hidden");
  }

  async function runAecBootstrap() {
    if (!state.bootstrapJobId || !$("#bootstrap-confirm").checked) return;
    const button = $("#run-bootstrap-button");
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span>Creating working copy…';
    try {
      state.currentJobId = state.bootstrapJobId;
      const response = await api.post(`/api/jobs/${encodeURIComponent(state.currentJobId)}/run`, {});
      const job = response?.job || response;
      closeModal("#bootstrap-aec-modal");
      navigate("ingest");
      showIngestStep(4);
      setText("#run-job-name", job?.name || "Register new AEC election");
      renderJob(job);
      pollJob(state.currentJobId);
      toast("Registration started", "The new election is being created and validated in an isolated working copy.", "info", 8000);
    } catch (error) {
      toast("Could not start registration", describeError(error), "error", 9000);
      button.disabled = false;
      button.textContent = "Start registration";
    }
  }

  function normaliseJob(data = {}) {
    const job = data.job || data;
    const progressRaw = job.progress_percent ?? job.progress ?? 0;
    const progress = Number(progressRaw) <= 1 && Number(progressRaw) > 0 ? Number(progressRaw) * 100 : Number(progressRaw || 0);
    return {
      ...job,
      id: job.id || job.job_id || job.import_run_id,
      name: job.name || job.job_name || job.file_name || "Ingestion job",
      status: job.status || job.state || "queued",
      progress: Math.max(0, Math.min(100, progress)),
      phase: job.phase || job.current_phase || "register",
      phases: job.phases || job.steps,
      message: job.progress_message || job.message,
      logs: job.log_lines || job.logs || job.log,
      unresolvedMappings: job.unresolved_mappings ?? job.open_mappings ?? 0,
      validationFailures: job.validation_failures ?? job.blocking_failures ?? 0,
    };
  }

  function renderJob(data) {
    const job = normaliseJob(data);
    state.currentJobData = job;
    if (job.id) state.currentJobId = job.id;
    setText("#run-job-name", job.name);
    setText("#run-progress-percent", `${Math.round(job.progress)}%`);
    setText("#run-progress-label", job.message || labelize(job.phase) || labelize(job.status));
    $("#run-progress-bar").style.width = `${job.progress}%`;
    const badge = $("#run-status-badge");
    const badgeMarkup = statusBadge(job.status);
    const holder = document.createElement("div");
    holder.innerHTML = badgeMarkup;
    badge.className = holder.firstElementChild.className;
    badge.textContent = holder.firstElementChild.textContent;
    const statusValue = String(job.status).toLowerCase();
    const successfulTerminal = ["validated", "published", "ready_to_publish"].includes(statusValue)
      || (statusValue === "completed" && job.progress >= 100);
    const phaseOrder = ["register", "stage", "transform", "validate"];
    let currentIndex = phaseOrder.findIndex((phase) => String(job.phase).toLowerCase().includes(phase));
    if (currentIndex < 0) currentIndex = Math.floor((job.progress / 100) * phaseOrder.length);
    const phaseData = Array.isArray(job.phases) ? job.phases : [];
    $$("#run-phase-list li").forEach((element, index) => {
      const provided = phaseData.find((item) => (item.id || item.phase || item.name) === element.dataset.phase);
      let status = provided?.status || (index < currentIndex ? "complete" : index === currentIndex ? "active" : "waiting");
      if (successfulTerminal) status = "complete";
      if (statusValue === "validation_failed") status = index < 3 ? "complete" : "failed";
      if (["completed", "done", "passed", "success"].includes(status)) status = "complete";
      if (["running", "in_progress"].includes(status)) status = "active";
      element.classList.toggle("complete", status === "complete");
      element.classList.toggle("active", status === "active");
      element.classList.toggle("failed", status === "failed");
      $("em", element).textContent = status === "complete" ? "Complete" : status === "active" ? "Running" : status === "failed" ? "Failed" : "Waiting";
    });
    const logs = Array.isArray(job.logs) ? job.logs.join("\n") : job.logs;
    if (logs) setText("#run-log", logs);
    const terminal = ["ready", "completed", "validated", "published", "ready_to_publish", "failed", "validation_failed", "cancelled", "review_required", "mapping_required", "needs_review"].includes(statusValue);
    $("#cancel-job-button").disabled = ["running", "publishing", "published", "cancelled"].includes(statusValue);
    const resume = $("#resume-job-button");
    resume.classList.toggle("hidden", !["ready", "failed"].includes(statusValue));
    resume.textContent = statusValue === "failed" ? "Resume from checkpoint" : "Start job";
    $("#review-mappings-button").classList.toggle("hidden", !job.unresolvedMappings && !["review_required", "mapping_required", "needs_review"].includes(String(job.status).toLowerCase()));
    $("#review-validation-button").classList.toggle("hidden", !["completed", "validated", "ready_to_publish", "validation_failed", "published"].includes(statusValue));
    renderFormatReview(job);
    return { job, terminal };
  }

  function renderFormatReview(job) {
    const panel = $("#format-review-panel");
    const datasets = (job.datasets || []).filter((item) => !item.detection?.selection);
    const needed = String(job.status).toLowerCase() === "needs_review" && datasets.length;
    panel.classList.toggle("hidden", !needed);
    if (!needed) return;
    $("#format-review-list").innerHTML = datasets.map((dataset) => {
      const candidates = dataset.detection?.candidates || [];
      const options = candidates.map((candidate) => `<option value="${escapeHtml(`${candidate.adapter_id}|||${candidate.dataset_key}`)}">${escapeHtml(labelize(candidate.dataset_key))} · ${escapeHtml(candidate.destination || candidate.adapter_id)}</option>`).join("");
      return `<div class="format-choice" data-dataset-id="${escapeHtml(dataset.dataset_id)}"><div><strong>${escapeHtml(dataset.virtual_name || dataset.original_name || dataset.dataset_id)}</strong><small>${formatNumber(dataset.row_count)} rows · ${formatNumber(dataset.headers?.length)} columns</small></div>${options ? `<select aria-label="Source format"><option value="">Choose a compatible format…</option>${options}</select>` : '<small>No registered adapter accepts these headers. Rows remain quarantined and cannot be published.</small>'}</div>`;
    }).join("");
    const choices = $$(".format-choice select", panel);
    const update = () => { $("#save-format-button").disabled = choices.length !== datasets.length || choices.some((select) => !select.value); };
    choices.forEach((select) => select.addEventListener("change", update));
    update();
  }

  async function saveFormatChoices() {
    if (!state.currentJobId) return;
    const button = $("#save-format-button");
    button.disabled = true;
    button.textContent = "Saving choices…";
    try {
      let job = state.currentJobData;
      for (const row of $$(".format-choice", $("#format-review-list"))) {
        const select = $("select", row);
        if (!select?.value) continue;
        const [adapterId, datasetKey] = select.value.split("|||");
        const response = await api.put(`/api/jobs/${encodeURIComponent(state.currentJobId)}/datasets/${encodeURIComponent(row.dataset.datasetId)}`, { adapter_id: adapterId, dataset_key: datasetKey });
        job = response?.job || response;
      }
      const rendered = renderJob(job || {});
      toast("Formats confirmed", "The operator selections are recorded in the job audit trail.", "success");
      if (String(rendered.job.status).toLowerCase() === "mapping_required") navigate("mappings", { jobId: state.currentJobId });
      else {
        const started = await api.post(`/api/jobs/${encodeURIComponent(state.currentJobId)}/run`, {});
        renderJob(started?.job || started || job);
        pollJob(state.currentJobId);
      }
    } catch (error) {
      toast("Could not save format choices", describeError(error), "error");
    } finally {
      button.textContent = "Save format choices";
      button.disabled = false;
    }
  }

  async function pollJob(jobId, immediate = false) {
    if (state.jobPoll) window.clearTimeout(state.jobPoll);
    if (!jobId) return;
    const check = async () => {
      try {
        const response = await api.get(`/api/jobs/${encodeURIComponent(jobId)}`);
        const rendered = renderJob(response?.job || response || {});
        if (!rendered.terminal && state.currentJobId === jobId) state.jobPoll = window.setTimeout(check, 1400);
        else {
          await refreshJobs();
          if (["completed", "validated", "ready_to_publish"].includes(String(rendered.job.status).toLowerCase())) toast("Ingestion complete", "Review the validation evidence before publication.", "success");
          if (rendered.job.unresolvedMappings) toast("Mapping review required", `${formatNumber(rendered.job.unresolvedMappings)} source labels need a decision.`, "info");
        }
      } catch (error) {
        setText("#run-progress-label", "Waiting to reconnect…");
        state.jobPoll = window.setTimeout(check, 3500);
      }
    };
    if (immediate) await check();
    else state.jobPoll = window.setTimeout(check, 500);
  }

  async function cancelJob() {
    if (!state.currentJobId || !window.confirm("Cancel this ingestion job? Staged work will be retained for audit, but no canonical data will be published.")) return;
    try {
      const response = await api.post(`/api/jobs/${encodeURIComponent(state.currentJobId)}/cancel`, {});
      renderJob(response?.job || response || { status: "cancelled", progress: 0 });
      toast("Job cancelled", "No canonical data was published.", "info");
    } catch (error) {
      toast("Could not cancel job", describeError(error), "error");
    }
  }

  async function runCurrentJob() {
    if (!state.currentJobId) return;
    const button = $("#resume-job-button");
    button.disabled = true;
    try {
      const response = await api.post(`/api/jobs/${encodeURIComponent(state.currentJobId)}/run`, {});
      renderJob(response?.job || response || {});
      pollJob(state.currentJobId);
      toast("Job started", "Execution will resume from its last safe checkpoint.", "success");
    } catch (error) {
      toast("Job could not start", describeError(error), "error");
    } finally {
      button.disabled = false;
    }
  }

  async function refreshJobs() {
    try {
      state.jobs = normaliseJobs(await api.get("/api/jobs"));
      renderJobs();
    } catch (_) { /* dashboard handles service state */ }
  }

  function populateJobSelectors() {
    const mapping = $("#mapping-job-select");
    const validation = $("#validation-job-select");
    const mappingValue = mapping.value;
    const validationValue = validation.value;
    mapping.innerHTML = '<option value="">Select a job…</option>' + state.jobs.map((job) => `<option value="${escapeHtml(job.id)}">${escapeHtml(job.name)} · ${escapeHtml(labelize(job.status))}</option>`).join("");
    validation.innerHTML = '<option value="current">Current database release</option>' + state.jobs.map((job) => `<option value="${escapeHtml(job.id)}">${escapeHtml(job.name)} · ${escapeHtml(labelize(job.status))}</option>`).join("");
    if ([...mapping.options].some((option) => option.value === mappingValue)) mapping.value = mappingValue;
    if ([...validation.options].some((option) => option.value === validationValue)) validation.value = validationValue;
  }

  async function loadMappingRoute(jobId) {
    if (!state.jobs.length) await refreshJobs();
    if (jobId) $("#mapping-job-select").value = jobId;
    else if (state.currentJobId && [...$("#mapping-job-select").options].some((option) => option.value === state.currentJobId)) $("#mapping-job-select").value = state.currentJobId;
    const selected = $("#mapping-job-select").value;
    if (selected) await loadMappings(selected);
  }

  function normaliseMappings(data) {
    const issues = Array.isArray(data) ? data : data?.issues || data?.mappings || data?.items || [];
    state.mappingSummary = data?.summary || {};
    return issues.map((item, index) => {
      const suggestion = item.suggested || item.suggestion || item.best_match || {};
      const resolution = item.resolution || item.resolved_to || {};
      return {
        ...item,
        id: item.id || item.issue_id || item.mapping_id || `issue-${index}`,
        sourceLabel: item.source_label || item.label || item.raw_value || "—",
        type: item.entity_type || item.type || item.canonical_type || "unknown",
        context: item.context || item.source_context || item.field_name || "—",
        status: item.status || item.match_status || (resolution.id ? "resolved" : "unmatched"),
        confidence: item.confidence ?? suggestion.confidence,
        suggestion: {
          id: suggestion.id || suggestion.entity_id || suggestion.canonical_id,
          label: suggestion.label || suggestion.name || suggestion.display_name,
          detail: suggestion.detail || suggestion.context || suggestion.identifier,
        },
        resolution: {
          id: resolution.id || resolution.entity_id || resolution.canonical_id || item.resolved_entity_id,
          label: resolution.label || resolution.name || resolution.display_name || item.resolved_label,
        },
        candidates: item.candidates || item.suggestions || [],
      };
    });
  }

  async function loadMappings(jobId) {
    state.currentJobId = jobId;
    $("#mapping-table-body").innerHTML = '<tr class="empty-row"><td colspan="7"><div class="empty-inline"><span class="spinner"></span>Loading mapping issues…</div></td></tr>';
    try {
      const data = await api.get(`/api/jobs/${encodeURIComponent(jobId)}/mappings`);
      state.mappings = normaliseMappings(data || {});
      state.mappingPage = 1;
      renderMappings();
    } catch (error) {
      state.mappings = [];
      $("#mapping-table-body").innerHTML = `<tr class="empty-row"><td colspan="7"><div class="empty-state compact"><h3>Mapping queue unavailable</h3><p>${escapeHtml(describeError(error))}</p></div></td></tr>`;
      toast("Could not load mappings", describeError(error), "error");
    }
  }

  function isResolved(issue) {
    return ["resolved", "matched", "provisional_match", "not_applicable"].includes(String(issue.status).toLowerCase()) || Boolean(issue.resolution?.id);
  }

  function renderMappings() {
    const resolved = state.mappings.filter(isResolved).length;
    const open = state.mappings.length - resolved;
    setText("#mapping-open-count", formatNumber(state.mappingSummary.open ?? open));
    setText("#mapping-resolved-count", formatNumber(state.mappingSummary.resolved ?? resolved));
    setText("#mapping-total-count", formatNumber(state.mappingSummary.total ?? state.mappings.length));
    setText("#metric-mappings", formatNumber(state.mappingSummary.open ?? open));
    const navCount = $("#mapping-nav-count");
    navCount.textContent = formatNumber(state.mappingSummary.open ?? open);
    navCount.classList.toggle("hidden", !(state.mappingSummary.open ?? open));
    $("#mapping-complete-bar").classList.toggle("hidden", open !== 0 || !state.currentJobId || !state.mappings.length);
    let filtered = state.mappings.filter((issue) => {
      const resolvedItem = isResolved(issue);
      if (state.mappingFilter === "open" && resolvedItem) return false;
      if (state.mappingFilter === "resolved" && !resolvedItem) return false;
      if (state.mappingType !== "all" && issue.type.toLowerCase() !== state.mappingType) return false;
      if (state.mappingQuery && !`${issue.sourceLabel} ${issue.context} ${issue.suggestion?.label || ""}`.toLowerCase().includes(state.mappingQuery)) return false;
      return true;
    });
    const pageCount = Math.max(1, Math.ceil(filtered.length / state.mappingPageSize));
    state.mappingPage = Math.min(state.mappingPage, pageCount);
    const start = (state.mappingPage - 1) * state.mappingPageSize;
    const items = filtered.slice(start, start + state.mappingPageSize);
    const body = $("#mapping-table-body");
    if (!items.length) {
      body.innerHTML = `<tr class="empty-row"><td colspan="7"><div class="empty-state compact"><h3>${state.mappings.length ? "No matching issues" : "No mapping issues"}</h3><p>${state.mappings.length ? "Adjust the filters to see other labels." : "This job has no source labels requiring review."}</p></div></td></tr>`;
    } else {
      body.innerHTML = items.map((issue) => {
        const resolvedItem = isResolved(issue);
        const canonical = resolvedItem ? issue.resolution : issue.suggestion;
        const confidenceRaw = Number(issue.confidence);
        const confidence = Number.isFinite(confidenceRaw) ? Math.round(confidenceRaw <= 1 ? confidenceRaw * 100 : confidenceRaw) : null;
        return `<tr>
          <td><strong>${escapeHtml(issue.sourceLabel)}</strong><br><small>${escapeHtml(issue.field_name || issue.source_field || "Source value")}</small></td>
          <td><span class="entity-chip">${escapeHtml(labelize(issue.type))}</span></td>
          <td title="${escapeHtml(typeof issue.context === "object" ? JSON.stringify(issue.context) : issue.context)}">${escapeHtml(typeof issue.context === "object" ? Object.values(issue.context).filter(Boolean).join(" · ") : issue.context)}</td>
          <td>${canonical?.label ? `<strong>${escapeHtml(canonical.label)}</strong>${canonical.detail ? `<br><small>${escapeHtml(canonical.detail)}</small>` : ""}` : "No suggestion"}</td>
          <td>${confidence != null ? `<div class="confidence"><span class="confidence-track"><span style="width:${Math.max(0, Math.min(100, confidence))}%"></span></span><small>${confidence}%</small></div>` : "—"}</td>
          <td>${resolvedItem ? '<span class="badge success">Resolved</span>' : statusBadge(issue.status)}</td>
          <td><button class="row-action" type="button" data-resolve-mapping="${escapeHtml(issue.id)}">${resolvedItem ? "Change" : issue.suggestion?.id ? "Review" : "Resolve"}</button></td>
        </tr>`;
      }).join("");
      $$('[data-resolve-mapping]', body).forEach((button) => button.addEventListener("click", () => openMappingModal(button.dataset.resolveMapping)));
    }
    setText("#mapping-result-count", `${formatNumber(filtered.length)} item${filtered.length === 1 ? "" : "s"}`);
    setText("#mapping-page-label", `Page ${state.mappingPage} of ${pageCount}`);
    $("#mapping-prev").disabled = state.mappingPage <= 1;
    $("#mapping-next").disabled = state.mappingPage >= pageCount;
  }

  async function openMappingModal(issueId) {
    const issue = state.mappings.find((item) => String(item.id) === String(issueId));
    if (!issue) return;
    state.activeMapping = issue;
    state.selectedCanonical = issue.resolution?.id || null;
    setText("#mapping-modal-title", `Match ${labelize(issue.type)}`);
    setText("#mapping-modal-source", `Source label: “${issue.sourceLabel}”`);
    $("#canonical-search").value = "";
    const initial = issue.candidates.length ? issue.candidates : issue.suggestion?.id ? [issue.suggestion] : [];
    state.canonicalResults = initial;
    renderCanonicalResults(initial);
    openModal("#mapping-modal");
    if (!initial.length) await searchCanonical("");
  }

  function normaliseCanonical(items) {
    return (Array.isArray(items) ? items : items?.items || items?.records || items?.results || []).map((item) => ({
      id: item.id || item.entity_id || item.canonical_id || item.person_id || item.party_id || item.constituency_id,
      label: item.label || item.name || item.display_name || item.full_name || item.party_name || item.constituency_name,
      detail: item.detail || item.context || item.secondary_label || item.identifier || item.abbreviation || item.jurisdiction,
    })).filter((item) => item.id);
  }

  function renderCanonicalResults(items) {
    const records = normaliseCanonical(items);
    state.canonicalResults = records;
    const container = $("#canonical-results");
    if (!records.length) {
      container.innerHTML = '<div class="empty-state compact"><h3>No canonical records found</h3><p>Try another search. Do not create a record from the source label here.</p></div>';
      $("#save-mapping-button").disabled = true;
      return;
    }
    container.innerHTML = records.map((record) => `<label class="canonical-option ${String(record.id) === String(state.selectedCanonical) ? "selected" : ""}"><input type="radio" name="canonical-record" value="${escapeHtml(record.id)}" ${String(record.id) === String(state.selectedCanonical) ? "checked" : ""}><span><strong>${escapeHtml(record.label)}</strong><small>${escapeHtml(record.detail || record.id)}</small></span></label>`).join("");
    $$('input[name="canonical-record"]', container).forEach((input) => input.addEventListener("change", () => {
      state.selectedCanonical = input.value;
      $$(".canonical-option", container).forEach((label) => label.classList.toggle("selected", $("input", label).checked));
      $("#save-mapping-button").disabled = false;
    }));
    $("#save-mapping-button").disabled = !state.selectedCanonical;
  }

  let canonicalSearchTimer;
  async function searchCanonical(query) {
    if (!state.activeMapping) return;
    $("#canonical-results").innerHTML = '<div class="loading-block"><span class="spinner"></span>Searching canonical records…</div>';
    try {
      const type = encodeURIComponent(state.activeMapping.type.toLowerCase());
      const data = await api.get(`/api/canonical/${type}?q=${encodeURIComponent(query)}&limit=30`);
      renderCanonicalResults(data);
    } catch (error) {
      $("#canonical-results").innerHTML = `<div class="empty-state compact"><h3>Search unavailable</h3><p>${escapeHtml(describeError(error))}</p></div>`;
    }
  }

  async function saveMapping(notApplicable = false) {
    if (!state.activeMapping || !state.currentJobId) return;
    const button = $("#save-mapping-button");
    button.disabled = true;
    const selected = state.canonicalResults.find((item) => String(item.id) === String(state.selectedCanonical));
    const payload = notApplicable
      ? { resolution_type: "not_applicable", canonical_id: null, resolved_by: "Local operator" }
      : { resolution_type: "matched", canonical_id: state.selectedCanonical, resolved_by: "Local operator" };
    try {
      const data = await api.patch(`/api/jobs/${encodeURIComponent(state.currentJobId)}/mappings/${encodeURIComponent(state.activeMapping.id)}`, payload);
      const index = state.mappings.findIndex((item) => item.id === state.activeMapping.id);
      if (data?.issue || data?.mapping) state.mappings[index] = normaliseMappings({ issues: [data.issue || data.mapping] })[0];
      else if (notApplicable) {
        state.mappings[index] = { ...state.mappings[index], status: "resolved", resolution: { id: null, label: "Not applicable" } };
      } else {
        state.mappings[index] = { ...state.mappings[index], status: "matched", resolution: selected || { id: state.selectedCanonical, label: state.selectedCanonical } };
      }
      closeModal("#mapping-modal");
      renderMappings();
      toast(notApplicable ? "Marked not applicable" : "Mapping saved", notApplicable ? "The decision is recorded in the job audit trail." : "The canonical resolution is recorded for this job.", "success");
    } catch (error) {
      toast("Could not save mapping", describeError(error), "error");
      button.disabled = false;
    }
  }

  async function continueMappedJob() {
    if (!state.currentJobId) return;
    try {
      const response = await api.post(`/api/jobs/${encodeURIComponent(state.currentJobId)}/run`, {});
      navigate("ingest");
      showIngestStep(4);
      renderJob(response?.job || response || {});
      pollJob(state.currentJobId);
    } catch (error) {
      toast("Job cannot continue", describeError(error), "error");
    }
  }

  async function loadValidationRoute(jobId) {
    if (!state.jobs.length) await refreshJobs();
    const select = $("#validation-job-select");
    if (jobId && [...select.options].some((option) => option.value === jobId)) select.value = jobId;
    else if (state.currentJobId && [...select.options].some((option) => option.value === state.currentJobId)) select.value = state.currentJobId;
    await loadValidation(select.value || "current");
  }

  function normaliseValidation(data = {}) {
    const report = data.validation || data.report || data;
    const checks = report.checks || report.results || report.rules || [];
    const normalisedChecks = (Array.isArray(checks) ? checks : []).map((item, index) => ({
      id: item.id || item.rule_id || item.validation_rule_id || `check-${index}`,
      name: item.name || item.rule_name || item.label || item.id || "Validation check",
      description: item.description || item.message || item.detail || "",
      status: String(item.status || item.result || (item.passed === true ? "pass" : item.passed === false ? "fail" : "warning")).toLowerCase(),
      blocking: item.blocking ?? (item.severity ? item.severity === "blocking" : true),
      count: item.count ?? item.failure_count,
    }));
    const count = (statuses) => normalisedChecks.filter((item) => statuses.includes(item.status)).length;
    const passed = report.passed ?? report.pass_count ?? count(["pass", "passed", "success"]);
    const warnings = report.warnings ?? report.warning_count ?? count(["warning", "warn"]);
    const failed = report.failed ?? report.fail_count ?? count(["fail", "failed", "error"]);
    return {
      ...report,
      checks: normalisedChecks,
      passed: Number(passed || 0),
      warnings: Number(warnings || 0),
      failed: Number(failed || 0),
      total: report.total ?? report.check_count ?? normalisedChecks.length,
      completedAt: report.completed_at || report.validated_at || report.created_at,
      openMappings: report.open_mappings ?? report.unresolved_mappings ?? 0,
      includedJobs: report.included_jobs ?? report.job_count ?? (state.currentJobId ? 1 : 0),
      canPublish: report.can_publish ?? report.publishable ?? (Number(failed || 0) === 0 && Number(report.open_mappings ?? 0) === 0 && normalisedChecks.length > 0),
    };
  }

  async function loadValidation(target = "current") {
    const container = $("#validation-checks");
    container.innerHTML = '<div class="loading-block"><span class="spinner"></span>Loading checks…</div>';
    state.currentJobId = target === "current" ? null : target;
    try {
      const path = target === "current" ? "/api/validation" : `/api/jobs/${encodeURIComponent(target)}/validation`;
      const data = await api.get(path);
      state.validation = normaliseValidation(data || {});
      renderValidation();
    } catch (error) {
      state.validation = null;
      container.innerHTML = `<div class="empty-state"><h3>Validation report unavailable</h3><p>${escapeHtml(describeError(error))}</p></div>`;
      setText("#validation-title", "Could not load validation evidence");
      setText("#validation-description", "Reconnect to the local application service and try again.");
      $("#publish-button").disabled = true;
    }
  }

  function renderValidation() {
    const report = state.validation;
    const allPassed = report.failed === 0 && report.checks.length > 0;
    setText("#validation-pass-count", formatNumber(report.passed));
    setText("#validation-warn-count", formatNumber(report.warnings));
    setText("#validation-fail-count", formatNumber(report.failed));
    setText("#validation-title", report.failed ? "Blocking checks require attention" : allPassed ? "All blocking checks passed" : "Validation report is incomplete");
    setText("#validation-description", report.failed
      ? `${formatNumber(report.failed)} blocking check${report.failed === 1 ? "" : "s"} failed. Publication remains locked.`
      : `${formatNumber(report.passed)} checks passed${report.warnings ? ` with ${formatNumber(report.warnings)} documented warning${report.warnings === 1 ? "" : "s"}` : ""}${report.completedAt ? ` · ${formatDate(report.completedAt, true)}` : ""}.`);
    const icon = $("#validation-hero-icon");
    icon.className = `validation-icon ${report.failed ? "danger" : report.warnings ? "warning" : allPassed ? "success" : "pending"}`;
    icon.innerHTML = report.failed ? icons.error : allPassed ? icons.check : icons.warning;
    setText("#publish-job-count", formatNumber(report.includedJobs));
    setText("#publish-open-mappings", formatNumber(report.openMappings));
    setText("#publish-blocking-failures", formatNumber(report.failed));
    const publish = $("#publish-button");
    publish.disabled = !report.canPublish;
    setText("#publish-help", report.canPublish ? "Ready to create a governed, immutable release." : report.openMappings ? "Resolve every open mapping before publication." : "Publication unlocks when every blocking check passes.");
    renderValidationChecks();
  }

  function checkStatus(item) {
    if (["pass", "passed", "success"].includes(item.status)) return "pass";
    if (["fail", "failed", "error"].includes(item.status)) return "fail";
    return "warning";
  }

  function renderValidationChecks() {
    const report = state.validation;
    if (!report) return;
    const checks = report.checks.filter((item) => {
      const status = checkStatus(item);
      if (state.validationFilter === "blocking") return item.blocking;
      if (state.validationFilter === "warning") return status === "warning";
      return true;
    });
    const container = $("#validation-checks");
    if (!checks.length) {
      container.innerHTML = '<div class="empty-state"><h3>No checks in this view</h3><p>Select another filter to see the complete validation report.</p></div>';
      return;
    }
    container.innerHTML = checks.map((item) => {
      const status = checkStatus(item);
      return `<div class="validation-check" data-check-kind="${status}" data-blocking="${item.blocking}">
        <span class="check-icon ${status}">${status === "pass" ? icons.check : status === "fail" ? icons.error : icons.warning}</span>
        <div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.description || (status === "pass" ? "Check passed." : "Review validation evidence."))}</small></div>
        <em>${item.count != null ? formatNumber(item.count) : item.blocking ? "Blocking" : "Advisory"}</em>
      </div>`;
    }).join("");
  }

  async function rerunValidation() {
    const button = $("#rerun-validation-button");
    button.disabled = true;
    const target = $("#validation-job-select").value || "current";
    try {
      const path = target === "current" ? "/api/validation/run" : `/api/jobs/${encodeURIComponent(target)}/validate`;
      const data = await api.post(path, {});
      state.validation = normaliseValidation(data || {});
      renderValidation();
      toast("Validation complete", "The evidence report has been refreshed.", state.validation.failed ? "error" : "success");
    } catch (error) {
      toast("Validation could not run", describeError(error), "error");
    } finally {
      button.disabled = false;
    }
  }

  async function publishSnapshot() {
    if (!state.validation?.canPublish || !$("#publication-confirm").checked) return;
    const button = $("#confirm-publish-button");
    button.disabled = true;
    button.textContent = "Publishing…";
    const payload = { release_note: $("#publication-note").value.trim() || null };
    try {
      const path = state.currentJobId ? `/api/jobs/${encodeURIComponent(state.currentJobId)}/publish` : "/api/publications";
      const data = await api.post(path, payload);
      closeModal("#publish-modal");
      const release = data?.publication || data?.snapshot || data;
      toast("Snapshot published", release?.id || release?.publication_snapshot_id ? `Release ${release.id || release.publication_snapshot_id} is now available.` : "The governed release was created.", "success", 7000);
      await loadValidation($("#validation-job-select").value || "current");
      await loadDashboard(true);
    } catch (error) {
      toast("Publication failed", describeError(error), "error");
    } finally {
      button.textContent = "Publish snapshot";
      button.disabled = !$("#publication-confirm").checked;
    }
  }

  function normaliseSheetsStatus(data = {}) {
    const connection = data.connection || data;
    const snapshot = data.snapshot || data.local_snapshot || {};
    const counts = snapshot.counts || data.counts || {};
    return {
      connected: connection.connected ?? (connection.status ? connection.status === "connected" : Boolean(data.ok)),
      message: connection.message || connection.status,
      spreadsheetId: connection.spreadsheet_id || data.spreadsheet_id,
      capturedAt: snapshot.captured_at || snapshot.created_at || data.snapshot_at,
      people: counts.People ?? counts.people ?? snapshot.people,
      parties: counts.Parties ?? counts.parties ?? snapshot.parties,
      constituencies: counts.Constituencies ?? counts.constituencies ?? snapshot.constituencies,
      checksum: snapshot.checksum || snapshot.workbook_checksum || data.checksum,
    };
  }

  async function loadSheetsStatus(showToast = false) {
    try {
      const data = await api.get("/api/sheets/status");
      renderSheetsStatus(normaliseSheetsStatus(data || {}));
      if (showToast) toast("Connection available", "The Grand Database can be read by the local application.", "success");
    } catch (error) {
      const badge = $("#sheets-status-badge");
      badge.className = "badge danger";
      badge.innerHTML = '<span class="status-dot error"></span>Not connected';
      if (showToast) toast("Connection failed", describeError(error), "error");
    }
  }

  function renderSheetsStatus(status) {
    const badge = $("#sheets-status-badge");
    badge.className = `badge ${status.connected ? "success" : "warning"}`;
    badge.innerHTML = `<span class="status-dot ${status.connected ? "online" : ""}"></span>${status.connected ? "Connected" : "Credentials required"}`;
    if (status.spreadsheetId) $("#spreadsheet-id").value = status.spreadsheetId;
    setText("#snapshot-date", status.capturedAt ? `Snapshot from ${formatDate(status.capturedAt)}` : "No local snapshot recorded");
    setText("#snapshot-people", formatNumber(status.people));
    setText("#snapshot-parties", formatNumber(status.parties));
    setText("#snapshot-constituencies", formatNumber(status.constituencies));
    setText("#snapshot-checksum", status.checksum ? `${String(status.checksum).slice(0, 14)}…` : "—");
    setText("#sync-people-count", status.people != null ? `${formatNumber(status.people)} local` : "—");
    setText("#sync-parties-count", status.parties != null ? `${formatNumber(status.parties)} local` : "—");
    setText("#sync-constituencies-count", status.constituencies != null ? `${formatNumber(status.constituencies)} local` : "—");
  }

  async function previewSync() {
    const tabs = $$('input[name="sync-tab"]:checked').map((input) => input.value);
    if (!tabs.length) {
      toast("Select a reference tab", "Choose at least one tab to compare.", "error");
      return;
    }
    const button = $("#preview-sync-button");
    button.disabled = true;
    button.innerHTML = '<span class="spinner"></span>Comparing snapshots…';
    try {
      const data = await api.post("/api/sheets/preview", {
        spreadsheet_id: $("#spreadsheet-id").value.trim(),
        tabs,
      });
      state.syncPreview = normaliseSyncPreview(data || {});
      renderSyncPreview();
      toast("Preview ready", "Review the differences before applying and activating a local snapshot.", "success");
      $("#sync-preview-panel").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      toast("Could not preview sync", describeError(error), "error");
    } finally {
      button.disabled = false;
      button.textContent = "Preview changes";
    }
  }

  function normaliseSyncPreview(data) {
    const preview = data.preview || data;
    const summary = preview.summary || preview.counts || {};
    const changes = preview.changes || preview.diffs || preview.items || [];
    return {
      id: preview.id || preview.preview_id || data.preview_id,
      added: summary.added ?? preview.added ?? changes.filter((item) => item.change === "added").length,
      updated: summary.updated ?? summary.changed ?? preview.updated ?? changes.filter((item) => ["updated", "changed"].includes(item.change)).length,
      removed: summary.removed ?? summary.missing ?? preview.removed ?? changes.filter((item) => ["removed", "missing"].includes(item.change)).length,
      unchanged: summary.unchanged ?? preview.unchanged ?? 0,
      changes,
      warnings: preview.warnings || [],
    };
  }

  function renderSyncPreview() {
    const preview = state.syncPreview;
    $("#sync-preview-panel").classList.remove("hidden");
    setText("#diff-added", formatNumber(preview.added));
    setText("#diff-updated", formatNumber(preview.updated));
    setText("#diff-removed", formatNumber(preview.removed));
    setText("#diff-unchanged", formatNumber(preview.unchanged));
    const body = $("#sync-diff-table-body");
    const changes = preview.changes.slice(0, 100);
    body.innerHTML = changes.length ? changes.map((item) => {
      const change = String(item.change || item.status || "updated").toLowerCase();
      const badgeClass = change === "added" ? "success" : ["removed", "missing"].includes(change) ? "warning" : "info";
      const fields = item.fields || item.changed_fields || item.diff || [];
      return `<tr><td>${escapeHtml(item.tab || item.table || item.entity_type || "—")}</td><td><strong>${escapeHtml(item.label || item.name || item.id || item.primary_key || "—")}</strong><br><small>${escapeHtml(item.id || item.primary_key || "")}</small></td><td><span class="badge ${badgeClass}">${escapeHtml(labelize(change))}</span></td><td>${escapeHtml(Array.isArray(fields) ? fields.join(", ") : typeof fields === "object" ? Object.keys(fields).join(", ") : fields || "—")}</td></tr>`;
    }).join("") : '<tr class="empty-row"><td colspan="4"><div class="empty-state compact"><h3>No record differences</h3><p>The selected tabs already match the local snapshot.</p></div></td></tr>';
    $("#apply-sync-button").disabled = !preview.id;
  }

  async function applySync() {
    if (!state.syncPreview?.id) return;
    const button = $("#apply-sync-button");
    button.disabled = true;
    button.textContent = "Applying & activating…";
    try {
      const data = await api.post("/api/sheets/sync", { preview_id: state.syncPreview.id });
      toast("Local release activated", "The reviewed references were written to an isolated copy, validated, frozen as an immutable local release and activated. The Grand Database was not modified.", "success", 8000);
      state.syncPreview = null;
      $("#sync-preview-panel").classList.add("hidden");
      renderSheetsStatus(normaliseSheetsStatus(data || {}));
      await loadDashboard(true);
    } catch (error) {
      toast("Snapshot could not be activated", describeError(error), "error");
    } finally {
      button.disabled = false;
      button.textContent = "Apply & activate local snapshot";
    }
  }

  function bindNavigation() {
    $$("[data-route]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.route)));
    $$("[data-go]").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.go)));
    $("#menu-button").addEventListener("click", () => {
      const open = document.body.classList.toggle("menu-open");
      $("#menu-button").setAttribute("aria-expanded", String(open));
    });
    $("#mobile-scrim").addEventListener("click", () => {
      document.body.classList.remove("menu-open");
      $("#menu-button").setAttribute("aria-expanded", "false");
    });
    $("#refresh-button").addEventListener("click", async () => {
      const button = $("#refresh-button");
      button.disabled = true;
      await loadRoute(state.route, { silent: true });
      button.disabled = false;
      toast("View refreshed", "Latest local state loaded.", "info", 2200);
    });
    window.addEventListener("hashchange", () => navigate(location.hash.slice(1), { fromHash: true }));
  }

  function bindIngestion() {
    const dropzone = $("#dropzone");
    const fileInput = $("#file-input");
    dropzone.addEventListener("click", () => fileInput.click());
    dropzone.addEventListener("keydown", (event) => {
      if (["Enter", " "].includes(event.key)) { event.preventDefault(); fileInput.click(); }
    });
    ["dragenter", "dragover"].forEach((name) => dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.add("dragging"); }));
    ["dragleave", "drop"].forEach((name) => dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.remove("dragging"); }));
    dropzone.addEventListener("drop", (event) => handleFile(event.dataTransfer?.files?.[0]));
    fileInput.addEventListener("change", () => handleFile(fileInput.files?.[0]));
    $("#replace-file-button").addEventListener("click", resetIngestion);
    $("#inspect-back-button").addEventListener("click", resetIngestion);
    $("#inspect-continue-button").addEventListener("click", () => showIngestStep(3));
    $("#configure-back-button").addEventListener("click", () => showIngestStep(2));
    $("#job-config-form").addEventListener("submit", createJob);
    $("#cancel-job-button").addEventListener("click", cancelJob);
    $("#resume-job-button").addEventListener("click", runCurrentJob);
    $("#save-format-button").addEventListener("click", saveFormatChoices);
    $("#review-mappings-button").addEventListener("click", () => navigate("mappings", { jobId: state.currentJobId }));
    $("#review-validation-button").addEventListener("click", () => navigate("validation", { jobId: state.currentJobId }));
    $("#reproduce-2025-button").addEventListener("click", () => openModal("#reproduce-modal"));
    $("#confirm-reproduce-button").addEventListener("click", reproduceGoverned2025);
    $("#bootstrap-aec-button").addEventListener("click", openBootstrapDialog);
    $("#bootstrap-aec-form").addEventListener("submit", previewAecBootstrap);
    $("#bootstrap-edit-button").addEventListener("click", editAecBootstrap);
    $("#bootstrap-confirm").addEventListener("change", (event) => {
      $("#run-bootstrap-button").disabled = !event.target.checked;
    });
    $("#run-bootstrap-button").addEventListener("click", runAecBootstrap);
  }

  function bindExplorer() {
    $('#explorer-dataset').addEventListener('change', () => {
      updateExplorerDependentOptions();
      state.explorerPage = 1;
    });
    $('#explorer-election').addEventListener('change', () => {
      updateExplorerDependentOptions();
      state.explorerPage = 1;
    });
    $('#explorer-chamber').addEventListener('change', () => {
      updateExplorerDependentOptions();
      state.explorerPage = 1;
    });
    $('#explorer-state').addEventListener('change', () => {
      updateExplorerDependentOptions();
      state.explorerPage = 1;
    });
    $('#explorer-contest').addEventListener('change', () => { state.explorerPage = 1; });
    $('#explorer-apply-button').addEventListener('click', () => {
      state.explorerPage = 1;
      loadExplorerRows();
    });
    $('#explorer-reset-button').addEventListener('click', resetExplorer);
    $('#explorer-export-button').addEventListener('click', exportExplorer);
    $('#explorer-page-size').addEventListener('change', () => {
      state.explorerPage = 1;
      loadExplorerRows();
    });
    $('#explorer-prev-button').addEventListener('click', () => {
      if (state.explorerPage > 1) {
        state.explorerPage -= 1;
        loadExplorerRows();
      }
    });
    $('#explorer-next-button').addEventListener('click', () => {
      if (state.explorerResult && state.explorerPage < state.explorerResult.total_pages) {
        state.explorerPage += 1;
        loadExplorerRows();
      }
    });
    $('#explorer-search').addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        state.explorerPage = 1;
        loadExplorerRows();
      }
    });
  }

  function bindFeeds() {
    $('#feeds-election').addEventListener('change', renderFeedCards);
    $('#feeds-state').addEventListener('change', renderFeedCards);
    $('#feed-cards').addEventListener('click', (event) => {
      const button = event.target.closest('[data-feed-action]');
      if (!button) return;
      const url = button.dataset.feedUrl;
      if (button.dataset.feedAction === 'copy') copyFeedUrl(url);
      else if (button.dataset.feedAction === 'open') window.open(url, '_blank', 'noopener');
      else if (button.dataset.feedAction === 'download') {
        window.location.assign(url);
        toast('CSV feed started', 'The release-bound publication file is being downloaded.', 'success', 3200);
      }
    });
  }

  function bindWebsitePublication() {
    $("#build-website-button").addEventListener("click", buildWebsitePublication);
  }

  function bindMappings() {
    $("#mapping-job-select").addEventListener("change", (event) => event.target.value && loadMappings(event.target.value));
    $$('[data-mapping-filter]').forEach((button) => button.addEventListener("click", () => {
      state.mappingFilter = button.dataset.mappingFilter;
      $$('[data-mapping-filter]').forEach((item) => item.classList.toggle("active", item === button));
      state.mappingPage = 1;
      renderMappings();
    }));
    $("#mapping-search").addEventListener("input", (event) => {
      state.mappingQuery = event.target.value.trim().toLowerCase();
      state.mappingPage = 1;
      renderMappings();
    });
    $("#mapping-type-filter").addEventListener("change", (event) => {
      state.mappingType = event.target.value;
      state.mappingPage = 1;
      renderMappings();
    });
    $("#mapping-prev").addEventListener("click", () => { state.mappingPage -= 1; renderMappings(); });
    $("#mapping-next").addEventListener("click", () => { state.mappingPage += 1; renderMappings(); });
    $("#canonical-search").addEventListener("input", (event) => {
      window.clearTimeout(canonicalSearchTimer);
      canonicalSearchTimer = window.setTimeout(() => searchCanonical(event.target.value.trim()), 250);
    });
    $("#save-mapping-button").addEventListener("click", () => saveMapping(false));
    $("#leave-unresolved-button").addEventListener("click", () => saveMapping(true));
    $("#continue-mapped-job-button").addEventListener("click", continueMappedJob);
  }

  function bindValidation() {
    $("#validation-job-select").addEventListener("change", (event) => loadValidation(event.target.value));
    $("#rerun-validation-button").addEventListener("click", rerunValidation);
    $$('[data-validation-filter]').forEach((button) => button.addEventListener("click", () => {
      state.validationFilter = button.dataset.validationFilter;
      $$('[data-validation-filter]').forEach((item) => item.classList.toggle("active", item === button));
      renderValidationChecks();
    }));
    $("#publish-button").addEventListener("click", () => {
      $("#publication-confirm").checked = false;
      $("#publication-note").value = "";
      $("#confirm-publish-button").disabled = true;
      openModal("#publish-modal");
    });
    $("#publication-confirm").addEventListener("change", (event) => { $("#confirm-publish-button").disabled = !event.target.checked; });
    $("#confirm-publish-button").addEventListener("click", publishSnapshot);
  }

  function bindSheets() {
    $("#test-sheets-button").addEventListener("click", () => loadSheetsStatus(true));
    $("#preview-sync-button").addEventListener("click", previewSync);
    $("#discard-sync-preview-button").addEventListener("click", () => {
      state.syncPreview = null;
      $("#sync-preview-panel").classList.add("hidden");
    });
    $("#apply-sync-button").addEventListener("click", applySync);
  }

  function bindModals() {
    $$('[data-close-modal]').forEach((button) => button.addEventListener("click", () => closeModal(button.closest(".modal-backdrop"))));
    $$(".modal-backdrop").forEach((backdrop) => backdrop.addEventListener("mousedown", (event) => {
      if (event.target === backdrop) closeModal(backdrop);
    }));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        const modal = $(".modal-backdrop:not(.hidden)");
        if (modal) closeModal(modal);
      }
    });
  }

  async function initialise() {
    bindNavigation();
    bindIngestion();
    bindExplorer();
    bindFeeds();
    bindWebsitePublication();
    bindMappings();
    bindValidation();
    bindSheets();
    bindModals();
    const initialRoute = pageLabels[location.hash.slice(1)] ? location.hash.slice(1) : "dashboard";
    navigate(initialRoute, { fromHash: true });
  }

  document.addEventListener("DOMContentLoaded", initialise);
})();
