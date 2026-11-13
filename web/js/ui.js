import { escapeHtml, prettyJson } from "./format.js";
import { icon } from "./icons.js";

const openDialogs = new Set();

export function spinner(label = "Working") {
  return `<span class="btn-spinner" role="status" aria-label="${escapeHtml(label)}"><span class="spinner"></span></span>`;
}

export function setBusy(button, busy, label = "Working") {
  if (!button) return;
  if (busy) {
    button.dataset.idleLabel = button.innerHTML;
    button.setAttribute("aria-busy", "true");
    button.disabled = true;
    button.innerHTML = `${spinner(label)}<span>${escapeHtml(label)}</span>`;
  } else {
    button.removeAttribute("aria-busy");
    button.disabled = false;
    if (button.dataset.idleLabel) button.innerHTML = button.dataset.idleLabel;
    delete button.dataset.idleLabel;
  }
}

export function toast(message, { type = "info", detail = "", duration = 4600 } = {}) {
  const root = document.getElementById("toast-root");
  if (!root) return;
  const mark =
    type === "success" ? "check" : type === "error" ? "x" : type === "warn" ? "alert" : "info";
  const node = document.createElement("div");
  node.className = `toast toast-${type}`;
  node.innerHTML = `
    <span class="toast-mark">${icon(mark)}</span>
    <div class="toast-msg">${escapeHtml(message)}${
      detail ? `<small>${escapeHtml(detail)}</small>` : ""
    }</div>
    <button class="toast-close" type="button" aria-label="Dismiss notification">${icon("x")}</button>
  `;
  root.append(node);
  let timer;
  const remove = () => {
    clearTimeout(timer);
    if (!node.isConnected) return;
    node.classList.add("is-leaving");
    setTimeout(() => node.remove(), 200);
  };
  node.querySelector(".toast-close").addEventListener("click", remove);
  timer = setTimeout(remove, duration);
  return remove;
}

export function errorText(error) {
  if (!error) return "Something went wrong.";
  if (error.details && Array.isArray(error.details)) {
    const lines = error.details.map((item) => {
      if (typeof item === "string") return item;
      return [item.path || item.file, item.message || item.error].filter(Boolean).join(": ");
    });
    return `${error.message} — ${lines.slice(0, 4).join("; ")}`;
  }
  if (error.details && typeof error.details === "object") {
    return `${error.message} — ${prettyJson(error.details)}`;
  }
  return error.message || String(error);
}

export function apiStatus(state, label) {
  const node = document.getElementById("api-state");
  if (!node) return;
  node.dataset.state = state;
  const text = node.querySelector(".api-label");
  if (text) text.textContent = label;
}

export function openDialog({ className = "modal", html, onMount, onClose }) {
  const dialog = document.createElement("dialog");
  dialog.className = className;
  dialog.innerHTML = html;
  document.body.append(dialog);
  document.body.classList.add("has-dialog");
  openDialogs.add(dialog);

  let closed = false;
  let closeTimer;
  const finish = (notify = true) => {
    if (closed) return;
    closed = true;
    dialog.classList.add("is-closing");
    clearTimeout(closeTimer);
    closeTimer = setTimeout(() => {
      if (dialog.open) dialog.close();
      dialog.remove();
      openDialogs.delete(dialog);
      if (!openDialogs.size) document.body.classList.remove("has-dialog");
      if (notify && onClose) onClose();
    }, 220);
  };

  const close = ({ notify = true } = {}) => finish(notify);

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) finish(true);
    if (event.target.closest("[data-close]")) finish(true);
  });
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    finish(true);
  });

  dialog.showModal();
  requestAnimationFrame(() => {
    const target =
      dialog.querySelector("[autofocus]") ||
      dialog.querySelector("input:not([type=hidden]), textarea, select, button");
    if (target) target.focus();
  });

  if (onMount) onMount(dialog, close);
  return { dialog, close };
}

export async function copyText(value, label = "Copied to clipboard") {
  const text = String(value ?? "");
  try {
    await navigator.clipboard.writeText(text);
    toast(label, { type: "success", duration: 2400 });
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    toast(label, { type: "success", duration: 2400 });
  }
}

export function emptyState({ title, body, actions = "" }) {
  return `
    <div class="empty-state">
      <span class="boot-mark" aria-hidden="true"></span>
      <h3>${escapeHtml(title)}</h3>
      <p>${escapeHtml(body)}</p>
      ${actions ? `<div class="empty-actions">${actions}</div>` : ""}
    </div>
  `;
}
