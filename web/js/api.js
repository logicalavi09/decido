export class ApiError extends Error {
  constructor(message, { status = 0, code = "network_error", details = null } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function queryString(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const result = search.toString();
  return result ? `?${result}` : "";
}

async function request(path, { method = "GET", body, headers = {} } = {}) {
  const options = { method, headers: { Accept: "application/json", ...headers } };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    throw new ApiError("The API is unreachable. Is the server running?", {
      status: 0,
      code: "network_error",
      details: String(error),
    });
  }

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    const error = payload && typeof payload === "object" ? payload.error : null;
    throw new ApiError(
      (error && error.message) || `Request failed with status ${response.status}`,
      {
        status: response.status,
        code: (error && error.code) || "request_failed",
        details: error ? error.details : payload,
      },
    );
  }

  return payload;
}

export const api = {
  health: () => request("/health"),
  workflows: () => request("/workflows"),
  workflow: (name) => request(`/workflows/${encodeURIComponent(name)}`),
  lint: (name) => request(`/workflows/${encodeURIComponent(name)}/lint`),
  runs: (params = {}) => request(`/runs${queryString(params)}`),
  run: (id) => request(`/runs/${encodeURIComponent(id)}`),
  audit: (id) => request(`/runs/${encodeURIComponent(id)}/audit`),
  explanation: (id) => request(`/runs/${encodeURIComponent(id)}/explanation`),
  verifyAudit: (id) => request(`/runs/${encodeURIComponent(id)}/audit/verify`),

  createRun: ({ workflow, input, simulation, idempotencyKey }) => {
    const headers = {};
    if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
    const body = { workflow, input };
    if (simulation) body.simulation = simulation;
    return request("/runs", { method: "POST", body, headers });
  },

  resolveRun: (id, { decision, reviewer, comment }) =>
    request(`/runs/${encodeURIComponent(id)}/resolve`, {
      method: "POST",
      body: { decision, reviewer, comment },
    }),

  resumeRun: (id, simulation) =>
    request(`/runs/${encodeURIComponent(id)}/resume`, {
      method: "POST",
      body: simulation ? { simulation } : undefined,
    }),

  impact: (name, payload) =>
    request(`/workflows/${encodeURIComponent(name)}/impact`, {
      method: "POST",
      body: payload,
    }),

  reload: ({ dryRun = false, ifMatch } = {}) => {
    const headers = {};
    if (ifMatch) headers["If-Match"] = ifMatch;
    return request(`/workflows/reload${queryString({ dry_run: dryRun })}`, {
      method: "POST",
      headers,
    });
  },
};
