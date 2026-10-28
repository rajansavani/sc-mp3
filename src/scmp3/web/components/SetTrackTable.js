// The set editor's track table: one row per track, with its include box, its number, its title
// editable in place, its artists and cleanups in popovers, and its status.
import { html } from "htm/preact";
import { useEffect, useRef } from "preact/hooks";

import { api } from "../api.js";
import { reportError } from "../store.js";
import { strings } from "../strings/index.js";
import { ArtistEditor } from "./ArtistEditor.js";
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
// What gets focus when a popover opens: its first control in the tab order.
const FIRST_STOP = 'button:not([tabindex="-1"]), [tabindex="0"], input';

// Where the popover goes: below its button, or above it in the lower half of the window. It's
// fixed to the window, since the table's frame would cut it off, and scrolls when it's taller
// than the room it has.
function popoverPlace(anchor) {
  const rect = anchor.current?.getBoundingClientRect();
  if (!rect) return undefined;
  const left = Math.max(EDGE, Math.min(rect.left, window.innerWidth - POPOVER_WIDTH - EDGE));
  if (rect.top > window.innerHeight / 2) {
    return {
      left: `${left}px`,
      bottom: `${window.innerHeight - rect.top + GAP}px`,
      maxHeight: `${rect.top - GAP - EDGE}px`,
    };
  }
  return {
    left: `${left}px`,
    top: `${rect.bottom + GAP}px`,
    maxHeight: `${window.innerHeight - rect.bottom - GAP - EDGE}px`,
  };
}

// A popover beside a track's row. Esc, a click elsewhere or scrolling outside it closes it. A
// click on the button that opened it is left to that button.
function Popover({ label, anchor, onClose, children }) {
  const ref = useRef(null);
  useEffect(() => {
    ref.current?.querySelector(FIRST_STOP)?.focus();
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
  // Keys stay inside, so ↑ and ↓ can't switch to another queue item and close the popover.
  // Shortcuts with Ctrl or ⌘ still reach the window.
  const onKeyDown = (event) => {
    if (!event.ctrlKey && !event.metaKey) event.stopPropagation();
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
      aria-label=${label}
      onKeyDown=${onKeyDown}
    >
      ${children}
    </div>
  `;
}

// A cell's button that opens a popover, and gets focus back when Esc closes it.
function usePopoverButton(onClose) {
  const button = useRef(null);
  const close = (options) => {
    onClose();
    if (options?.refocus) button.current?.focus();
  };
  return { button, close };
}

function CleanupsCell({ child, title, open, onOpen, onClose }) {
  const { button, close } = usePopoverButton(onClose);
  const changes = child.suggestion?.changes ?? [];
  if (!child.tags) return html`<td class="set-cell"></td>`;
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
      html`
        <${Popover} label=${text.cleanupsLabel(title)} anchor=${button} onClose=${close}>
          <fieldset class="editor-lock" disabled=${!trackEditable(child)}>
            <${Cleanups} job=${child} />
          </fieldset>
        <//>
      `}
    </td>
  `;
}

// The track's joined credit, which opens the same artist editor a single track has.
function ArtistCell({ child, title, drafts, open, onOpen, onClose }) {
  const { button, close } = usePopoverButton(onClose);
  if (!child.tags) return html`<td class="set-cell"></td>`;
  const label = strings.artists.artistsOf(title);
  const credit = child.tags.artist || strings.artists.noArtistCell;
  const locked = !trackEditable(child);
  return html`
    <td class="set-cell">
      <button
        ref=${button}
        type="button"
        class="set-artist"
        title=${credit}
        aria-label=${`${label}: ${credit}`}
        data-empty=${child.tags.artist ? undefined : "true"}
        aria-expanded=${String(open)}
        disabled=${locked}
        onClick=${() => (open ? close() : onOpen())}
      >
        <span class="set-artist-text">${credit}</span>
      </button>
      ${open &&
      html`
        <${Popover} label=${label} anchor=${button} onClose=${close}>
          <fieldset class="editor-lock set-artists" disabled=${locked}>
            <${ArtistEditor} job=${child} drafts=${drafts} idBase=${`artists-${child.id}`} />
          </fieldset>
        <//>
      `}
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
      <${ArtistCell}
        child=${child}
        title=${title}
        drafts=${drafts}
        open=${open === "artists"}
        onOpen=${() => onOpen("artists")}
        onClose=${onClose}
      />
      <${CleanupsCell}
        child=${child}
        title=${title}
        open=${open === "cleanups"}
        onOpen=${() => onOpen("cleanups")}
        onClose=${onClose}
      />
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
        open=${openId?.id === child.id ? openId.part : null}
        onOpen=${(part) => setOpenId({ id: child.id, part })}
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
              <th scope="col" class="set-head is-artist">${columns.artist}</th>
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
