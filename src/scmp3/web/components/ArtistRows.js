// The artist editor's two rows of name tags, main and featured. A tag moves by drag and drop, or
// from the keyboard and its menu, which does everything a drag does.
import { html } from "htm/preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { strings } from "../strings/index.js";
import { added, moved, otherRow, removed, ROWS } from "./artist-list.js";
import { AddName, isLocked, Tag } from "./ArtistTag.js";

const text = strings.artists;

// What a dragged tag carries. Only these rows take it, so a drag can't add a name to the queue.
const DRAG_TYPE = "application/x-scmp3-artist";
const FOCUS_STEPS = { ArrowLeft: -1, ArrowRight: 1 };

function byName(root, name) {
  const slots = root?.querySelectorAll(".artist-slot") ?? [];
  return [...slots].find((slot) => slot.dataset.name === name) ?? null;
}

// The name a dragged tag would land before, reading the row's lines in order, or null for the end.
function dropBefore(row, event, dragged) {
  const tags = [...row.querySelectorAll(".artist-slot")].filter((slot) => {
    return slot.dataset.name !== dragged;
  });
  for (const slot of tags) {
    const box = slot.querySelector(".artist-tag").getBoundingClientRect();
    if (event.clientY < box.top) return slot.dataset.name;
    if (event.clientY <= box.bottom && event.clientX < box.left + box.width / 2) {
      return slot.dataset.name;
    }
  }
  return null;
}

// The tag on the nearest line above (step -1) or below (step 1), in either row, and on that
// line the one nearest across.
function tagAcross(root, name, step) {
  const from = byName(root, name)?.querySelector(".artist-tag").getBoundingClientRect();
  if (!from) return null;
  const middle = (box) => box.left + box.width / 2;
  const lines = [...root.querySelectorAll(".artist-tag")]
    .map((tag) => ({ tag, box: tag.getBoundingClientRect() }))
    .map(({ tag, box }) => ({ tag, down: (box.top - from.top) * step, across: middle(box) }))
    .filter(({ down }) => down > from.height / 2);
  if (lines.length === 0) return null;
  const nearest = Math.min(...lines.map(({ down }) => down));
  const line = lines.filter(({ down }) => down - nearest < from.height / 2);
  const distance = ({ across }) => Math.abs(across - middle(from));
  return line.reduce((best, item) => (distance(item) < distance(best) ? item : best)).tag;
}

// Where focus goes after an edit, once the tags have moved: a name, a menu item, or a row's Add.
function useFocusAfterEdit(root) {
  const target = useRef(null);
  useEffect(() => {
    const wanted = target.current;
    if (!wanted) return;
    target.current = null;
    if (wanted.add) {
      root.current?.querySelector(`[data-add="${wanted.add}"]`)?.focus();
      return;
    }
    const slot = byName(root.current, wanted.name);
    const item = wanted.item && slot?.querySelector(`[data-action="${wanted.item}"]:not(:disabled)`);
    const fallback = wanted.item ? slot?.querySelector("[role=menuitem]:not(:disabled)") : null;
    (item || fallback || slot?.querySelector(".artist-name"))?.focus();
  });
  return (wanted) => {
    target.current = wanted;
  };
}

function Row({ row, names, labelId, hintId, active, adding, dropAt, actions }) {
  const focusIndex = Math.max(0, names.indexOf(active));
  const target = dropAt?.row === row;
  const dropAtEnd = target && dropAt.before === null;
  return html`
    <div
      class="artist-row"
      role="group"
      aria-labelledby=${labelId}
      data-target=${target ? "true" : undefined}
      onDragOver=${(event) => actions.dragOver(event, row)}
      onDragLeave=${(event) => actions.dragLeave(event, row)}
      onDrop=${(event) => actions.drop(event, row)}
    >
      ${names.length === 0 && adding !== row &&
      html`<span class="artist-empty">${text.emptyRow[row]}</span>`}
      ${names.map(
        (name, index) => html`
          <${Tag}
            key=${name}
            name=${name}
            row=${row}
            index=${index}
            count=${names.length}
            focusable=${index === focusIndex}
            hintId=${hintId}
            drop=${target && dropAt.before === name}
            actions=${actions}
          />
        `,
      )}
      ${adding === row
        ? html`
            <${AddName}
              row=${row}
              drop=${dropAtEnd}
              onAdd=${(typed) => actions.add(row, typed)}
              onClose=${(refocus) => actions.stopAdding(row, refocus)}
            />
          `
        : html`
            <button
              type="button"
              class="text-button artist-add"
              data-add=${row}
              data-drop=${dropAtEnd ? "before" : undefined}
              aria-label=${text.addLabel[row]}
              onClick=${() => actions.startAdding(row)}
            >
              ${text.add}
            </button>
          `}
    </div>
  `;
}

// What every tag and row can do to the list, in one place.
function useRowActions(list, onChange, root) {
  const [menuFor, setMenuFor] = useState(null);
  const [adding, setAdding] = useState(null);
  const [active, setActive] = useState({});
  const [dropAt, setDropAt] = useState(null);
  const [dragging, setDragging] = useState(null);
  // The dragged name, read before the drag's own render has happened.
  const dragged = useRef(null);
  const focusAfter = useFocusAfterEdit(root);

  function pick(name, action) {
    const row = list.main.includes(name) ? "main" : "featured";
    const names = list[row];
    const index = names.indexOf(name);
    if (action === "remove") {
      setMenuFor(null);
      const neighbor = names[index + 1] ?? names[index - 1];
      focusAfter(neighbor ? { name: neighbor } : { add: row });
      onChange(removed(list, name));
    } else if (action === "switch") {
      setMenuFor(null);
      const to = otherRow(row);
      focusAfter({ name });
      onChange(moved(list, name, to, list[to].length));
    } else {
      focusAfter({ name, item: action });
      onChange(moved(list, name, row, index + (action === "left" ? -1 : 1)));
    }
  }

  function focusAlong(row, index, key) {
    const names = list[row];
    const to = { Home: 0, End: names.length - 1 }[key] ?? index + FOCUS_STEPS[key];
    const name = names[Math.max(0, Math.min(to, names.length - 1))];
    byName(root.current, name)?.querySelector(".artist-name")?.focus();
  }

  return {
    menuFor,
    dragging,
    pick,
    focusAlong,
    focusAcross(name, step) {
      tagAcross(root.current, name, step)?.querySelector(".artist-name")?.focus();
    },
    toggleMenu: (name) => setMenuFor(menuFor === name ? null : name),
    closeMenu(name, refocus) {
      setMenuFor((open) => (open === name ? null : open));
      if (refocus) byName(root.current, name)?.querySelector(".artist-name")?.focus();
    },
    setActive: (row, name) => setActive((current) => ({ ...current, [row]: name })),
    startAdding: (row) => setAdding(row),
    stopAdding(row, refocus) {
      setAdding((open) => (open === row ? null : open));
      if (refocus) focusAfter({ add: row });
    },
    add: (row, typed) => {
      const next = added(list, row, typed);
      if (next !== list) onChange(next);
    },
    dragStart(event, name) {
      if (isLocked(event.currentTarget)) {
        event.preventDefault();
        return;
      }
      event.dataTransfer.setData(DRAG_TYPE, name);
      event.dataTransfer.effectAllowed = "move";
      dragged.current = name;
      setMenuFor(null);
      // The tag fades after the browser has taken its picture for the drag.
      requestAnimationFrame(() => setDragging(name));
    },
    dragEnd() {
      dragged.current = null;
      setDragging(null);
      setDropAt(null);
    },
    dragOver(event, row) {
      if (!dragged.current || !event.dataTransfer.types.includes(DRAG_TYPE)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      const before = dropBefore(event.currentTarget, event, dragged.current);
      if (dropAt?.row !== row || dropAt.before !== before) setDropAt({ row, before });
    },
    dragLeave(event, row) {
      if (event.currentTarget.contains(event.relatedTarget)) return;
      setDropAt((at) => (at?.row === row ? null : at));
    },
    drop(event, row) {
      const name = dragged.current;
      if (!name || !event.dataTransfer.types.includes(DRAG_TYPE)) return;
      event.preventDefault();
      const before = dropBefore(event.currentTarget, event, name);
      const others = list[row].filter((credited) => credited !== name);
      const index = before === null ? others.length : others.indexOf(before);
      dragged.current = null;
      setDragging(null);
      setDropAt(null);
      onChange(moved(list, name, row, index));
    },
    adding,
    active,
    dropAt,
  };
}

// The rows, each with its label. `reset` goes beside the first row's label.
export function ArtistRows({ list, onChange, idBase, reset }) {
  const root = useRef(null);
  const actions = useRowActions(list, onChange, root);
  const hintId = `${idBase}-tag-hint`;
  return html`
    <div class="artist-rows" ref=${root}>
      <span id=${hintId} class="visually-hidden">${text.tagHint}</span>
      ${ROWS.map((row) => {
        const labelId = `${idBase}-${row}-label`;
        return html`
          <div key=${row} class="artist-row-field">
            <div class="field-label-row">
              <span id=${labelId}>${text.rows[row]}</span>
              ${row === "main" && reset}
            </div>
            <${Row}
              row=${row}
              names=${list[row]}
              labelId=${labelId}
              hintId=${hintId}
              active=${actions.active[row]}
              adding=${actions.adding}
              dropAt=${actions.dropAt}
              actions=${actions}
            />
          </div>
        `;
      })}
    </div>
  `;
}
