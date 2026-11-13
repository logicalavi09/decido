import { api } from "./api.js";
import { demoRuns } from "./data.js";
import { hydrateIcons } from "./icons.js";
import { errorText, setBusy, toast } from "./ui.js";
import {
  openCompose,
  openPalette,
  openRunDrawer,
  openWorkflowDrawer,
  renderGovernance,
  renderOverview,
  renderRuns,
  renderWorkflows,
} from "./views.js";

const state = {
  health: null,
  workflows: null,
  runs: null,
  workflowCache: new Map(),
  route: { view: "overview", id: null },
  loading: true,
};

const view = document.getElementById("view");
let activeDrawer = null;
let activeDrawerKind = null;
let activeDrawerId = null;
let routeSequence = 0;
let seedInFlight = null;

const VIEW_META = {
  overview: { label: "Overview", title: "Overview", crumb: "Overview" },
  runs: { label: "Decisions", title: "Decision ledger", crumb: "Decisions" },
  workflows: { label: "Workflows", title: "Workflow library", crumb: "Workflows" },
  governance: { label: "Change lab", title: "Change lab", crumb: "Change lab" },
};

function parseRoute() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const [name, ...rest] = raw.split("/");
  const id = rest.length ? decodeURIComponent(rest.join("/")) : null;
  if (name === "runs") return { view: "runs", id };
  if (name === "workflows") return { view: "workflows", id };
  if (name === "governance") return { view: "governance", id: null };
  return { view: "overview", id: null };
}

function applyChrome(route) {
  const meta = VIEW_META[route.view] || VIEW_META.overview;
  document.querySelectorAll("[data-route]").forEach((link) => {
    if (link.dataset.route === route.view) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  const crumb = document.getElementById("crumb-view");
  if (crumb) crumb.textContent = route.id ? `${meta.crumb} · ${route.id.slice(0, 8)}` : meta.crumb;
  document.title = `${route.id ? `${meta.title} · ${route.id.slice(0, 8)}` : meta.title} · Decido`;
}

async function refresh({
  all = false,
  workflows = false,
  runs = false,
  health = false,
  render = false,
} = {}) {
  const wantWorkflows = all || workflows;
  const wantRuns = all || runs;
  const wantHealth = all || health;
  const tasks = [];
  if (wantWorkflows) tasks.push(api.workflows().then((value) => ({ key: "workflows", value })));
  if (wantRuns) tasks.push(api.runs({ limit: 200 }).then((value) => ({ key: "runs", value })));
  if (wantHealth) tasks.push(api.health().then((value) => ({ key: "health", value })));
  if (!tasks.length) {
    if (render) await renderRoute();
    return;
  }

  try {
    const results = await Promise.all(tasks);
    for (const result of results) {
      state[result.key] = result.value;
      if (result.key === "workflows") state.workflowCache.clear();
    }
    document.getElementById("api-state").dataset.state = "live";
    const label = document.querySelector("#api-state .api-label");
    if (label) label.textContent = "Live";
    if (state.health) {
      document.getElementById("api-state").title = `${state.health.workflows} workflows loaded · ${state.health.status}`;
    }
    if (render && !state.loading) await renderRoute();
  } catch (error) {
    const apiNode = document.getElementById("api-state");
    if (apiNode) {
      apiNode.dataset.state = "offline";
      const label = apiNode.querySelector(".api-label");
      if (label) label.textContent = "Offline";
    }
    throw error;
  }
}

function closeActiveDrawer() {
  if (!activeDrawer) return;
  const drawer = activeDrawer;
  activeDrawer = null;
  activeDrawerKind = null;
  activeDrawerId = null;
  drawer.close({ notify: false });
}

function handleDrawerClose(base) {
  activeDrawer = null;
  activeDrawerKind = null;
  activeDrawerId = null;
  const route = { view: base, id: null };
  state.route = route;
  if (window.location.hash.startsWith(`#/${base}/`)) {
    window.history.replaceState(null, "", `#/${base}`);
  }
  applyChrome(route);
}

async function renderRoute() {
  if (state.loading) return;
  const sequence = ++routeSequence;
  const route = parseRoute();
  const sameDrawer =
    activeDrawer &&
    activeDrawerKind === route.view &&
    activeDrawerId === route.id;
  if (activeDrawer && !sameDrawer) closeActiveDrawer();

  state.route = route;
  applyChrome(route);

  if (!view) return;
  view.classList.remove("is-entering");
  void view.offsetWidth;

  let result;
  if (route.view === "runs") result = renderRuns(state);
  else if (route.view === "workflows") result = renderWorkflows(state);
  else if (route.view === "governance") result = renderGovernance(state);
  else result = renderOverview(state);

  if (sequence !== routeSequence) return;
  view.innerHTML = result.html;
  hydrateIcons(view);
  result.mount?.(view, ctx);
  view.classList.add("is-entering");

  if (route.id && !sameDrawer) {
    if (route.view === "runs") {
      activeDrawerKind = "runs";
      activeDrawerId = route.id;
      activeDrawer = openRunDrawer(route.id, state, ctx, {
        onClose: () => handleDrawerClose("runs"),
      });
    } else if (route.view === "workflows") {
      activeDrawerKind = "workflows";
      activeDrawerId = route.id;
      activeDrawer = openWorkflowDrawer(route.id, state, ctx, {
        onClose: () => handleDrawerClose("workflows"),
      });
    }
  }
}

function navigate(hash) {
  if (window.location.hash === hash) renderRoute();
  else window.location.hash = hash;
}

const ctx = {
  refresh,
  render: renderRoute,
  navigate,
  openRun(id) {
    navigate(`#/runs/${encodeURIComponent(id)}`);
  },
  openWorkflow(name) {
    navigate(`#/workflows/${encodeURIComponent(name)}`);
  },
  openCompose(workflow) {
    openCompose(state, ctx, { workflow });
  },
  openPalette() {
    openPalette(state, ctx);
  },
  handleDrawerClose,
  async seedDemo(button) {
    if (seedInFlight) return seedInFlight;
    seedInFlight = (async () => {
      setBusy(button, true, "Seeding");
      const payloads = demoRuns(Date.now());
      let created = 0;
      let failed = 0;
      for (const payload of payloads) {
        try {
          await api.createRun(payload);
          created += 1;
        } catch (error) {
          failed += 1;
          console.warn("demo seed failed", error);
        }
      }
      try {
        await refresh({ runs: true });
      } catch (error) {
        toast("Demo ledger loaded, but the view could not refresh", {
          type: "warn",
          detail: errorText(error),
        });
      }
      if (created) {
        toast("Demo ledger loaded", {
          type: "success",
          detail: `${created} decisions recorded${failed ? ` · ${failed} could not be recorded` : ""}`,
        });
      } else {
        toast("No demo decisions were recorded", {
          type: "error",
          detail: failed ? "Check that the API and workflow configuration are healthy." : "",
        });
      }
      await renderRoute();
      setBusy(button, false);
    })().finally(() => {
      seedInFlight = null;
    });
    return seedInFlight;
  },
};

function renderFatal(error) {
  if (!view) return;
  view.innerHTML = `
    <div class="empty-state">
      <span class="boot-mark" aria-hidden="true"></span>
      <h3>Decido could not reach its API.</h3>
      <p>${errorText(error)}</p>
      <div class="empty-actions">
        <button class="btn btn-primary" type="button" id="fatal-retry">Retry connection</button>
      </div>
    </div>
  `;
  document.getElementById("fatal-retry")?.addEventListener("click", () => window.location.reload());
}

function bindShell() {
  document.getElementById("new-run-btn")?.addEventListener("click", () => ctx.openCompose());
  document.getElementById("command-trigger")?.addEventListener("click", () => ctx.openPalette());
  document.getElementById("command-trigger-rail")?.addEventListener("click", () => ctx.openPalette());
  window.addEventListener("hashchange", renderRoute);
  window.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      ctx.openPalette();
    }
  });
}

async function boot() {
  hydrateIcons();
  bindShell();
  try {
    await refresh({ all: true });
    state.loading = false;
    await renderRoute();
  } catch (error) {
    state.loading = false;
    renderFatal(error);
  }
}

boot();
