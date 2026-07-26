import {partyColour, partyKey} from "./party.3576021c.js";
import {stateForRow} from "./format.e2a4a187.js";

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function nameKey(value) {
  return String(value || "Independent / ungrouped").trim().toLowerCase();
}

export function marginSpectrumRows(seats) {
  return (seats || [])
    .map((seat) => ({...seat, margin: finite(seat.winning_margin_percentage_points)}))
    .filter((seat) => seat.margin !== null)
    .sort((a, b) => a.margin - b.margin || String(a.contest_name).localeCompare(String(b.contest_name)));
}

export function closestContestRows(seats, limit = 12) {
  return marginSpectrumRows(seats).slice(0, Math.max(0, Number(limit) || 0));
}

export function tcpSwingRows(seats, limit = 14) {
  return (seats || [])
    .map((seat) => ({...seat, tcpSwing: finite(seat.tcp_swing)}))
    .filter((seat) => seat.tcpSwing !== null)
    .sort((a, b) => Math.abs(b.tcpSwing) - Math.abs(a.tcpSwing) || String(a.contest_name).localeCompare(String(b.contest_name)))
    .slice(0, Math.max(0, Number(limit) || 0));
}

export function partyGainLossRows(seats) {
  const groups = new Map();
  const identityByName = new Map();
  const get = (key, name, colour = "") => {
    if (!groups.has(key)) groups.set(key, {partyKey: key, partyName: name, colour, gains: 0, losses: 0, retained: 0, newMembers: 0});
    const current = groups.get(key);
    if (!current.colour && colour) current.colour = colour;
    return current;
  };
  for (const seat of seats || []) {
    identityByName.set(nameKey(seat.party_name), partyKey(seat));
  }
  for (const seat of seats || []) {
    const winnerName = seat.party_name || "Independent / ungrouped";
    const winner = get(partyKey(seat), winnerName, partyColour(seat));
    if (seat.seat_change_type === "gained") winner.gains += 1;
    else if (seat.seat_change_type === "retained") winner.retained += 1;
    else winner.newMembers += 1;
    if (seat.defeated_incumbent_party_name) {
      const defeatedName = seat.defeated_incumbent_party_name;
      const defeatedKey = identityByName.get(nameKey(defeatedName)) || nameKey(defeatedName);
      get(defeatedKey, defeatedName).losses += 1;
    }
  }
  return [...groups.values()]
    .filter((row) => row.gains || row.losses || row.retained || row.newMembers)
    .sort((a, b) => (b.gains + b.losses) - (a.gains + a.losses) || a.partyName.localeCompare(b.partyName));
}

export function voteSeatRows(partySummary, seats, candidateRows) {
  const scopedSeats = seats || [];
  const contestIds = new Set(scopedSeats.map((seat) => seat.contest_id));
  const votes = new Map();
  for (const candidate of candidateRows || []) {
    if (candidate.result_type !== "first_preference" || !contestIds.has(candidate.contest_id)) continue;
    const key = partyKey(candidate);
    const current = votes.get(key) || {
      partyKey: key,
      partyName: candidate.party_name || "Independent / ungrouped",
      colour: partyColour(candidate),
      votes: 0
    };
    current.votes += Number(candidate.votes || 0);
    votes.set(key, current);
  }
  const seatCounts = new Map();
  for (const seat of scopedSeats) seatCounts.set(partyKey(seat), (seatCounts.get(partyKey(seat)) || 0) + 1);
  const totalVotes = [...votes.values()].reduce((sum, row) => sum + row.votes, 0);
  const totalSeats = scopedSeats.length;
  const summaryByKey = new Map((partySummary || []).map((row) => [partyKey(row), row]));
  const keys = new Set([...votes.keys(), ...seatCounts.keys()]);
  return [...keys].map((key) => {
    const vote = votes.get(key);
    const summary = summaryByKey.get(key);
    const seatsWon = seatCounts.get(key) || 0;
    return {
      partyKey: key,
      partyName: vote?.partyName || summary?.party_name || "Independent / ungrouped",
      colour: vote?.colour || partyColour(summary || {}),
      votes: vote?.votes || 0,
      voteShare: totalVotes ? 100 * (vote?.votes || 0) / totalVotes : 0,
      seats: seatsWon,
      seatShare: totalSeats ? 100 * seatsWon / totalSeats : 0
    };
  }).sort((a, b) => b.seats - a.seats || b.votes - a.votes || a.partyName.localeCompare(b.partyName));
}

export function stateComparisonRows(seats) {
  const groups = new Map();
  for (const seat of seats || []) {
    const state = stateForRow(seat);
    if (!state) continue;
    const current = groups.get(state) || {state, seats: 0, marginTotal: 0, marginCount: 0, turnoutTotal: 0, turnoutCount: 0, gains: 0};
    current.seats += 1;
    const margin = finite(seat.winning_margin_percentage_points);
    if (margin !== null) {
      current.marginTotal += margin;
      current.marginCount += 1;
    }
    const turnout = finite(seat.turnout_percentage);
    if (turnout !== null) {
      current.turnoutTotal += turnout;
      current.turnoutCount += 1;
    }
    if (seat.seat_change_type === "gained") current.gains += 1;
    groups.set(state, current);
  }
  return [...groups.values()].map((row) => ({
    state: row.state,
    seats: row.seats,
    averageMargin: row.marginCount ? row.marginTotal / row.marginCount : null,
    averageTurnout: row.turnoutCount ? row.turnoutTotal / row.turnoutCount : null,
    gains: row.gains
  })).sort((a, b) => a.state.localeCompare(b.state));
}

export function voteTypeRows(candidateRows, contestId) {
  const order = new Map([["first_preference", 0], ["tcp", 1], ["tpp", 2]]);
  return (candidateRows || [])
    .filter((row) => row.contest_id === contestId && order.has(row.result_type))
    .map((row) => ({...row, voteShareNumber: finite(row.vote_share), votesNumber: finite(row.votes) || 0}))
    .sort((a, b) => order.get(a.result_type) - order.get(b.result_type) || b.votesNumber - a.votesNumber);
}
