// Per-trip GPS trace for meter calibration. Driver tools only (driver.js is the one caller).
//
// What it is: one row per GPS fix the trip meter saw, accepted or rejected, kept as CSV text in this browser's
// localStorage so that, after a few shifts, a distance scale factor can be fitted from real data instead of guessed.
// What it is not: it is never uploaded or shared by the app. The only way it leaves the device is the driver tapping
// "Export trace", which saves a file locally. "Delete traces" removes every saved trace.
//
// Storage layout, kept apart from the 200-trip list so the trip list can never be crowded out:
//   warn-map-trace-running   the trace of the trip in progress (CSV text, rewritten with the trip every 10 s)
//   warn-map-traces          saved traces, newest first: [{ id, startTime, rows, dropped, csv }]
// Limits: at most MAX_TRACES saved traces, at most MAX_ROWS rows per trip, and a total character budget well under
// the browser's quota. The oldest trace goes first; storage errors are swallowed, never thrown.

export const TRACE_COLUMNS = [
  "t_s", "lat", "lon", "accuracy_m", "speed_ms", "interval_s", "straight_m", "road_m", "added_m", "slow",
  "wait_added_s", "reject",
];
export const MAX_TRACES = 10;
export const MAX_ROWS = 20_000;
export const MAX_TOTAL_CHARS = 2_500_000;
export const TRACES_KEY = "warn-map-traces";
export const RUNNING_KEY = "warn-map-trace-running";

const HEADER = TRACE_COLUMNS.join(",");

function number(value, digits) {
  return Number.isFinite(value) ? value.toFixed(digits) : "";
}

/**
 * One CSV row for one fix. `fix` fields (all optional, blank when absent): t (seconds from trip start), lat, lon,
 * accuracy, speed (m/s as reported), interval (s since the previous accepted fix), straight (m from the previous
 * accepted fix), road (m from the router), added (m actually added to the trip), slow (true/false), waitAdded (s),
 * reject (reason text, empty when the fix was accepted).
 */
export function traceRow(fix) {
  return [
    number(fix.t, 1), number(fix.lat, 5), number(fix.lon, 5), number(fix.accuracy, 1), number(fix.speed, 2),
    number(fix.interval, 2), number(fix.straight, 3), number(fix.road, 3), number(fix.added, 3),
    fix.slow === undefined || fix.slow === null ? "" : (fix.slow ? "1" : "0"),
    number(fix.waitAdded, 2), fix.reject || "",
  ].join(",");
}

/** A trace being recorded: { lines, dropped }. Rows past MAX_ROWS are counted, not kept. */
export function newLog(lines = []) {
  return { lines, dropped: 0 };
}

export function appendRow(log, fix) {
  if (log.lines.length >= MAX_ROWS) {
    log.dropped += 1;
    return;
  }
  log.lines.push(traceRow(fix));
}

export function csvText(lines) {
  return `${HEADER}\n${lines.join("\n")}${lines.length > 0 ? "\n" : ""}`;
}

function read(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

/** The trace of the trip in progress, as saved at the last persist; null when there is none. */
export function loadRunning() {
  const text = read(RUNNING_KEY);
  if (!text) return null;
  const lines = text.split("\n").filter((line) => line !== "");
  return newLog(lines.slice(0, MAX_ROWS));
}

/** Saves the running trace. Returns false (and keeps going) when storage refuses it. */
export function saveRunning(log) {
  if (log.lines.length === 0) return write(RUNNING_KEY, null);
  return write(RUNNING_KEY, log.lines.join("\n"));
}

export function clearRunning() {
  write(RUNNING_KEY, null);
}

export function loadTraces() {
  try {
    const traces = JSON.parse(read(TRACES_KEY) ?? "[]");
    return Array.isArray(traces) ? traces.filter((trace) => trace && typeof trace.csv === "string") : [];
  } catch {
    return [];
  }
}

export function traceFor(startTime) {
  return loadTraces().find((trace) => trace.startTime === startTime) ?? null;
}

/**
 * Adds a finished trip's trace (newest first), then trims: at most MAX_TRACES, at most MAX_TOTAL_CHARS of CSV, and
 * further oldest-first if the browser still refuses the write. The newest trace is the last to go. Returns
 * { saved, kept }: whether this trace is stored, and how many traces are.
 */
export function storeTrace(startTime, log) {
  const entry = {
    id: String(startTime), startTime, rows: log.lines.length, dropped: log.dropped, csv: csvText(log.lines),
  };
  let traces = [entry, ...loadTraces().filter((trace) => trace.startTime !== startTime)].slice(0, MAX_TRACES);
  const size = (list) => list.reduce((sum, trace) => sum + trace.csv.length, 0);
  while (traces.length > 1 && size(traces) > MAX_TOTAL_CHARS) traces.pop();
  while (traces.length > 0) {
    if (write(TRACES_KEY, JSON.stringify(traces))) {
      return { saved: traces[0] === entry, kept: traces.length };
    }
    traces.pop(); // quota: drop the oldest and try again
  }
  write(TRACES_KEY, null);
  return { saved: false, kept: 0 };
}

export function clearTraces() {
  write(TRACES_KEY, null);
}

/** A file name for the export, from the trip's local start time: warn-trace-20261019-1642.csv */
export function traceFileName(startTime) {
  const date = new Date(startTime);
  const two = (value) => String(value).padStart(2, "0");
  return `warn-trace-${date.getFullYear()}${two(date.getMonth() + 1)}${two(date.getDate())}`
    + `-${two(date.getHours())}${two(date.getMinutes())}.csv`;
}

/** Saves the CSV as a local file. Runs only from a tap; makes no network request. */
export function downloadTrace(trace) {
  const blob = new Blob([trace.csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = traceFileName(trace.startTime);
  document.body.append(link);
  link.click();
  link.remove();
  globalThis.setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
