import { api } from "./api.js";
import { sampleInput } from "./data.js";
import {
  conditionText,
  escapeHtml,
  findingLocation,
  formatDate,
  formatNumber,
  formatPercent,
  h,
  jsonBlock,
  outcomeLabel,
  prettyJson,
  relativeTime,
  runReason,
  runSubject,
  shortHash,
  shortId,
  statusMeta,
  statusStamp,
  titleize,
} from "./format.js";
import { icon } from "./icons.js";
import {
  copyText,
  emptyState,
  errorText,
  openDialog,
  setBusy,
  toast,
} from "./ui.js";

function runItems(state) {
  return (state.runs && state.runs.items) || [];
}

function workflowItems(state) {
  return (state.workflows && state.workflows.workflows) || [];
}

function pageHead({ kicker = "", title, intro, actions = "" }) {
  return `
    <header class="page-head">
      <div class="page-head-copy">
        ${kicker ? `<p class="mono faint" style="margin-bottom:10px;letter-spacing:.08em;text-transform:uppercase">${h(kicker)}</p>` : ""}
        <h1 class="page-title">${h(title)}</h1>
        <p class="page-intro">${h(intro)}</p>
      </div>
      ${actions ? `<div class="page-actions">${actions}</div>` : ""}
    </header>
  `;
}

function refreshButton(label = "Refresh") {
  return `<button class="btn btn-sm btn-ghost" type="button" data-action="refresh">${icon("refresh")} ${h(label)}</button>`;
}

function composeButton(label = "New decision", primary = true) {
  return `<button class="btn ${primary ? "btn-primary" : ""} btn-sm" type="button" data-action="compose">${icon("plus")} ${h(label)}</button>`;
}

function workflowCard(workflow, index) {
  const stageCount = (workflow.stages || []).length;
  const dots = (workflow.stages || [])
    .map(
      (stage, dotIndex) => `
        ${dotIndex ? '<span class="stage-line"></span>' : ""}
        <span class="stage-dot" data-stage-dot="${dotIndex}" title="${h(stage)}"></span>
      `,
    )
    .join("");
  return `
    <button class="workflow-card" type="button" data-workflow="${h(workflow.name)}" style="--i:${index}" aria-label="Inspect workflow ${h(workflow.name)}">
      <span class="workflow-card-head">
        <span>
          <span class="workflow-name">${h(titleize(workflow.name))}</span>
          <span class="workflow-version">v${h(workflow.version)} · ${h(stageCount)} stages</span>
        </span>
        <span class="workflow-arrow">${icon("arrowUpRight")}</span>
      </span>
      <span class="workflow-desc">${h(workflow.description || "No description supplied for this workflow.")}</span>
      <span class="workflow-stages" aria-hidden="true">${dots}</span>
      <span class="workflow-foot">
        <span data-lint="${h(workflow.name)}">${icon("clock")} Checking lint</span>
        <span>${h(workflow.source || "yaml")}</span>
      </span>
    </button>
  `;
}

function outcomeSegments(runs) {
  const counts = {};
  for (const run of runs) {
    const key = run.outcome || run.status;
    counts[key] = (counts[key] || 0) + 1;
  }
  const order = ["approved", "manual_review", "retry_scheduled", "rejected", "failed", "running", "received"];
  return order
    .filter((key) => counts[key])
    .map((key) => ({
      key,
      count: counts[key],
      label: statusMeta(key).label,
      color: statusMeta(key).color,
    }));
}

function outcomeBar(segments, total) {
  if (!total) {
    return `<div class="empty-line">No decisions recorded yet. Seed the ledger to see the mix.</div>`;
  }
  return `
    <div class="outcome-bar" role="img" aria-label="Outcome distribution">
      ${segments
        .map(
          (segment) =>
            `<span class="outcome-seg" style="--width:${(segment.count / total) * 100}%;--seg-color:${segment.color}"></span>`,
        )
        .join("")}
    </div>
    <div class="outcome-legend">
      ${segments
        .map(
          (segment) => `
            <span class="legend-item">
              <span class="legend-swatch" style="--seg-color:${segment.color}"></span>
              ${h(segment.label)}
              <span class="legend-count">${h(segment.count)}</span>
            </span>
          `,
        )
        .join("")}
    </div>
  `;
}

function recentRow(run) {
  const meta = statusMeta(run.outcome || run.status);
  return `
    <a class="recent-row" href="#/runs/${encodeURIComponent(run.id)}">
      <span class="recent-time">${h(relativeTime(run.created_at))}</span>
      <span>
        <span class="recent-name">${h(titleize(run.workflow))}</span>
        <span class="recent-sub">${h(runSubject(run))} · ${h(shortId(run.id))}</span>
      </span>
      <span class="recent-right">
        <span class="status-stamp status-${h(meta.cls)}">${h(meta.label)}</span>
      </span>
    </a>
  `;
}

function wireItems(runs) {
  if (!runs.length) {
    return `<div class="wire-empty">The wire is quiet. Record a decision or load the demo ledger to bring it to life.</div>`;
  }
  return runs
    .slice(0, 6)
    .map((run, index) => {
      const meta = statusMeta(run.outcome || run.status);
      return `
        <a class="wire-item" href="#/runs/${encodeURIComponent(run.id)}" style="--i:${index};--wire-color:${meta.color}">
          <span class="wire-node"></span>
          <span class="wire-main">
            <span class="wire-workflow">${h(titleize(run.workflow))}</span>
            <span class="wire-meta">${h(runSubject(run))} · ${h(shortId(run.id, 6))}</span>
          </span>
          <span class="wire-status">${h(meta.label)}</span>
        </a>
      `;
    })
    .join("");
}

export function renderOverview(state) {
  const runs = runItems(state);
  const total = runs.length;
  const counts = outcomeSegments(runs);
  const byStatus = Object.fromEntries(counts.map((entry) => [entry.key, entry.count]));
  const approved = byStatus.approved || 0;
  const attention =
    (byStatus.manual_review || 0) +
    (byStatus.retry_scheduled || 0) +
    (byStatus.failed || 0);
  const approvalRate = total ? (approved / total) * 100 : 0;
  const recent = [...runs]
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))
    .slice(0, 5);

  const html = `
    <section class="hero">
      <div class="hero-copy">
        <h1 class="hero-title">
          <span class="line"><span>Decisions</span></span>
          <span class="line"><span>you can</span></span>
          <span class="line"><span class="accent-line">defend.</span></span>
        </h1>
        <p class="hero-sub">
          Every business rule, external check and escalation is pinned to the run that used it.
          Submit a request, watch the stages resolve, then prove exactly why the outcome happened.
        </p>
        <div class="hero-actions">
          ${composeButton("Record a decision")}
          <button class="btn" type="button" data-action="seed-demo">${icon("spark")} Load demo ledger</button>
          <a class="btn btn-ghost" href="#/governance">${icon("sliders")} Open change lab</a>
        </div>
        <p class="hero-note">${icon("shield")} SHA-256 chained audit on every decision · ${h(state.workflows ? state.workflows.count : "—")} workflows active</p>
      </div>
      <aside class="wire-card" aria-label="Recent decision wire">
        <div class="wire-head">
          <span class="wire-title">Decision wire</span>
          <span class="wire-live"><span class="dot"></span>${total ? "Live" : "Idle"}</span>
        </div>
        <div class="wire-list">${wireItems([...runs].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))))}</div>
      </aside>
    </section>

    <div class="stat-strip">
      <div class="stat" style="--i:0">
        <div class="stat-label">Decisions recorded</div>
        <div class="stat-value">${formatNumber(total)}<small>runs</small></div>
        <div class="stat-foot">${total ? "pinned to workflow snapshots" : "waiting for first request"}</div>
      </div>
      <div class="stat" style="--i:1">
        <div class="stat-label">Approval rate</div>
        <div class="stat-value">${formatPercent(approvalRate, 1)}<small>${approved}/${total || 0}</small></div>
        <div class="stat-foot ${approvalRate >= 50 ? "is-good" : ""}">${total ? "across the current ledger" : "no sample yet"}</div>
      </div>
      <div class="stat" style="--i:2">
        <div class="stat-label">Needs attention</div>
        <div class="stat-value">${formatNumber(attention)}<small>open</small></div>
        <div class="stat-foot ${attention ? "is-warn" : "is-good"}">${attention ? "review, retry or failure" : "queue is clear"}</div>
      </div>
      <div class="stat" style="--i:3">
        <div class="stat-label">Active workflows</div>
        <div class="stat-value">${formatNumber(state.workflows ? state.workflows.count : 0)}<small>configs</small></div>
        <div class="stat-foot">etag ${h(shortHash(state.workflows && state.workflows.config_etag, 10))}</div>
      </div>
    </div>

    <section class="section">
      <div class="section-head">
        <h2 class="section-title">The ledger at a glance</h2>
        <p class="section-note">Outcome mix and the latest decisions, without the spreadsheets.</p>
      </div>
      <div class="overview-grid">
        <div class="panel">
          <div class="panel-head">
            <span class="panel-title">Outcome distribution</span>
            <span class="mono faint">${h(total)} total</span>
          </div>
          <div class="panel-body">${outcomeBar(counts, total)}</div>
        </div>
        <div class="panel">
          <div class="panel-head">
            <span class="panel-title">Recent decisions</span>
            <a class="mono faint" href="#/runs">View all</a>
          </div>
          <div class="panel-body">
            <div class="recent-list">
              ${recent.length ? recent.map(recentRow).join("") : `<div class="empty-line">Nothing here yet.</div>`}
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <h2 class="section-title">Workflow library</h2>
        <p class="section-note">${h(workflowItems(state).length)} declarative flows · inspect rules and transitions</p>
      </div>
      <div class="workflow-grid">
        ${workflowItems(state).map(workflowCard).join("")}
      </div>
    </section>
  `;

  return {
    html,
    mount(root, ctx) {
      root.querySelectorAll("[data-action=compose]").forEach((button) =>
        button.addEventListener("click", () => ctx.openCompose()),
      );
      const seed = root.querySelector("[data-action=seed-demo]");
      if (seed) seed.addEventListener("click", () => ctx.seedDemo(seed));
      root.querySelector("[data-action=refresh]")?.addEventListener("click", () =>
        ctx.refresh({ runs: true, render: true }),
      );
      root.querySelectorAll("[data-workflow]").forEach((card) =>
        card.addEventListener("click", () => ctx.openWorkflow(card.dataset.workflow)),
      );
      hydrateWorkflowCards(root, state);
    },
  };
}

async function hydrateWorkflowCards(root, state) {
  for (const workflow of workflowItems(state)) {
    try {
      const detail = await api.workflow(workflow.name);
      if (!root.isConnected) return;
      const card = root.querySelector(`[data-workflow="${CSS.escape(workflow.name)}"]`);
      if (!card) continue;
      const lint = detail.lint && detail.lint.summary;
      const badge = card.querySelector("[data-lint]");
      if (badge && lint) {
        const errors = lint.error || 0;
        const warnings = lint.warning || 0;
        if (errors) {
          badge.className = "lint-error";
          badge.innerHTML = `${icon("alert")} ${errors} lint error${errors === 1 ? "" : "s"}`;
        } else if (warnings) {
          badge.className = "lint-warn";
          badge.innerHTML = `${icon("alert")} ${warnings} warning${warnings === 1 ? "" : "s"}`;
        } else {
          badge.className = "lint-ok";
          badge.innerHTML = `${icon("check")} Lint clean`;
        }
      }
      const dots = card.querySelectorAll("[data-stage-dot]");
      detail.workflow.stages.forEach((stage, index) => {
        if (stage.dependency && dots[index]) {
          dots[index].classList.add("is-dependency");
          dots[index].title = `${stage.name} · ${stage.dependency.connector}`;
        }
      });
    } catch {
      const badge = root.querySelector(
        `[data-workflow="${CSS.escape(workflow.name)}"] [data-lint]`,
      );
      if (badge) badge.innerHTML = `${icon("alert")} Lint unavailable`;
    }
  }
}

function runRow(run) {
  const meta = statusMeta(run.outcome || run.status);
  return `
    <tr tabindex="0" data-run-id="${h(run.id)}" aria-label="Open decision ${h(shortId(run.id))}">
      <td class="cell-id">${h(shortId(run.id))}</td>
      <td class="cell-workflow">${h(titleize(run.workflow))}<div class="mono faint">v${h(run.workflow_version)}</div></td>
      <td><span class="status-stamp status-${h(meta.cls)}">${h(meta.label)}</span></td>
      <td class="cell-stage">${h(run.current_stage || "—")}</td>
      <td class="cell-reason">${h(runReason(run))}</td>
      <td class="cell-time">${h(relativeTime(run.updated_at || run.created_at))}</td>
    </tr>
  `;
}

export function renderRuns(state) {
  const runs = runItems(state);
  const statusFilters = [
    ["all", "All"],
    ["approved", "Approved"],
    ["rejected", "Rejected"],
    ["manual_review", "Review"],
    ["retry_scheduled", "Retry"],
    ["failed", "Failed"],
  ];
  const workflows = workflowItems(state);

  const html = `
    ${pageHead({
      kicker: "Decision ledger",
      title: "Every run, on the record.",
      intro:
        "Search the ledger, open a decision and inspect the exact rule, field value and audit chain that produced it.",
      actions: `${refreshButton("Refresh ledger")}${composeButton()}`,
    })}
    ${
      runs.length
        ? `
      <div class="toolbar">
        <div class="search-wrap">
          ${icon("search")}
          <input class="input" id="run-search" type="search" placeholder="Search id, workflow, subject, reason…" aria-label="Search decisions" />
        </div>
        <div class="filter-group" role="group" aria-label="Filter by status">
          ${statusFilters
            .map(
              ([key, label], index) =>
                `<button class="chip" type="button" data-status-filter="${key}" aria-pressed="${index === 0 ? "true" : "false"}">${h(label)}</button>`,
            )
            .join("")}
        </div>
        <div class="toolbar-spacer"></div>
        <select class="select" id="run-workflow-filter" aria-label="Filter by workflow" style="width:190px;height:40px;font-size:.76rem">
          <option value="">All workflows</option>
          ${workflows.map((workflow) => `<option value="${h(workflow.name)}">${h(titleize(workflow.name))}</option>`).join("")}
        </select>
        <span class="mono faint" id="run-count">${runs.length} decisions</span>
      </div>
      <div class="table-wrap">
        <table class="run-table">
          <thead>
            <tr>
              <th scope="col">Decision</th>
              <th scope="col">Workflow</th>
              <th scope="col">Status</th>
              <th scope="col">Stage</th>
              <th scope="col">Reason</th>
              <th scope="col">Updated</th>
            </tr>
          </thead>
          <tbody id="run-rows">${runs.map(runRow).join("")}</tbody>
        </table>
      </div>
    `
        : emptyState({
            title: "The ledger is empty",
            body: "Record a decision or load the demo ledger to see the full audit trail in action.",
            actions: `${composeButton("Record a decision")}<button class="btn" type="button" data-action="seed-demo">${icon("spark")} Load demo ledger</button>`,
          })
    }
  `;

  return {
    html,
    mount(root, ctx) {
      const search = root.querySelector("#run-search");
      const workflowFilter = root.querySelector("#run-workflow-filter");
      const rows = root.querySelector("#run-rows");
      const count = root.querySelector("#run-count");
      const buttons = [...root.querySelectorAll("[data-status-filter]")];
      let activeStatus = "all";

      const filtered = () => {
        const query = (search ? search.value : "").trim().toLowerCase();
        const workflow = workflowFilter ? workflowFilter.value : "";
        return runs.filter((run) => {
          const status = run.outcome || run.status;
          if (activeStatus !== "all") {
            if (activeStatus === "manual_review" && status !== "manual_review") return false;
            if (activeStatus === "retry_scheduled" && status !== "retry_scheduled") return false;
            if (!["manual_review", "retry_scheduled"].includes(activeStatus) && status !== activeStatus) return false;
          }
          if (workflow && run.workflow !== workflow) return false;
          if (!query) return true;
          const haystack = [
            run.id,
            run.workflow,
            run.workflow_version,
            run.current_stage,
            status,
            runReason(run),
            runSubject(run),
            JSON.stringify(run.input || {}),
          ]
            .join(" ")
            .toLowerCase();
          return haystack.includes(query);
        });
      };

      const paint = () => {
        if (!rows) return;
        const visible = filtered();
        rows.innerHTML = visible.length
          ? visible.map(runRow).join("")
          : `<tr><td colspan="6"><div class="table-empty"><strong>No matching decisions</strong>Try a different status, workflow or search term.</div></td></tr>`;
        if (count) count.textContent = `${visible.length} of ${runs.length} decisions`;
      };

      if (search) search.addEventListener("input", paint);
      if (workflowFilter) workflowFilter.addEventListener("change", paint);
      buttons.forEach((button) => {
        button.addEventListener("click", () => {
          activeStatus = button.dataset.statusFilter;
          buttons.forEach((item) =>
            item.setAttribute("aria-pressed", String(item === button)),
          );
          paint();
        });
      });

      if (rows) {
        rows.addEventListener("click", (event) => {
          const row = event.target.closest("[data-run-id]");
          if (row) ctx.openRun(row.dataset.runId);
        });
        rows.addEventListener("keydown", (event) => {
          if (event.key !== "Enter" && event.key !== " ") return;
          const row = event.target.closest("[data-run-id]");
          if (!row) return;
          event.preventDefault();
          ctx.openRun(row.dataset.runId);
        });
      }

      root.querySelector("[data-action=refresh]")?.addEventListener("click", () =>
        ctx.refresh({ runs: true, render: true }),
      );
      root.querySelectorAll("[data-action=compose]").forEach((button) =>
        button.addEventListener("click", () => ctx.openCompose()),
      );
      const seed = root.querySelector("[data-action=seed-demo]");
      if (seed) seed.addEventListener("click", () => ctx.seedDemo(seed));
    },
  };
}

export function renderWorkflows(state) {
  const workflows = workflowItems(state);
  const html = `
    ${pageHead({
      kicker: "Workflow library",
      title: "Rules are data. Decisions are evidence.",
      intro:
        "Inspect every stage, condition and transition. Open a workflow to see exactly what the engine will evaluate.",
      actions: `${refreshButton("Reload view")}${composeButton()}`,
    })}
    ${
      workflows.length
        ? `<div class="workflow-grid">${workflows.map(workflowCard).join("")}</div>`
        : emptyState({
            title: "No workflows loaded",
            body: "Check the workflow YAML directory and reload the server configuration.",
            actions: refreshButton("Try again"),
          })
    }
  `;
  return {
    html,
    mount(root, ctx) {
      root.querySelectorAll("[data-workflow]").forEach((card) =>
        card.addEventListener("click", () => ctx.openWorkflow(card.dataset.workflow)),
      );
      root.querySelector("[data-action=refresh]")?.addEventListener("click", () =>
        ctx.refresh({ all: true, render: true }),
      );
      root.querySelectorAll("[data-action=compose]").forEach((button) =>
        button.addEventListener("click", () => ctx.openCompose()),
      );
      hydrateWorkflowCards(root, state);
    },
  };
}

function findingMarkup(finding, index) {
  return `
    <div class="finding finding-${h(finding.severity)}" style="--i:${index}">
      <span class="finding-code">${h(finding.code)}</span>
      <span class="finding-msg">${h(finding.message)}</span>
      <span class="finding-where">${h(findingLocation(finding) || "workflow")}</span>
    </div>
  `;
}

function findingsMarkup(lint) {
  if (!lint) return "";
  const summary = lint.summary || { error: 0, warning: 0, info: 0 };
  const findings = lint.findings || [];
  return `
    <div class="lab-summary">
      <span class="severity severity-error">${summary.error || 0} errors</span>
      <span class="severity severity-warning">${summary.warning || 0} warnings</span>
      <span class="severity severity-info">${summary.info || 0} info</span>
    </div>
    ${
      findings.length
        ? `<div class="finding-list">${findings.map(findingMarkup).join("")}</div>`
        : `<div class="empty-line">${icon("check")} No lint findings. The configuration is internally consistent.</div>`
    }
  `;
}

export function renderGovernance(state) {
  const workflows = workflowItems(state);
  const options = workflows
    .map((workflow) => `<option value="${h(workflow.name)}">${h(titleize(workflow.name))} · v${h(workflow.version)}</option>`)
    .join("");
  const first = workflows[0] ? workflows[0].name : "";
  const etag = state.workflows ? state.workflows.config_etag : "";

  const html = `
    ${pageHead({
      kicker: "Change lab",
      title: "Change the rules without breaking the truth.",
      intro:
        "Lint the live configuration, preview a reload before activation and shadow-evaluate a candidate against real decisions.",
      actions: `${refreshButton("Refresh config")}`,
    })}
    ${
      workflows.length
        ? `
      <div class="governance">
        <aside class="governance-nav" aria-label="Change lab sections">
          <p class="governance-nav-title">Three safety checks</p>
          <ol class="step-list">
            <li><a class="step-link is-active" href="#lab-lint" data-lab-link="lab-lint"><span class="step-num">01</span><span class="step-copy"><strong>Lint config</strong><small>Graph, thresholds, dangling references</small></span></a></li>
            <li><a class="step-link" href="#lab-reload" data-lab-link="lab-reload"><span class="step-num">02</span><span class="step-copy"><strong>Preview reload</strong><small>Validate and diff before activation</small></span></a></li>
            <li><a class="step-link" href="#lab-impact" data-lab-link="lab-impact"><span class="step-num">03</span><span class="step-copy"><strong>Shadow impact</strong><small>Compare outcomes on real inputs</small></span></a></li>
          </ol>
        </aside>
        <div class="governance-content">
          <section class="lab-panel" id="lab-lint">
            <div class="lab-head">
              <div class="lab-title">Static analysis <span class="mono">lint</span></div>
              <div class="lab-controls">
                <select class="select" id="lint-workflow" aria-label="Workflow to lint">${options}</select>
                <button class="btn btn-sm" type="button" data-action="lint-scan">${icon("scan")} Scan</button>
              </div>
            </div>
            <div class="lab-body" id="lint-result"></div>
          </section>

          <section class="lab-panel" id="lab-reload">
            <div class="lab-head">
              <div class="lab-title">Safe reload <span class="mono">dry run</span></div>
              <div class="lab-controls">
                <button class="btn btn-sm" type="button" data-action="reload-preview">${icon("eye")} Preview reload</button>
              </div>
            </div>
            <div class="lab-body" id="reload-result">
              <div class="etag-row">
                <span class="etag"><b>active etag</b> ${h(etag || "unavailable")}</span>
              </div>
              <div class="empty-line">Preview validates the YAML on disk without activating it. If anything is invalid, the current configuration stays live.</div>
            </div>
          </section>

          <section class="lab-panel" id="lab-impact">
            <div class="lab-head">
              <div class="lab-title">Shadow impact <span class="mono">candidate</span></div>
              <div class="lab-controls">
                <select class="select" id="impact-workflow" aria-label="Workflow to analyse">${options}</select>
              </div>
            </div>
            <div class="lab-body">
              <div class="impact-form">
                <div class="field">
                  <label class="field-label" for="impact-sample">Sample size <span class="field-hint">1–500</span></label>
                  <input class="input" id="impact-sample" type="number" min="1" max="500" value="50" />
                </div>
                <div class="field">
                  <label class="field-label">Inputs <span class="field-hint">history or request</span></label>
                  <label class="check-row" style="min-height:44px;margin:0"><input type="checkbox" id="impact-inputs" /><span class="check-copy">Return changed inputs</span></label>
                </div>
                <button class="btn btn-primary" type="button" data-action="run-impact">${icon("play")} Run impact</button>
              </div>
              <details class="candidate-box">
                <summary>Candidate configuration <span class="mono faint" id="impact-candidate-meta">active snapshot</span></summary>
                <textarea class="textarea" id="impact-candidate" spellcheck="false" aria-label="Candidate workflow JSON"></textarea>
                <div class="candidate-actions">
                  <button class="btn btn-sm btn-quiet" type="button" data-action="load-candidate">${icon("refresh")} Reset to active snapshot</button>
                  <span class="mono faint" id="impact-candidate-hint">Edit thresholds or rules, then run the shadow analysis.</span>
                </div>
              </details>
              <div id="impact-result"><div class="empty-line">Choose a workflow, adjust the candidate, and compare active vs. candidate outcomes.</div></div>
            </div>
          </section>
        </div>
      </div>
    `
        : emptyState({
            title: "No configuration to inspect",
            body: "The workflow directory is empty or failed to load.",
            actions: refreshButton("Try again"),
          })
    }
  `;

  return {
    html,
    mount(root, ctx) {
      if (!workflows.length) {
        root.querySelector("[data-action=refresh]")?.addEventListener("click", () =>
          ctx.refresh({ all: true, render: true }),
        );
        return;
      }

      root.querySelector("[data-action=refresh]")?.addEventListener("click", () =>
        ctx.refresh({ all: true, render: true }),
      );

      const links = [...root.querySelectorAll("[data-lab-link]")];
      links.forEach((link) => {
        link.addEventListener("click", (event) => {
          event.preventDefault();
          const target = root.querySelector(`#${link.dataset.labLink}`);
          links.forEach((item) => item.classList.toggle("is-active", item === link));
          target?.scrollIntoView({ behavior: "smooth", block: "start" });
        });
      });

      const lintSelect = root.querySelector("#lint-workflow");
      const lintResult = root.querySelector("#lint-result");
      const scanLint = async () => {
        if (!lintResult || !lintSelect) return;
        lintResult.innerHTML = `<div class="boot" style="padding:40px 0"><span class="spinner"></span><p>Scanning ${h(titleize(lintSelect.value))}</p></div>`;
        try {
          const result = await api.lint(lintSelect.value);
          if (!lintResult.isConnected) return;
          lintResult.innerHTML = findingsMarkup(result);
        } catch (error) {
          if (lintResult.isConnected) {
            lintResult.innerHTML = `<div class="empty-line">${h(errorText(error))}</div>`;
          }
        }
      };
      lintSelect?.addEventListener("change", scanLint);
      root.querySelector("[data-action=lint-scan]")?.addEventListener("click", scanLint);
      scanLint();

      const reloadResult = root.querySelector("#reload-result");
      let reloadPreview = null;
      const previewReload = async () => {
        reloadResult.innerHTML = `<div class="boot" style="padding:40px 0"><span class="spinner"></span><p>Validating candidate configuration</p></div>`;
        try {
          reloadPreview = await api.reload({ dryRun: true, ifMatch: etag });
          if (!reloadResult.isConnected) return;
          reloadResult.innerHTML = reloadMarkup(reloadPreview);
        } catch (error) {
          if (!reloadResult.isConnected) return;
          reloadPreview = null;
          reloadResult.innerHTML = `<div class="empty-line">${h(errorText(error))}</div>`;
        }
      };
      root.querySelector("[data-action=reload-preview]")?.addEventListener("click", previewReload);
      reloadResult?.addEventListener("click", async (event) => {
        const activate = event.target.closest("[data-action=activate-reload]");
        if (!activate) return;
        const originalEtag = etag;
        setBusy(activate, true, "Activating");
        try {
          const result = await api.reload({ dryRun: false, ifMatch: originalEtag });
          toast("Configuration activated", {
            type: "success",
            detail: `${shortHash(result.previous_etag, 10)} → ${shortHash(result.config_etag, 10)}`,
          });
          await ctx.refresh({ all: true, render: true });
        } catch (error) {
          toast("Activation failed", { type: "error", detail: errorText(error) });
          setBusy(activate, false);
        }
      });

      const impactSelect = root.querySelector("#impact-workflow");
      const candidate = root.querySelector("#impact-candidate");
      const candidateMeta = root.querySelector("#impact-candidate-meta");
      const impactResult = root.querySelector("#impact-result");
      const runImpactButton = root.querySelector("[data-action=run-impact]");
      let activeDetail = null;

      const loadCandidate = async (name) => {
        if (!candidate || !candidateMeta) return;
        candidateMeta.textContent = "loading…";
        candidate.value = "";
        try {
          activeDetail = await api.workflow(name);
          if (!candidate.isConnected) return;
          candidate.value = prettyJson(activeDetail.workflow);
          candidateMeta.textContent = `v${activeDetail.workflow.version} · ${activeDetail.lint.summary.error} lint errors`;
        } catch (error) {
          if (candidate.isConnected) {
            candidateMeta.textContent = "unavailable";
            candidate.value = "";
          }
        }
      };

      impactSelect?.addEventListener("change", () => loadCandidate(impactSelect.value));
      root.querySelector("[data-action=load-candidate]")?.addEventListener("click", () => {
        if (impactSelect) loadCandidate(impactSelect.value);
      });

      root.querySelector("[data-action=run-impact]")?.addEventListener("click", async () => {
        if (!candidate || !impactSelect || !impactResult) return;
        let parsed;
        try {
          parsed = JSON.parse(candidate.value);
          if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
            throw new Error("Candidate must be a JSON object.");
          }
        } catch (error) {
          impactResult.innerHTML = `<div class="empty-line">${h(
            error instanceof SyntaxError ? `Candidate JSON is invalid: ${error.message}` : error.message,
          )}</div>`;
          return;
        }
        setBusy(runImpactButton, true, "Simulating");
        try {
          const sampleSize = Number(root.querySelector("#impact-sample")?.value || 50);
          const payload = {
            candidate: parsed,
            sample_size: sampleSize,
            include_inputs: Boolean(root.querySelector("#impact-inputs")?.checked),
          };
          const result = await api.impact(impactSelect.value, payload);
          if (!impactResult.isConnected) return;
          impactResult.innerHTML = impactMarkup(result);
        } catch (error) {
          if (impactResult.isConnected) {
            impactResult.innerHTML = `<div class="empty-line">${h(errorText(error))}</div>`;
          }
        } finally {
          setBusy(runImpactButton, false);
        }
      });

      loadCandidate(first);
    },
  };
}

function diffMarkup(diff) {
  if (!diff || !diff.has_changes) {
    return `<div class="empty-line">${icon("check")} The candidate matches the active configuration exactly.</div>`;
  }
  const changed = diff.changed_workflows || [];
  return `
    <div class="diff-list">
      ${
        diff.added_workflows.length
          ? `<div class="diff-item"><span class="diff-name">Added workflows</span><span class="change-badge">${diff.added_workflows.map((item) => h(item)).join(", ")}</span></div>`
          : ""
      }
      ${
        diff.removed_workflows.length
          ? `<div class="diff-item"><span class="diff-name">Removed workflows</span><span class="diff-detail">${diff.removed_workflows.map((item) => h(item)).join(", ")}</span></div>`
          : ""
      }
      ${changed
        .map(
          (item) => `
            <div class="diff-item">
              <span class="diff-name">${h(titleize(item.name))}</span>
              <span class="diff-detail">v${h(item.version.from)} → v${h(item.version.to)} · ${h(item.rule_changes)} rule changes${
                item.changed_sections.length ? ` · ${h(item.changed_sections.join(", "))}` : ""
              }${item.added_stages.length ? ` · +${h(item.added_stages.join(", "))} stages` : ""}${
                item.removed_stages.length ? ` · −${h(item.removed_stages.join(", "))} stages` : ""
              }</span>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function reloadMarkup(preview) {
  const lintSummary = preview.lint ? preview.lint.totals : { error: 0, warning: 0, info: 0 };
  return `
    <div class="etag-row">
      <span class="etag"><b>active</b> ${h(preview.active_etag)}</span>
      <span class="etag"><b>candidate</b> ${h(preview.candidate_etag)}</span>
      <span class="change-badge">${preview.has_changes ? "changes found" : "no changes"}</span>
    </div>
    <div class="lab-summary">
      <span class="severity severity-error">${lintSummary.error || 0} errors</span>
      <span class="severity severity-warning">${lintSummary.warning || 0} warnings</span>
      <span class="severity severity-info">${lintSummary.info || 0} info</span>
    </div>
    ${diffMarkup(preview.diff)}
    ${
      preview.has_changes && (lintSummary.error || 0) === 0
        ? `<div class="candidate-actions" style="padding:14px 0 0"><button class="btn btn-primary btn-sm" type="button" data-action="activate-reload">${icon("play")} Activate candidate</button><span class="mono faint">Uses If-Match guard · atomic reload</span></div>`
        : preview.has_changes
          ? `<div class="empty-line">Fix lint errors before activation.</div>`
          : ""
    }
  `;
}

function matrixMarkup(matrix) {
  const rows = Object.keys(matrix || {}).sort();
  if (!rows.length) return `<div class="empty-line">No samples were available for comparison.</div>`;
  const preferred = ["approved", "manual_review", "rejected", "retry_scheduled", "failed", "schema_invalid"];
  const columns = [...new Set(rows.flatMap((row) => Object.keys(matrix[row] || {})))].sort(
    (a, b) => preferred.indexOf(a) - preferred.indexOf(b),
  );
  const max = Math.max(1, ...rows.flatMap((row) => Object.values(matrix[row] || {})));
  return `
    <div class="matrix-wrap">
      <div class="matrix" style="--cols:${columns.length}">
        <span class="matrix-cell head">active ↓ / candidate →</span>
        ${columns.map((column) => `<span class="matrix-cell head">${h(outcomeLabel(column))}</span>`).join("")}
        ${rows
          .map(
            (row) =>
              `<span class="matrix-cell rowhead">${h(outcomeLabel(row))}</span>${columns
                .map((column) => {
                  const value = (matrix[row] && matrix[row][column]) || 0;
                  return `<span class="matrix-cell data${row !== column && value ? " changed" : ""}" data-value="${value}" style="--intensity:${(value / max).toFixed(2)}">${value}</span>`;
                })
                .join("")}`,
          )
          .join("")}
      </div>
    </div>
  `;
}

function outcomeMini(title, outcomes) {
  return `
    <div class="outcome-mini">
      <h4>${h(title)}</h4>
      ${
        Object.keys(outcomes || {}).length
          ? Object.entries(outcomes)
              .sort(([a], [b]) => outcomeLabel(a).localeCompare(outcomeLabel(b)))
              .map(
                ([key, value]) =>
                  `<div class="outcome-mini-row"><span>${h(outcomeLabel(key))}</span><span>${h(value)}</span></div>`,
              )
              .join("")
          : `<div class="outcome-mini-row"><span>No outcomes</span><span>—</span></div>`
      }
    </div>
  `;
}

function impactMarkup(impact) {
  const changedExamples = impact.changed_examples || [];
  return `
    <div class="lab-summary">
      <span class="mono">sample ${h(impact.sample_size)} · ${h(impact.sample_source)}</span>
      <span class="change-badge">${h(impact.changed)} changed · ${h(formatPercent(impact.changed_percent, 1))}</span>
      <span class="mono faint">${h(shortHash(impact.active_hash, 8))} → ${h(shortHash(impact.candidate_hash, 8))}</span>
    </div>
    <div class="impact-grid">
      <div>
        <div class="subsection-title"><span>Transition matrix</span><span>active → candidate</span></div>
        ${matrixMarkup(impact.transition_matrix)}
      </div>
      <div class="impact-side">
        <div class="outcome-pair">
          ${outcomeMini("Active outcomes", impact.outcomes.active)}
          ${outcomeMini("Candidate outcomes", impact.outcomes.candidate)}
        </div>
        <div class="subsection-title" style="margin:4px 0 0"><span>Changed examples</span><span>${h(changedExamples.length)}</span></div>
        ${
          changedExamples.length
            ? `<div class="changed-list">${changedExamples
                .map(
                  (example) => `
                    <div class="changed-item">
                      <div class="changed-item-head"><span>${h(example.run_id ? shortId(example.run_id, 10) : `sample ${example.index + 1}`)}</span><span>${h(example.baseline.decision_stage || "—")} → ${h(example.candidate.decision_stage || "—")}</span></div>
                      <div class="changed-route">${h(outcomeLabel(example.baseline.outcome))} ${icon("arrowRight")} ${h(outcomeLabel(example.candidate.outcome))}</div>
                      <div class="mono faint" style="margin-top:5px;line-height:1.5">${h(example.candidate.reason || example.baseline.reason || "")}</div>
                      ${
                        (example.schema_errors || []).length
                          ? `<div class="mono" style="margin-top:5px;color:var(--reject)">${example.schema_errors
                              .map((item) => `${h(item.path || "input")}: ${h(item.message || item.code)}`)
                              .join(" · ")}</div>`
                          : ""
                      }
                    </div>
                  `,
                )
                .join("")}</div>`
            : `<div class="empty-line">${icon("check")} Candidate produces identical outcomes on this sample.</div>`
        }
      </div>
    </div>
    <div class="subsection">
      <div class="subsection-title"><span>Candidate lint</span><span>${h(impact.candidate_lint ? impact.candidate_lint.summary.error : 0)} errors</span></div>
      ${findingsMarkup(impact.candidate_lint)}
    </div>
  `;
}

function drawerShell({ kicker, title, sub, actions = "", body, foot = "" }) {
  return `
    <div class="drawer-shell">
      <header class="drawer-head">
        <div style="min-width:0">
          <div class="drawer-kicker">${kicker}</div>
          <h2 class="drawer-title">${title}</h2>
          <p class="drawer-sub">${sub}</p>
        </div>
        <div class="drawer-actions">
          ${actions}
          <button class="icon-btn" type="button" data-close aria-label="Close panel">${icon("x")}</button>
        </div>
      </header>
      <div class="drawer-body">${body}</div>
      ${foot ? `<footer class="drawer-foot">${foot}</footer>` : ""}
    </div>
  `;
}

function loadingBody(label) {
  return `<div class="boot" style="padding:80px 0"><div class="boot-mark" aria-hidden="true"></div><p>${h(label)}</p></div>`;
}

function counterfactualMarkup(cf) {
  if (!cf) return "";
  return `
    <div class="counterfactual">
      <div class="counterfactual-top">
        <span class="counterfactual-field">${h(cf.field)}</span>
        <span class="counterfactual-gap">${cf.gap === null || cf.gap === undefined ? "flip" : `gap ${h(cf.gap)}`}</span>
      </div>
      <div class="counterfactual-action">${h(cf.action)}</div>
      <div class="counterfactual-meta">${h(cf.mode)} · actual ${h(JSON.stringify(cf.actual))} · required ${h(cf.required)}</div>
    </div>
  `;
}

function stageTimeline(stages) {
  if (!stages || !stages.length) {
    return `<div class="empty-line">No stage summaries yet. The run has not reached a decision.</div>`;
  }
  return `
    <div class="stage-timeline">
      ${stages
        .map(
          (stage) => `
            <div class="stage-item" data-status="${h(stage.status || "")}">
              <span class="stage-marker"></span>
              <div>
                <div class="stage-name">${h(stage.stage || "stage")} <span class="mono faint">${h(stage.status || "")}</span></div>
                <div class="stage-meta">${h(stage.reason || "No reason recorded")}${stage.next_stage ? ` · next: ${h(stage.next_stage)}` : ""}</div>
                ${stage.decided_by ? `<div class="stage-rule">decided by ${h(stage.decided_by)}</div>` : ""}
              </div>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function ruleTraceMarkup(trace, index) {
  return `
    <details class="trace" ${trace.decided || trace.triggered ? "open" : ""}>
      <summary>
        <span class="trace-id">${h(trace.rule_id || `rule-${index + 1}`)}</span>
        <span class="trace-desc">${h(trace.description || trace.reason || "Rule evaluation")}</span>
        <span class="trace-badge ${trace.triggered ? "triggered" : ""}">${trace.triggered ? "triggered" : "clear"}</span>
        ${trace.decided ? '<span class="trace-badge triggered">decided</span>' : ""}
      </summary>
      <div class="trace-body">
        <dl class="kv">
          <dt>stage</dt><dd>${h(trace.stage || "—")}</dd>
          <dt>mode → effect</dt><dd>${h(trace.mode || "—")} → ${h(trace.effect || "—")}</dd>
          <dt>condition</dt><dd class="mono">${h(conditionText(trace.condition))}</dd>
          <dt>condition result</dt><dd>${trace.condition_result ? "passed" : "failed"}${trace.evaluation_error ? ` · ${h(trace.evaluation_error)}` : ""}</dd>
          <dt>reason</dt><dd>${h(trace.reason || "—")}</dd>
        </dl>
        ${trace.counterfactual ? `<div class="subsection-title"><span>Counterfactual</span></div>${counterfactualMarkup(trace.counterfactual)}` : ""}
        <div class="subsection-title"><span>Captured fields</span></div>
        ${jsonBlock(trace.field_values || {})}
      </div>
    </details>
  `;
}

function integrityMarkup(integrity) {
  const valid = integrity && integrity.valid;
  return `
    <div class="integrity ${valid ? "" : "is-broken"}" id="audit-integrity">
      <span class="integrity-mark">${icon(valid ? "shield" : "alert")}</span>
      <div>
        <div class="integrity-title">${valid ? "Audit chain verified" : "Audit chain broken"}</div>
        <div class="integrity-sub">${h(
          valid
            ? `${integrity.event_count} events · head ${shortHash(integrity.head_hash, 18)}`
            : integrity.reason || "The event stream failed verification.",
        )}</div>
      </div>
      <button class="btn btn-sm btn-quiet" type="button" data-action="verify-audit">${icon("scan")} Verify</button>
    </div>
  `;
}

function auditEventsMarkup(events) {
  if (!events || !events.length) return `<div class="empty-line">No audit events.</div>`;
  return `
    <div class="event-list">
      ${events
        .map(
          (event) => `
            <div class="event">
              <span class="event-seq">#${h(event.seq)}</span>
              <div>
                <div class="event-type">${h(titleize(event.event_type))}</div>
                <div class="event-meta">${h(event.stage || "run")} · ${h(formatDate(event.created_at))}</div>
                <details>
                  <summary>Payload & hashes</summary>
                  ${jsonBlock({
                    payload: event.payload,
                    prev_hash: event.prev_hash,
                    event_hash: event.event_hash,
                  })}
                </details>
              </div>
              <span class="event-outcome">${event.outcome ? h(outcomeLabel(event.outcome)) : ""}</span>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function decisionPanel(run, explanation, audit) {
  const result = run.result || {};
  const decision = explanation.decision || {};
  const outcome = run.outcome || run.status;
  const counterfactuals = explanation.counterfactuals || [];
  const showReview = run.status === "manual_review";
  return `
    <div class="tab-panel" id="panel-decision">
      <div class="decision-summary">
        <div class="decision-summary-top">
          <div>
            <div class="mono faint" style="color:rgba(251,252,247,.5);letter-spacing:.08em;text-transform:uppercase">Final outcome</div>
            <div class="decision-outcome">${h(outcomeLabel(outcome))}</div>
          </div>
          <span class="mono" style="color:rgba(251,252,247,.65)">${h(shortId(run.id, 12))}</span>
        </div>
        <p class="decision-reason">${h(result.reason || (run.status === "running" ? "Execution is still in progress." : "No decision reason recorded."))}</p>
        <div class="decision-meta">
          <span>stage ${h(decision.stage || run.current_stage || "—")}</span>
          <span>rule ${h(decision.rule_id || result.decided_by || "—")}</span>
          <span>decided ${h(relativeTime(decision.decided_at || run.updated_at))}</span>
          <span>snapshot ${h(shortHash(run.workflow_hash, 10))}</span>
        </div>
      </div>

      ${
        counterfactuals.length
          ? `<div class="subsection"><div class="subsection-title"><span>Closest path to a different outcome</span><span>${counterfactuals.length}</span></div><div class="counterfactuals">${counterfactuals.map(counterfactualMarkup).join("")}</div></div>`
          : ""
      }

      ${
        showReview
          ? `
            <div class="subsection">
              <div class="subsection-title"><span>Manual review</span><span>human decision required</span></div>
              <form class="review-form" id="review-form">
                <div class="field">
                  <label class="field-label" for="reviewer">Reviewer <span class="field-hint">required</span></label>
                  <input class="input" id="reviewer" name="reviewer" value="Reviewer" required />
                </div>
                <div class="field">
                  <label class="field-label" for="review-comment">Comment <span class="field-hint">optional</span></label>
                  <textarea class="textarea" id="review-comment" name="comment" style="min-height:74px" placeholder="Why is this decision being resolved?"></textarea>
                </div>
                <div class="review-actions">
                  <button class="btn btn-approve" type="submit" data-decision="approve">${icon("check")} Approve run</button>
                  <button class="btn btn-reject" type="submit" data-decision="reject">${icon("x")} Reject run</button>
                </div>
              </form>
            </div>
          `
          : ""
      }

      <div class="subsection">
        <div class="subsection-title"><span>Stage timeline</span><span>${h((explanation.stages || []).length)} entries</span></div>
        ${stageTimeline(explanation.stages || result.stage_results || [])}
      </div>

      <div class="subsection">
        <div class="subsection-title"><span>Input snapshot</span><span>${h(run.workflow_version)}</span></div>
        ${jsonBlock(explanation.input || run.input)}
      </div>
    </div>
  `;
}

function rulesPanel(audit, explanation) {
  const traceEvents = (audit.events || []).filter(
    (event) => event.event_type === "rule_evaluated",
  );
  const traces = traceEvents.length
    ? traceEvents.map((event) => ({ ...event.payload, stage: event.stage }))
    : (explanation.rule_traces || []).map((trace) => ({ ...trace, stage: trace.stage || explanation.decision.stage }));
  const dependencyEvents = (audit.events || []).filter(
    (event) => event.event_type === "dependency_attempt",
  );
  return `
    <div class="tab-panel" id="panel-rules" hidden>
      <div class="subsection" style="margin-top:0">
        <div class="subsection-title"><span>Rule evaluations</span><span>${h(traces.length)} traces</span></div>
        ${traces.length ? `<div class="trace-list">${traces.map(ruleTraceMarkup).join("")}</div>` : `<div class="empty-line">No rule traces were recorded.</div>`}
      </div>
      ${
        dependencyEvents.length
          ? `<div class="subsection"><div class="subsection-title"><span>Dependency attempts</span><span>${h(dependencyEvents.length)}</span></div><div class="trace-list">${dependencyEvents
              .map(
                (event) => `
                  <details class="trace">
                    <summary>
                      <span class="trace-id">${h(event.payload.connector || "connector")}</span>
                      <span class="trace-desc">attempt ${h(event.payload.attempt)} · ${h(event.stage || "")}</span>
                      <span class="trace-badge ${event.payload.ok ? "triggered" : ""}">${event.payload.ok ? "ok" : "failed"}</span>
                    </summary>
                    <div class="trace-body">${jsonBlock(event.payload)}</div>
                  </details>
                `,
              )
              .join("")}</div></div>`
          : ""
      }
      <div class="subsection">
        <div class="subsection-title"><span>Decision contract</span><span>explanation</span></div>
        ${jsonBlock({
          decision: explanation.decision,
          counterfactuals: explanation.counterfactuals,
        })}
      </div>
    </div>
  `;
}

function auditPanel(audit) {
  return `
    <div class="tab-panel" id="panel-audit" hidden>
      ${integrityMarkup(audit.integrity)}
      <div class="etag-row" style="margin-top:12px">
        <span class="etag"><b>events</b> ${h((audit.events || []).length)}</span>
        <span class="etag"><b>workflow hash</b> ${h(shortHash(audit.workflow_hash, 18))}</span>
        <span class="etag"><b>anchor</b> ${h(audit.integrity.anchor ? audit.integrity.anchor.event_count : "—")}</span>
      </div>
      <div class="subsection-title"><span>Event stream</span><span>SHA-256 chained</span></div>
      ${auditEventsMarkup(audit.events)}
    </div>
  `;
}

export function openRunDrawer(runId, state, ctx, { onClose } = {}) {
  let closeDrawer = null;
  let activeTab = "decision";
  const handle = openDialog({
    className: "drawer",
    html: drawerShell({
      kicker: `<span class="mono">Decision</span>`,
      title: "Loading decision",
      sub: h(runId),
      body: loadingBody("Reconstructing the decision"),
    }),
    onClose,
    onMount(dialog, close) {
      closeDrawer = close;
      load(dialog);
    },
  });

  async function load(dialog) {
    if (!dialog.isConnected) return;
    const body = dialog.querySelector(".drawer-body");
    if (!body) return;
    body.innerHTML = loadingBody("Reading decision, explanation and audit chain");
    try {
      const [run, explanation, audit] = await Promise.all([
        api.run(runId),
        api.explanation(runId),
        api.audit(runId),
      ]);
      if (!dialog.isConnected) return;
      const meta = statusMeta(run.outcome || run.status);
      dialog.innerHTML = drawerShell({
        kicker: `${statusStamp(run.outcome || run.status)}<span class="mono">${h(shortId(run.id, 10))}</span>`,
        title: h(titleize(run.workflow)),
        sub: `${h(run.workflow_version)} · ${h(runSubject(run))} · ${h(shortHash(run.workflow_hash, 14))}`,
        actions: `
          <button class="icon-btn" type="button" data-action="copy-run" title="Copy run id" aria-label="Copy run id">${icon("copy")}</button>
          <button class="icon-btn" type="button" data-action="reload-run" title="Refresh decision" aria-label="Refresh decision">${icon("refresh")}</button>
          ${["retry_scheduled", "running", "received"].includes(run.status) ? `<button class="btn btn-sm" type="button" data-action="resume-run">${icon("play")} Resume</button>` : ""}
        `,
        body: `
          <div class="tabs" role="tablist" aria-label="Decision sections">
            <button class="tab" type="button" role="tab" data-tab="decision" aria-selected="${activeTab === "decision"}">Decision</button>
            <button class="tab" type="button" role="tab" data-tab="rules" aria-selected="${activeTab === "rules"}">Rules <span class="mono faint">${(audit.events || []).filter((event) => event.event_type === "rule_evaluated").length}</span></button>
            <button class="tab" type="button" role="tab" data-tab="audit" aria-selected="${activeTab === "audit"}">Audit</button>
          </div>
          ${decisionPanel(run, explanation, audit)}
          ${rulesPanel(audit, explanation)}
          ${auditPanel(audit)}
        `,
        foot: `<span class="mono faint" style="margin-right:auto">status ${h(meta.label.toLowerCase())} · updated ${h(relativeTime(run.updated_at))}</span>`,
      });
      dialog.querySelectorAll(".tab-panel").forEach((panel) => {
        panel.hidden = panel.id !== `panel-${activeTab}`;
      });
      bind(dialog, run, audit);
    } catch (error) {
      if (!dialog.isConnected) return;
      body.innerHTML = `
        <div class="empty-state" style="padding:32px">
          <h3>Could not load this decision</h3>
          <p>${h(errorText(error))}</p>
          <div class="empty-actions"><button class="btn" type="button" data-action="reload-run">${icon("refresh")} Try again</button></div>
        </div>
      `;
      dialog.querySelector("[data-action=reload-run]")?.addEventListener("click", () => load(dialog));
    }
  }

  function bind(dialog, run, audit) {
    dialog.querySelectorAll("[data-tab]").forEach((tab) => {
      tab.addEventListener("click", () => {
        activeTab = tab.dataset.tab;
        dialog.querySelectorAll("[data-tab]").forEach((item) => {
          item.setAttribute("aria-selected", String(item === tab));
        });
        dialog.querySelectorAll(".tab-panel").forEach((panel) => {
          const show = panel.id === `panel-${activeTab}`;
          panel.hidden = !show;
          if (show) {
            panel.classList.remove("is-entering");
            void panel.offsetWidth;
            panel.classList.add("is-entering");
          }
        });
      });
    });

    dialog.querySelector("[data-action=copy-run]")?.addEventListener("click", () =>
      copyText(run.id, "Run id copied"),
    );
    dialog.querySelector("[data-action=reload-run]")?.addEventListener("click", () =>
      load(dialog),
    );

    const resume = dialog.querySelector("[data-action=resume-run]");
    resume?.addEventListener("click", async () => {
      setBusy(resume, true, "Resuming");
      try {
        const updated = await api.resumeRun(run.id);
        toast("Run resumed", {
          type: "success",
          detail: `${titleize(updated.workflow)} is now ${updated.status.replace("_", " ")}`,
        });
        await ctx.refresh({ runs: true, render: false });
        await load(dialog);
      } catch (error) {
        toast("Could not resume run", { type: "error", detail: errorText(error) });
        setBusy(resume, false);
      }
    });

    bindVerify(dialog, run);

    const reviewForm = dialog.querySelector("#review-form");
    reviewForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitter = event.submitter;
      const decision = submitter?.dataset.decision;
      if (!decision) return;
      const reviewer = reviewForm.querySelector("#reviewer").value.trim();
      const comment = reviewForm.querySelector("#review-comment").value.trim();
      if (!reviewer) {
        toast("Reviewer is required", { type: "warn" });
        return;
      }
      setBusy(submitter, true, decision === "approve" ? "Approving" : "Rejecting");
      try {
        const updated = await api.resolveRun(run.id, { decision, reviewer, comment });
        toast(`Run ${updated.status}`, {
          type: updated.status === "approved" ? "success" : "warn",
          detail: `Resolved by ${reviewer}`,
        });
        await ctx.refresh({ runs: true, render: false });
        await load(dialog);
      } catch (error) {
        toast("Could not resolve review", { type: "error", detail: errorText(error) });
        setBusy(submitter, false);
      }
    });
  }

  function bindVerify(dialog, run) {
    const verify = dialog.querySelector("[data-action=verify-audit]");
    if (!verify || verify.dataset.bound) return;
    verify.dataset.bound = "1";
    verify.addEventListener("click", async () => {
      setBusy(verify, true, "Verifying");
      try {
        const integrity = await api.verifyAudit(run.id);
        const current = dialog.querySelector("#audit-integrity");
        if (current) current.outerHTML = integrityMarkup(integrity);
        bindVerify(dialog, run);
        if (integrity.valid) {
          toast("Audit chain verified", {
            type: "success",
            detail: `${integrity.event_count} events · head ${shortHash(integrity.head_hash, 12)}`,
          });
        } else {
          toast("Audit chain failed verification", {
            type: "error",
            detail: integrity.reason || "The chain may have been tampered with.",
          });
        }
      } catch (error) {
        toast("Could not verify audit", { type: "error", detail: errorText(error) });
        setBusy(verify, false);
      }
    });
  }

  void closeDrawer;
  return handle;
}

function workflowTransition(stage) {
  const parts = [];
  if (stage.on_pass?.goto) parts.push(`pass → ${stage.on_pass.goto}`);
  if (stage.on_pass?.outcome) parts.push(`pass → ${outcomeLabel(stage.on_pass.outcome)}`);
  if (stage.on_fail?.goto) parts.push(`fail → ${stage.on_fail.goto}`);
  if (stage.on_fail?.outcome) parts.push(`fail → ${outcomeLabel(stage.on_fail.outcome)}`);
  for (const [key, transition] of Object.entries(stage.on || {})) {
    if (transition.goto) parts.push(`${key} → ${transition.goto}`);
    if (transition.outcome) parts.push(`${key} → ${outcomeLabel(transition.outcome)}`);
  }
  return parts.join(" · ");
}

function workflowRule(rule) {
  return `
    <details class="trace">
      <summary>
        <span class="trace-id">${h(rule.id)}</span>
        <span class="trace-desc">${h(rule.description || rule.reason || "No description")}</span>
        <span class="trace-badge">${h(rule.effect)}</span>
      </summary>
      <div class="trace-body">
        <dl class="kv">
          <dt>severity / mode</dt><dd>${h(rule.severity)} / ${h(rule.mode)}</dd>
          <dt>condition</dt><dd class="mono">${h(conditionText(rule.condition))}</dd>
          <dt>effect</dt><dd>${h(rule.effect)}${rule.goto ? ` → ${h(rule.goto)}` : ""}</dd>
          <dt>reason</dt><dd>${h(rule.reason || "—")}</dd>
        </dl>
      </div>
    </details>
  `;
}

export function openWorkflowDrawer(name, state, ctx, { onClose } = {}) {
  let closeDrawer = null;
  const handle = openDialog({
    className: "drawer",
    html: drawerShell({
      kicker: `<span class="mono">Workflow</span>`,
      title: "Loading workflow",
      sub: h(name),
      body: loadingBody("Reading the declarative workflow"),
    }),
    onClose,
    onMount(dialog, close) {
      closeDrawer = close;
      load(dialog);
    },
  });

  async function load(dialog) {
    try {
      const detail = await api.workflow(name);
      if (!dialog.isConnected) return;
      const workflow = detail.workflow;
      const ruleCount = workflow.stages.reduce((total, stage) => total + stage.rules.length, 0);
      const dependencyCount = workflow.stages.filter((stage) => stage.dependency).length;
      const lintBadge =
        detail.lint.summary.error > 0
          ? `<span class="lint-error">${icon("alert")} ${detail.lint.summary.error} errors</span>`
          : detail.lint.summary.warning > 0
            ? `<span class="lint-warn">${icon("alert")} ${detail.lint.summary.warning} warnings</span>`
            : `<span class="lint-ok">${icon("check")} Lint clean</span>`;
      dialog.innerHTML = drawerShell({
        kicker: `<span class="mono">Workflow</span>${lintBadge}`,
        title: h(titleize(workflow.name)),
        sub: `v${h(workflow.version)} · ${h(workflow.stages.length)} stages · ${h(ruleCount)} rules · ${h(shortHash(detail.snapshot_hash, 16))}`,
        actions: `
          <button class="icon-btn" type="button" data-action="copy-workflow-hash" title="Copy snapshot hash" aria-label="Copy snapshot hash">${icon("copy")}</button>
          <button class="btn btn-sm" type="button" data-action="workflow-compose">${icon("plus")} Compose</button>
          <button class="btn btn-sm btn-ghost" type="button" data-action="workflow-lab">${icon("sliders")} Change lab</button>
        `,
        body: `
          <p class="page-intro" style="margin:0 0 18px;max-width:none">${h(workflow.description || "No description supplied.")}</p>
          <div class="panel">
            <div class="panel-body">
              <dl class="kv">
                <dt>entry stage</dt><dd>${h(workflow.entry_stage || "—")}</dd>
                <dt>stages / rules</dt><dd>${h(workflow.stages.length)} / ${h(ruleCount)}</dd>
                <dt>dependencies</dt><dd>${h(dependencyCount)}</dd>
                <dt>retry budget</dt><dd>${h(workflow.retry.max_workflow_attempts)} attempts · ${h(workflow.retry.workflow_backoff_seconds)}s backoff</dd>
                <dt>snapshot hash</dt><dd class="mono">${h(detail.snapshot_hash)}</dd>
              </dl>
            </div>
          </div>
          <div class="subsection">
            <div class="subsection-title"><span>Lint findings</span><span>${h(detail.lint.findings.length)}</span></div>
            ${findingsMarkup(detail.lint)}
          </div>
          <div class="subsection">
            <div class="subsection-title"><span>Stages</span><span>${h(workflow.stages.length)}</span></div>
            <div class="stage-timeline">
              ${workflow.stages
                .map(
                  (stage) => `
                    <div class="stage-item" data-status="${stage.dependency ? "branched" : ""}">
                      <span class="stage-marker"></span>
                      <div>
                        <div class="stage-name">${h(stage.name)} ${stage.dependency ? `<span class="trace-badge triggered">dependency</span>` : ""}</div>
                        <div class="stage-meta">${h(stage.description || "No stage description")}</div>
                        ${stage.dependency ? `<div class="stage-rule">${h(stage.dependency.connector)} · ${h(stage.dependency.operation)} · failure policy ${h(stage.dependency.failure_policy)}</div>` : ""}
                        <div class="trace-list" style="margin-top:8px">${stage.rules.map(workflowRule).join("")}</div>
                        ${workflowTransition(stage) ? `<div class="stage-rule">${h(workflowTransition(stage))}</div>` : ""}
                      </div>
                    </div>
                  `,
                )
                .join("")}
            </div>
          </div>
          <div class="subsection">
            <div class="subsection-title"><span>Input schema</span><span>JSON Schema subset</span></div>
            ${jsonBlock(workflow.input_schema)}
          </div>
        `,
      });
      dialog.querySelector("[data-action=copy-workflow-hash]")?.addEventListener("click", () =>
        copyText(detail.snapshot_hash, "Snapshot hash copied"),
      );
      dialog.querySelector("[data-action=workflow-compose]")?.addEventListener("click", () => {
        closeDrawer?.();
        ctx.openCompose(workflow.name);
      });
      dialog.querySelector("[data-action=workflow-lab]")?.addEventListener("click", () => {
        closeDrawer?.();
        ctx.navigate("#/governance");
      });
    } catch (error) {
      if (!dialog.isConnected) return;
      dialog.innerHTML = drawerShell({
        kicker: `<span class="mono">Workflow</span>`,
        title: "Could not load workflow",
        sub: h(name),
        body: `<div class="empty-line">${h(errorText(error))}</div>`,
      });
    }
  }

  return handle;
}

export function openCompose(state, ctx, { workflow } = {}) {
  const workflows = workflowItems(state);
  if (!workflows.length) {
    toast("No workflows loaded", { type: "warn" });
    return;
  }
  const selected = workflows.some((item) => item.name === workflow)
    ? workflow
    : workflows[0].name;
  const options = workflows
    .map(
      (item) =>
        `<option value="${h(item.name)}" ${item.name === selected ? "selected" : ""}>${h(titleize(item.name))} · v${h(item.version)}</option>`,
    )
    .join("");

  let connector = null;
  let connectorReady = false;
  let closed = false;

  const handle = openDialog({
    className: "modal",
    html: `
      <div class="modal-shell">
        <header class="modal-head">
          <div>
            <h2 class="modal-title">Record a decision</h2>
            <p class="modal-sub">Submit a JSON payload. The engine pins this request to the active workflow snapshot.</p>
          </div>
          <button class="icon-btn" type="button" data-close aria-label="Close composer">${icon("x")}</button>
        </header>
        <form id="compose-form">
          <div class="modal-body">
            <div class="field">
              <label class="field-label" for="compose-workflow">Workflow <span class="field-hint" id="compose-version"></span></label>
              <select class="select" id="compose-workflow">${options}</select>
            </div>
            <div class="field" style="margin-top:13px">
              <label class="field-label" for="compose-input">Payload JSON <span class="field-hint">validated against the workflow schema</span></label>
              <textarea class="textarea" id="compose-input" spellcheck="false" autofocus></textarea>
              <div class="field-error" id="compose-error" role="alert" hidden></div>
            </div>
            <div class="field" style="margin-top:13px">
              <label class="field-label" for="compose-key">Idempotency key <span class="field-hint">optional · replays the original result</span></label>
              <input class="input" id="compose-key" placeholder="e.g. app-2026-0001" autocomplete="off" />
            </div>
            <label class="check-row" style="margin-top:13px">
              <input type="checkbox" id="compose-fault" disabled />
              <span class="check-copy">Simulate a dependency failure<small id="compose-fault-hint">Loading dependency information…</small></span>
            </label>
          </div>
          <footer class="modal-foot">
            <button class="btn btn-ghost" type="button" data-close>Cancel</button>
            <button class="btn btn-primary" type="submit" id="compose-submit">${icon("play")} Run decision</button>
          </footer>
        </form>
      </div>
    `,
    onClose() {
      closed = true;
    },
    onMount(dialog) {
      const workflowSelect = dialog.querySelector("#compose-workflow");
      const input = dialog.querySelector("#compose-input");
      const keyInput = dialog.querySelector("#compose-key");
      const fault = dialog.querySelector("#compose-fault");
      const faultHint = dialog.querySelector("#compose-fault-hint");
      const version = dialog.querySelector("#compose-version");
      const errorBox = dialog.querySelector("#compose-error");
      const form = dialog.querySelector("#compose-form");
      const submit = dialog.querySelector("#compose-submit");

      const showError = (message) => {
        errorBox.hidden = false;
        errorBox.textContent = message;
      };

      const clearError = () => {
        errorBox.hidden = true;
        errorBox.textContent = "";
      };

      const chooseWorkflow = async (name) => {
        const summary = workflows.find((item) => item.name === name);
        if (version) version.textContent = summary ? `active v${summary.version}` : "";
        input.value = prettyJson(sampleInput(name));
        keyInput.value = "";
        clearError();
        connector = null;
        connectorReady = false;
        fault.checked = false;
        fault.disabled = true;
        faultHint.textContent = "Loading dependency information…";
        try {
          const detail = await api.workflow(name);
          if (closed) return;
          const stage = detail.workflow.stages.find((item) => item.dependency);
          connector = stage ? stage.dependency.connector : null;
          connectorReady = true;
          if (connector) {
            fault.disabled = false;
            faultHint.textContent = `Inject a simulated error into ${connector}. The configured failure policy still applies.`;
          } else {
            faultHint.textContent = "This workflow has no external dependency to fault.";
          }
        } catch {
          if (closed) return;
          connectorReady = true;
          faultHint.textContent = "Dependency information unavailable; fault simulation is disabled.";
        }
      };

      workflowSelect.addEventListener("change", () => chooseWorkflow(workflowSelect.value));

      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        clearError();
        let payload;
        try {
          payload = JSON.parse(input.value);
          if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
            throw new Error("The payload must be a JSON object.");
          }
        } catch (error) {
          showError(error instanceof SyntaxError ? `Invalid JSON: ${error.message}` : error.message);
          input.focus();
          return;
        }

        const simulation =
          fault.checked && connector && connectorReady
            ? { faults: { [connector]: "error" } }
            : undefined;

        setBusy(submit, true, "Recording");
        try {
          const run = await api.createRun({
            workflow: workflowSelect.value,
            input: payload,
            simulation,
            idempotencyKey: keyInput.value.trim() || undefined,
          });
          toast(`Decision ${outcomeLabel(run.outcome || run.status).toLowerCase()}`, {
            type: run.status === "failed" || run.status === "rejected" ? "warn" : "success",
            detail: `${titleize(run.workflow)} · ${shortId(run.id, 10)}${simulation ? " · simulated fault" : ""}`,
          });
          handle.close();
          await ctx.refresh({ runs: true, render: false });
          ctx.openRun(run.id);
        } catch (error) {
          showError(errorText(error));
          setBusy(submit, false);
        }
      });

      chooseWorkflow(selected);
    },
  });

  return handle;
}

function paletteItem(command, index) {
  return `
    <button class="palette-item" type="button" data-command-index="${index}" aria-selected="${index === 0}">
      ${icon(command.icon || "arrowRight")}
      <span class="palette-item-main">${h(command.label)}</span>
      ${command.meta ? `<span class="palette-item-meta">${h(command.meta)}</span>` : ""}
    </button>
  `;
}

export function openPalette(state, ctx) {
  const existing = document.querySelector("dialog.palette[open]");
  if (existing) {
    existing.querySelector(".palette-input")?.focus();
    return;
  }

  const commands = [
    { group: "Navigate", label: "Overview", meta: "G O", icon: "pulse", action: () => ctx.navigate("#/overview") },
    { group: "Navigate", label: "Decision ledger", meta: "G D", icon: "list", action: () => ctx.navigate("#/runs") },
    { group: "Navigate", label: "Workflow library", meta: "G W", icon: "layers", action: () => ctx.navigate("#/workflows") },
    { group: "Navigate", label: "Change lab", meta: "G C", icon: "shield", action: () => ctx.navigate("#/governance") },
    { group: "Actions", label: "Record a decision", meta: "N", icon: "plus", action: () => ctx.openCompose() },
    { group: "Actions", label: "Load demo ledger", meta: "seed", icon: "spark", action: () => ctx.seedDemo() },
    ...workflowItems(state).map((workflow) => ({
      group: "Workflows",
      label: titleize(workflow.name),
      meta: `v${workflow.version}`,
      icon: "layers",
      action: () => ctx.openWorkflow(workflow.name),
    })),
    ...runItems(state)
      .slice(0, 5)
      .map((run) => ({
        group: "Recent decisions",
        label: `${titleize(run.workflow)} · ${runSubject(run)}`,
        meta: shortId(run.id, 8),
        icon: "arrowUpRight",
        action: () => ctx.openRun(run.id),
      })),
  ];

  let filtered = commands;
  let selected = 0;

  const handle = openDialog({
    className: "palette",
    html: `
      <div class="palette-input-wrap">
        ${icon("search")}
        <input class="palette-input" id="palette-input" type="text" placeholder="Search commands, workflows and decisions…" autofocus aria-label="Command palette search" aria-controls="palette-list" />
      </div>
      <div class="palette-list" id="palette-list" role="listbox" aria-label="Commands"></div>
      <div class="palette-foot">
        <span><kbd>↑</kbd> <kbd>↓</kbd> navigate</span>
        <span><kbd>↵</kbd> open</span>
        <span><kbd>esc</kbd> close</span>
        <span style="margin-left:auto">${h(commands.length)} commands</span>
      </div>
    `,
    onMount(dialog) {
      const input = dialog.querySelector("#palette-input");
      const list = dialog.querySelector("#palette-list");

      const execute = (index) => {
        const command = filtered[index];
        if (!command) return;
        handle.close();
        command.action();
      };

      const renderList = () => {
        const groups = [];
        filtered.forEach((command, index) => {
          let group = groups.find((item) => item.name === command.group);
          if (!group) {
            group = { name: command.group, items: [] };
            groups.push(group);
          }
          group.items.push({ command, index });
        });
        list.innerHTML = groups.length
          ? groups
              .map(
                (group) =>
                  `<div class="palette-group">${h(group.name)}</div>${group.items
                    .map(({ command, index }) => paletteItem(command, index))
                    .join("")}`,
              )
              .join("")
          : `<div class="palette-empty">No commands match that search.</div>`;
        const buttons = [...list.querySelectorAll("[data-command-index]")];
        buttons.forEach((button) => {
          const index = Number(button.dataset.commandIndex);
          button.setAttribute("aria-selected", String(index === selected));
          button.classList.toggle("is-selected", index === selected);
        });
      };

      const move = (delta) => {
        if (!filtered.length) return;
        selected = (selected + delta + filtered.length) % filtered.length;
        renderList();
        const active = list.querySelector(`[data-command-index="${selected}"]`);
        active?.scrollIntoView({ block: "nearest" });
      };

      const filter = (query) => {
        const needle = query.trim().toLowerCase();
        filtered = needle
          ? commands.filter((command) =>
              `${command.group} ${command.label} ${command.meta || ""}`
                .toLowerCase()
                .includes(needle),
            )
          : commands;
        selected = 0;
        renderList();
      };

      input.addEventListener("input", () => filter(input.value));
      input.addEventListener("keydown", (event) => {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          move(1);
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          move(-1);
        } else if (event.key === "Enter") {
          event.preventDefault();
          execute(selected);
        } else if (event.key === "Escape") {
          event.preventDefault();
          handle.close();
        }
      });
      list.addEventListener("click", (event) => {
        const button = event.target.closest("[data-command-index]");
        if (button) execute(Number(button.dataset.commandIndex));
      });
      list.addEventListener("mousemove", (event) => {
        const button = event.target.closest("[data-command-index]");
        if (!button) return;
        const index = Number(button.dataset.commandIndex);
        if (index !== selected) {
          selected = index;
          renderList();
        }
      });

      renderList();
    },
  });

  return handle;
}
