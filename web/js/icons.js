const paths = {
  pulse:
    '<path d="M3 12h4l2.2-6 4.1 12 2.4-6H21" />',
  list:
    '<path d="M8 6h13M8 12h13M8 18h13" /><circle cx="3.5" cy="6" r=".9" fill="currentColor" stroke="none" /><circle cx="3.5" cy="12" r=".9" fill="currentColor" stroke="none" /><circle cx="3.5" cy="18" r=".9" fill="currentColor" stroke="none" />',
  layers:
    '<path d="m12 3 9 4.5-9 4.5-9-4.5L12 3Z" /><path d="m3 12 9 4.5 9-4.5" /><path d="m3 16.5 9 4.5 9-4.5" />',
  shield:
    '<path d="M12 3 5 6v5c0 4.4 2.8 7.6 7 10 4.2-2.4 7-5.6 7-10V6l-7-3Z" /><path d="m9 12 2 2 4-4" />',
  command:
    '<path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6Z" />',
  search:
    '<circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />',
  plus:
    '<path d="M12 5v14M5 12h14" />',
  check:
    '<path d="m5 12 4 4L19 6" />',
  x:
    '<path d="M6 6l12 12M18 6 6 18" />',
  alert:
    '<path d="M12 3 2.8 19.2A1.3 1.3 0 0 0 4 21h16a1.3 1.3 0 0 0 1.2-1.8L12 3Z" /><path d="M12 9v4.5" /><circle cx="12" cy="17" r=".9" fill="currentColor" stroke="none" />',
  info:
    '<circle cx="12" cy="12" r="9" /><path d="M12 11v5" /><circle cx="12" cy="8" r=".9" fill="currentColor" stroke="none" />',
  clock:
    '<circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" />',
  arrowRight:
    '<path d="M4 12h15" /><path d="m14 7 5 5-5 5" />',
  arrowUpRight:
    '<path d="M7 17 17 7" /><path d="M8 7h9v9" />',
  refresh:
    '<path d="M20 7v5h-5" /><path d="M4 17v-5h5" /><path d="M6.1 9a7 7 0 0 1 11.6-2L20 12M4 12l2.3 5a7 7 0 0 0 11.6-2" />',
  play:
    '<path d="M8 5.5v13l10-6.5-10-6.5Z" />',
  copy:
    '<rect x="8" y="8" width="11" height="11" rx="1.5" /><path d="M16 8V5.5A1.5 1.5 0 0 0 14.5 4h-9A1.5 1.5 0 0 0 4 5.5v9A1.5 1.5 0 0 0 5.5 16H8" />',
  chevronDown:
    '<path d="m6 9 6 6 6-6" />',
  database:
    '<ellipse cx="12" cy="5.5" rx="8" ry="3" /><path d="M4 5.5v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" /><path d="M4 11.5v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6" />',
  file:
    '<path d="M6 3h7l5 5v13H6V3Z" /><path d="M13 3v5h5M9 13h6M9 17h4" />',
  lock:
    '<rect x="5" y="10" width="14" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" />',
  spark:
    '<path d="m12 3 1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z" /><path d="m19 16 .8 2.2L22 19l-2.2.8L19 22l-.8-2.2L16 19l2.2-.8L19 16Z" />',
  external:
    '<path d="M14 4h6v6" /><path d="M20 4 11 13" /><path d="M19 14v5.5A1.5 1.5 0 0 1 17.5 21h-12A1.5 1.5 0 0 1 4 19.5v-12A1.5 1.5 0 0 1 5.5 6H11" />',
  scan:
    '<path d="M4 8V5a1 1 0 0 1 1-1h3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3" /><path d="M7 12h10" />',
  branch:
    '<circle cx="6" cy="5" r="2" /><circle cx="6" cy="19" r="2" /><circle cx="18" cy="8" r="2" /><path d="M6 7v10M8 10c3 0 3-2 8-2" />',
  filter:
    '<path d="M3 5h18l-7 8v6l-4 2v-8L3 5Z" />',
  eye:
    '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Z" /><circle cx="12" cy="12" r="3" />',
  link:
    '<path d="M10 13.5a4 4 0 0 0 5.7 0l2.8-2.8a4 4 0 0 0-5.7-5.7L11.5 6" /><path d="M14 10.5a4 4 0 0 0-5.7 0l-2.8 2.8a4 4 0 0 0 5.7 5.7l1.3-1" />',
  rotate:
    '<path d="M20 11a8 8 0 1 0-2.3 6.7" /><path d="M20 4v7h-7" />',
  sliders:
    '<path d="M4 7h10M18 7h2M4 17h2M10 17h10" /><circle cx="16" cy="7" r="2" /><circle cx="8" cy="17" r="2" />',
};

export function icon(name, className = "") {
  const body = paths[name] || paths.info;
  return `<svg${className ? ` class="${className}"` : ""} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

export function hydrateIcons(root = document) {
  root.querySelectorAll("[data-icon]").forEach((node) => {
    node.innerHTML = icon(node.dataset.icon);
  });
}
