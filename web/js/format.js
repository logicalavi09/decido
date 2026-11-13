const ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ESCAPES[char]);
}

export const h = escapeHtml;

export function shortId(value, length = 8) {
  const text = String(value || "");
  return text.length > length ? text.slice(0, length) : text;
}

export function shortHash(value, length = 12) {
  const text = String(value || "");
  return text ? `${text.slice(0, length)}…` : "—";
}

export function titleize(value) {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export const STATUS_META = {
  approved: { label: "Approved", color: "var(--approve)", cls: "approved" },
  rejected: { label: "Rejected", color: "var(--reject)", cls: "rejected" },
  manual_review: { label: "Manual review", color: "var(--review)", cls: "manual_review" },
  retry_scheduled: { label: "Retry scheduled", color: "var(--retry)", cls: "retry_scheduled" },
  failed: { label: "Failed", color: "var(--failed)", cls: "failed" },
  running: { label: "Running", color: "var(--accent)", cls: "running" },
  received: { label: "Received", color: "var(--accent)", cls: "received" },
  schema_invalid: { label: "Schema invalid", color: "var(--reject)", cls: "rejected" },
};

export function statusMeta(status) {
  return (
    STATUS_META[status] || {
      label: titleize(status || "unknown"),
      color: "var(--muted)",
      cls: "",
    }
  );
}

export function statusStamp(status) {
  const meta = statusMeta(status);
  return `<span class="status-stamp status-${escapeHtml(meta.cls)}">${escapeHtml(meta.label)}</span>`;
}

export function outcomeLabel(outcome) {
  return statusMeta(outcome).label;
}

export function runReason(run) {
  return (
    (run && run.result && run.result.reason) ||
    (run && run.last_error) ||
    (run && run.status === "running" ? "Decision in progress" : "No reason recorded")
  );
}

export function runSubject(run) {
  if (!run || !run.input) return "—";
  const input = run.input;
  return (
    input.reference ||
    input.policy_id ||
    (input.vendor && input.vendor.name) ||
    (input.candidate && input.candidate.name) ||
    (input.owner && input.owner.name) ||
    (input.applicant && input.applicant.name) ||
    (input.document && input.document.type) ||
    run.workflow
  );
}

export function formatNumber(value) {
  return new Intl.NumberFormat("en-US").format(Number(value) || 0);
}

export function formatPercent(value, digits = 0) {
  return `${(Number(value) || 0).toFixed(digits)}%`;
}

export function formatDate(value, options = {}) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    ...options,
  }).format(date);
}

export function relativeTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const delta = date.getTime() - Date.now();
  const abs = Math.abs(delta);
  if (abs < 10_000) return delta < 0 ? "just now" : "in a moment";
  const units = [
    ["year", 31_536_000_000],
    ["month", 2_592_000_000],
    ["day", 86_400_000],
    ["hour", 3_600_000],
    ["minute", 60_000],
    ["second", 1000],
  ];
  const [unit, ms] = units.find(([, size]) => abs >= size) || units[units.length - 1];
  const amount = Math.round(delta / ms);
  return new Intl.RelativeTimeFormat("en", { numeric: "auto" }).format(amount, unit);
}

export function durationLabel(from, to) {
  if (!from || !to) return "—";
  const start = new Date(from).getTime();
  const end = new Date(to).getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) return "—";
  const seconds = Math.max(0, (end - start) / 1000);
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export function prettyJson(value, space = 2) {
  try {
    return JSON.stringify(value, null, space);
  } catch {
    return String(value);
  }
}

export function jsonBlock(value, extraClass = "") {
  return `<pre class="code-block ${extraClass}">${escapeHtml(prettyJson(value))}</pre>`;
}

export function isPlainObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

const OP_LABELS = {
  eq: "=",
  neq: "≠",
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  in: "in",
  not_in: "not in",
  contains: "contains",
  not_contains: "does not contain",
  exists: "exists",
  not_exists: "does not exist",
  matches: "matches",
  between: "between",
  len_gte: "length ≥",
  len_lte: "length ≤",
  len_eq: "length =",
};

function valueText(value) {
  if (typeof value === "string") return `"${value}"`;
  return JSON.stringify(value);
}

export function conditionText(condition) {
  if (!condition || typeof condition !== "object") return "—";
  if (Array.isArray(condition.all)) {
    return condition.all.map(conditionText).join(" AND ");
  }
  if (Array.isArray(condition.any)) {
    return condition.any.map(conditionText).join(" OR ");
  }
  if (condition.not) {
    return `NOT (${conditionText(condition.not)})`;
  }
  const op = OP_LABELS[condition.op] || condition.op || "?";
  if (condition.op === "exists" || condition.op === "not_exists") {
    return `${condition.field} ${op}`;
  }
  const expected =
    typeof condition.value === "string" ? `"${condition.value}"` : valueText(condition.value);
  return `${condition.field} ${op} ${expected}`;
}

export function findingLocation(finding) {
  return [finding.stage, finding.rule_id, finding.field].filter(Boolean).join(" · ");
}

export function pluralize(count, singular, plural = `${singular}s`) {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function sigil(value) {
  const text = String(value || "?")
    .trim()
    .charAt(0)
    .toUpperCase();
  return text || "?";
}
