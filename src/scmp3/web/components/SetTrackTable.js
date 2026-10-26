// The set editor's track table: one row per track, with its include box, its number, its title
// and artist editable in place, its cleanups in a popover, and its status.
import { html } from "htm/preact";
import { useEffect, useRef } from "preact/hooks";

import { api } from "../api.js";
import { reportError } from "../store.js";
import { strings } from "../strings/index.js";
import { renderedValue } from "./EditorFields.js";
import { Cleanups } from "./EditorSections.js";
import { useFieldDrafts } from "./field-drafts.js";
import { canNeverSave } from "./job-view.js";
import {
  inclusionEditable,
  trackEditable,
  trackNumber,
  trackStatus,
  trackTitle,
} from "./set-tracks.js";

const text = strings.sets;

// The popover's width and its gap from the window's edge and from its button, in pixels.
const POPOVER_WIDTH = 440;
const EDGE = 16;
const GAP = 4;

// Where the popover goes: below its button, or above it in the lower half of the window. It's
// fixed to the window, since the table's frame would cut it off.
function popoverPlace(anchor) {
  const rect = anchor.current?.getBoundingClientRect();
  if (!rect) return undefined;
  const left = Math.max(EDGE, Math.min(rect.left, window.innerWidth - POPOVER_WIDTH - EDGE));
  if (rect.top > window.innerHeight / 2) {
    return { left: `${left}px`, bottom: `${window.innerHeight - rect.top + GAP}px` };
  }
  return { left: `${left}px`, top: `${rect.bottom + GAP}px` };
}

// The cleanups of one track, beside its row. Esc, a click elsewhere or scrolling closes it. A
// click on the button that opened it is left to that button.
function CleanupsPopover({ child, title, anchor, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    ref.current?.querySelector("button")?.focus();
    const onPointer = (event) => {
      const inside = ref.current?.contains(event.target) || anchor.current?.contains(event.target);
      if (!inside) onClose();
    };
    const onScroll = (event) => {
      if (!ref.current?.contains(event.target)) onClose();
    };
    document.addEventListener("pointerdown", onPointer);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, []);
  const onKeyDown = (event) => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    onClose({ refocus: true });
  };
  return html`
    <div
      ref=${ref}
      class="set-popover"
      style=${popoverPlace(anchor)}
      role="dialog"
      aria-label=${text.cleanupsLabel(title)}
      onKeyDown=${onKeyDown}
    >
      <fieldset class="editor-lock" disabled=${!trackEditable(child)}>
        <${Cleanups} job=${child} />
      </fieldset>
    </div>
  `;
}

function CleanupsCell({ child, title, open, onOpen, onClose }) {
  const button = useRef(null);
  const changes = child.suggestion?.changes ?? [];
  if (!child.tags) return html`<td class="set-cell"></td>`;
  const close = (options) => {
    onClose();
    if (options?.refocus) button.current?.focus();
  };
  return html`
    <td class="set-cell set-cleanups">
      <button
        ref=${button}
        type="button"
        class=${changes.length === 0 ? "text-button is-quiet" : "text-button"}
        aria-expanded=${String(open)}
        onClick=${() => (open ? close() : onOpen())}
      >
        ${text.cleanups(changes.length)}
      </button>
      ${open &&
      html`<${CleanupsPopover} child=${child} title=${title} anchor=${button} onClose=${close} />`}
    </td>
  `;
}

function TrackInput({ drafts, field, label, disabled }) {
  return html`
    <input
      class="text-field set-input"
      type="text"
      aria-label=${label}
      value=${drafts.value(field)}
      disabled=${disabled}
      onInput=${(event) => drafts.type(field, event.currentTarget.value)}
      onFocus=${() => drafts.focus(field)}
      onBlur=${() => drafts.blur(field)}
    />
  `;
}

function StatusCell({ job, child }) {
  const status = trackStatus(job, child);
  return html`
    <td class="set-cell set-status">
      <span class="set-status-text" data-tone=${status.tone}>${status.text}</span>
      ${status.pct != null &&
      html`
        <span class="progress-track set-progress" aria-hidden="true">
          <span class="progress-fill" style=${{ "--progress": `${status.pct}%` }}></span>
        </span>
      `}
    </td>
  `;
}

function TrackRow({ job, child, index, open, onOpen, onClose, register }) {
  const drafts = useFieldDrafts(child, renderedValue);
  register(child.id, drafts);
  useEffect(() => () => register(child.id, null), []);
  const title = trackTitle(job, child, index);
  const included = child.included && !canNeverSave(child);
  const toggle = async (event) => {
    reportError(await api.set_included(job.id, child.id, event.currentTarget.checked));
  };
  const locked = !trackEditable(child);
  return html`
    <tr class="set-row" data-included=${String(included)}>
      <td class="set-cell">
        <input
          type="checkbox"
          class="set-check"
          checked=${included}
          disabled=${!inclusionEditable(job, child)}
          aria-label=${text.includeLabel(title)}
          onChange=${toggle}
        />
      </td>
      <td class="set-cell set-number">${trackNumber(job, child)}</td>
      <td class="set-cell">
        ${child.tags
          ? html`<${TrackInput}
              drafts=${drafts}
              field="title"
              label=${text.titleLabel(title)}
              disabled=${locked}
            />`
          : html`<span class="set-pending">${title}</span>`}
      </td>
      <td class="set-cell">
        ${child.tags &&
        html`<${TrackInput}
          drafts=${drafts}
          field="artist"
          label=${text.artistLabel(title)}
          disabled=${locked}
        />`}
      </td>
      <${CleanupsCell} child=${child} title=${title} open=${open} onOpen=${onOpen} onClose=${onClose} />
      <${StatusCell} job=${job} child=${child} />
    </tr>
  `;
}

export function SetTrackTable({ job, openId, setOpenId, register }) {
  const columns = text.columns;
  const rows = job.children.map(
    (child, index) => html`
      <${TrackRow}
        key=${child.id}
        job=${job}
        child=${child}
        index=${index}
        open=${openId === child.id}
        onOpen=${() => setOpenId(child.id)}
        onClose=${() => setOpenId(null)}
        register=${register}
      />
    `,
  );
  return html`
    <section class="set-tracks" aria-labelledby="set-tracks-title">
      <div class="set-tracks-head">
        <h2 id="set-tracks-title" class="set-tracks-title">${text.tracksTitle}</h2>
        <span class="set-tracks-hint">${text.tracksHint}</span>
      </div>
      <div class="set-table-frame">
        <table class="set-table">
          <thead>
            <tr>
              <th scope="col" class="set-head is-save">${columns.save}</th>
              <th scope="col" class="set-head is-number">${columns.number}</th>
              <th scope="col" class="set-head">${columns.title}</th>
              <th scope="col" class="set-head">${columns.artist}</th>
              <th scope="col" class="set-head is-cleanups">${columns.cleanups}</th>
              <th scope="col" class="set-head is-status">${columns.status}</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </section>
  `;
}
