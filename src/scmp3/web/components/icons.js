// The main window's inline SVG icons: 24-unit shapes with a 1.8 stroke and round caps and joins.
import { html } from "htm/preact";

// Each shape is a function, so every icon gets its own nodes.
const SHAPES = {
  person: () => html`<circle cx="12" cy="8" r="4" /><path d="M4 20c1.5-4 4.5-6 8-6s6.5 2 8 6" />`,
  sliders: () => html`
    <path d="M4 7h10M18 7h2M4 17h4M12 17h8" />
    <circle cx="16" cy="7" r="2" />
    <circle cx="10" cy="17" r="2" />
  `,
  image: () => html`
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <circle cx="9" cy="10" r="2" />
    <path d="M21 17l-5-5-9 8" />
  `,
  crop: () => html`<path d="M6 2v14a2 2 0 0 0 2 2h14" /><path d="M2 6h14a2 2 0 0 1 2 2v14" />`,
  pencil: () => html`<path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />`,
  check: () => html`<path d="M5 12.5l4.2 4.2L19 7" />`,
  undo: () => html`<path d="M4 10h11a5 5 0 0 1 0 10h-3" /><path d="M8 6l-4 4 4 4" />`,
  folder: () => html`
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  `,
  bars: () => html`<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2" />`,
  clock: () => html`<circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" />`,
  warning: () => html`
    <path d="M12 9v4M12 17h.01" />
    <path
      d="M10.3 3.9L2.5 17.5A2 2 0 0 0 4.2 20.5h15.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"
    />
  `,
  error: () => html`<circle cx="12" cy="12" r="9" /><path d="M12 7v6M12 16.5h.01" />`,
  offline: () => html`
    <path d="M2 8.5a15 15 0 0 1 20 0M5 12a10 10 0 0 1 14 0M8.5 15.5a5 5 0 0 1 7 0M12 19h.01" />
    <path d="M3 3l18 18" />
  `,
  download: () => html`
    <path d="M12 3v12M7.5 10.5L12 15l4.5-4.5" />
    <path d="M4 15v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3" />
  `,
  close: () => html`<path d="M6 6l12 12M18 6L6 18" />`,
  music: () => html`
    <path d="M9 18V5l12-2v13" />
    <circle cx="6" cy="18" r="3" />
    <circle cx="18" cy="16" r="3" />
  `,
};

export function Icon({ name, size = 16, weight = 1.8, className = undefined }) {
  return html`
    <svg
      class=${className}
      width=${size}
      height=${size}
      viewBox="0 0 24 24"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      stroke-width=${weight}
      stroke-linecap="round"
      stroke-linejoin="round"
    >
      ${SHAPES[name]()}
    </svg>
  `;
}
