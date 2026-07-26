import {normaliseState, stateForRow} from "./format.e2a4a187.js";
import {partyColour, partyKey} from "./party.3576021c.js";

function numeric(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function senateRounds(rows, state) {
  const selected = normaliseState(state);
  return [...new Set(rows
    .filter((row) => stateForRow(row) === selected)
    .map((row) => numeric(row.round_number, NaN))
    .filter(Number.isFinite))]
    .sort((a, b) => a - b);
}

export function senateRoundSnapshot(rows, state, roundNumber) {
  const selected = normaliseState(state);
  const round = numeric(roundNumber, NaN);
  const current = rows.filter((row) =>
    stateForRow(row) === selected && numeric(row.round_number, NaN) === round
  );
  const priorRounds = senateRounds(rows, selected).filter((value) => value < round);
  const previousRound = priorRounds.length ? priorRounds[priorRounds.length - 1] : null;
  const previous = new Map(rows
    .filter((row) => stateForRow(row) === selected && numeric(row.round_number, NaN) === previousRound)
    .map((row) => [String(row.candidacy_id), numeric(row.progressive_total, 0)]));
  return current
    .map((row) => ({
      ...row,
      total: numeric(row.progressive_total, numeric(row.votes_value, 0)),
      change: previousRound === null
        ? numeric(row.progressive_total, numeric(row.votes_value, 0))
        : numeric(row.progressive_total, 0) - (previous.get(String(row.candidacy_id)) || 0),
      status: String(row.candidate_count_status || "continuing").toLowerCase()
    }))
    .sort((a, b) => b.total - a.total || String(a.candidate_name).localeCompare(String(b.candidate_name)));
}

export function senateMovementRows(rows, state, roundNumber) {
  const selected = normaliseState(state);
  const round = numeric(roundNumber, NaN);
  return rows
    .filter((row) => stateForRow(row) === selected && numeric(row.round_number, NaN) === round)
    .map((row) => ({
      ...row,
      candidateName: row.exhausted ? "Exhausted" : (row.to_candidate_name || row.from_candidate_name || "Unidentified movement"),
      partyName: row.to_party_name || row.from_party_name || "",
      colour: row.exhausted ? "#657382" : (row.to_party_colour || row.from_party_colour || "#8c9aa6"),
      movement: numeric(row.votes_value, 0)
    }))
    .filter((row) => row.movement !== 0)
    .sort((a, b) => Math.abs(b.movement) - Math.abs(a.movement) || a.candidateName.localeCompare(b.candidateName));
}

export function senateMilestones(rows, state) {
  const selected = normaliseState(state);
  const milestones = [];
  const observed = new Set();
  const selectedRows = [...rows]
    .filter((row) => stateForRow(row) === selected)
    .sort((a, b) => numeric(a.round_number) - numeric(b.round_number));
  for (const row of selectedRows) {
    const status = String(row.candidate_count_status || "").toLowerCase();
    if (!/(elect|exclud|eliminat)/.test(status)) continue;
    const type = /elect/.test(status) ? "elected" : "excluded";
    const key = `${row.candidacy_id}|${type}`;
    if (observed.has(key)) continue;
    observed.add(key);
    milestones.push({
      round: numeric(row.round_number),
      type,
      candidateName: row.candidate_name || "Candidate",
      partyName: row.party_name || "Independent / ungrouped",
      colour: partyColour(row),
      total: numeric(row.progressive_total, 0)
    });
  }
  return milestones.sort((a, b) => a.round - b.round || a.candidateName.localeCompare(b.candidateName));
}

export function senateDelegationSummary(rows) {
  const states = ["ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"];
  return states.map((state) => {
    const members = rows.filter((row) => stateForRow(row) === state);
    const parties = new Map();
    for (const member of members) {
      const key = partyKey(member);
      const current = parties.get(key) || {
        partyKey: key,
        partyName: member.party_name || member.party_abbreviation || "Independent / ungrouped",
        colour: partyColour(member),
        count: 0
      };
      current.count += 1;
      parties.set(key, current);
    }
    return {
      state,
      members,
      parties: [...parties.values()].sort((a, b) => b.count - a.count || a.partyName.localeCompare(b.partyName))
    };
  });
}

export function senateGroupQuotaRows(rows, quota) {
  const quotaValue = numeric(quota, 0);
  return [...rows]
    .map((row) => ({
      ...row,
      votesNumber: numeric(row.votes, 0),
      quotaMultiple: quotaValue > 0 ? numeric(row.votes, 0) / quotaValue : null
    }))
    .sort((a, b) => b.votesNumber - a.votesNumber || String(a.party_name || a.subject_name).localeCompare(String(b.party_name || b.subject_name)));
}
