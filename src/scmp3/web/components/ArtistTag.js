// One name tag in the artist editor's rows, with its remove button and its menu, and the small
// input "+ Add" opens.
import { html } from "htm/preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { api } from "../api.js";
import { reportError } from "../store.js";
import { strings } from "../strings/index.js";
import { Icon } from "./icons.js";

const text = strings.artists;

const toggleMenu = (actions, tag) => actions.toggleMenu(tag.name);
const remove = (actions, tag) => actions.pick(tag.name, "remove");
const along = (actions, tag, key) => actions.focusAlong(tag.row, tag.index, key);

// What each key does on a tag. ↑ and ↓ go to the line above or below, even into the other row,
// so they never reach the queue, which would switch tracks.
const TAG_KEYS = {
  Enter: toggleMenu,
  " ": toggleMenu,
  Delete: remove,
  Backspace: remove,
  ArrowLeft: along,
  ArrowRight: along,
  Home: along,
  End: along,
  ArrowUp: (actions, tag) => actions.focusAcross(tag.name, -1),
  ArrowDown: (actions, tag) => actions.focusAcross(tag.name, 1),
};

// Tags in a locked form neither move nor open their menu, as its buttons don't.
export function isLocked(element) {
  return element.closest("fieldset:disabled") !== null;
}

function TagMenu({ name, row, index, count, onPick, onClose }) {
  const ref = useRef(null);
  useEffect(() => {
    ref.current?.querySelector("button:not(:disabled)")?.focus();
    ref.current?.scrollIntoView({ block: "nearest" });
    const slot = ref.current?.parentElement;
    const onPointer = (event) => {
      if (!slot?.contains(event.target)) onClose(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, []);
  const items = [
    { action: "switch", label: row === "main" ? text.menu.toFeatured : text.menu.toMain },
    { action: "left", label: text.menu.left, disabled: index === 0 },
    { action: "right", label: text.menu.right, disabled: index === count - 1 },
    { action: "remove", label: text.menu.remove },
  ];
  const onKeyDown = (event) => {
    const step = { ArrowDown: 1, ArrowUp: -1 }[event.key];
    if (event.key !== "Escape" && !step) return;
    // Kept from the queue's arrow keys, and from the Escape of a popup around the editor.
    event.preventDefault();
    event.stopPropagation();
    if (event.key === "Escape") {
      onClose(true);
      return;
    }
    const enabled = [...ref.current.querySelectorAll("button:not(:disabled)")];
    const next = enabled.indexOf(document.activeElement) + step;
    enabled[(next + enabled.length) % enabled.length]?.focus();
  };
  // Tab leaves the menu. A click elsewhere closes it on pointerdown, and focus lost to a tag
  // moving keeps it open, since the moved tag's item gets focus back.
  const onFocusOut = (event) => {
    const to = event.relatedTarget;
    if (to && !ref.current?.parentElement?.contains(to)) onClose(false);
  };
  return html`
    <div
      ref=${ref}
      class="artist-menu"
      role="menu"
      aria-label=${name}
      onKeyDown=${onKeyDown}
      onFocusOut=${onFocusOut}
    >
      ${items.map(
        (item) => html`
          <button
            key=${item.action}
            type="button"
            role="menuitem"
            tabindex="-1"
            class="artist-menu-item"
            data-action=${item.action}
            disabled=${item.disabled}
            onClick=${() => onPick(item.action)}
          >
            ${item.label}
          </button>
        `,
      )}
    </div>
  `;
}

export function Tag({ name, row, index, count, focusable, hintId, drop, actions }) {
  const menuOpen = actions.menuFor === name;
  const onKeyDown = (event) => {
    const handle = TAG_KEYS[event.key];
    if (!handle || event.altKey || event.ctrlKey || event.metaKey) return;
    if (isLocked(event.currentTarget)) return;
    event.preventDefault();
    event.stopPropagation();
    handle(actions, { name, row, index }, event.key);
  };
  const onClick = (event) => {
    if (!isLocked(event.currentTarget)) actions.toggleMenu(name);
  };
  return html`
    <div
      class="artist-slot"
      data-name=${name}
      data-drop=${drop ? "before" : undefined}
      data-dragging=${actions.dragging === name ? "true" : undefined}
    >
      <span
        class="artist-tag"
        draggable="true"
        onDragStart=${(event) => actions.dragStart(event, name)}
        onDragEnd=${actions.dragEnd}
      >
        <span
          class="artist-name"
          role="button"
          tabindex=${focusable ? "0" : "-1"}
          aria-haspopup="menu"
          aria-expanded=${String(menuOpen)}
          aria-describedby=${hintId}
          onKeyDown=${onKeyDown}
          onClick=${onClick}
          onFocus=${() => actions.setActive(row, name)}
        >
          ${name}
        </span>
        <button
          type="button"
          class="artist-remove"
          tabindex="-1"
          aria-label=${text.remove(name)}
          onClick=${() => actions.pick(name, "remove")}
        >
          <${Icon} name="close" size=${14} />
        </button>
      </span>
      ${menuOpen &&
      html`
        <${TagMenu}
          name=${name}
          row=${row}
          index=${index}
          count=${count}
          onPick=${(action) => actions.pick(name, action)}
          onClose=${(refocus) => actions.closeMenu(name, refocus)}
        />
      `}
    </div>
  `;
}

// The small input "+ Add" opens. Enter or a comma adds what's typed, and it stays open for more.
export function AddName({ row, drop, onAdd, onClose }) {
  const [value, setValue] = useState("");
  const ref = useRef(null);
  const closing = useRef(false);
  useEffect(() => ref.current?.focus(), []);
  const close = (refocus) => {
    closing.current = true;
    onClose(refocus);
  };
  const onInput = (event) => {
    const parts = event.currentTarget.value.split(",");
    const rest = parts.pop();
    if (parts.length > 0) onAdd(parts);
    setValue(rest);
  };
  const onKeyDown = (event) => {
    if (event.isComposing) return;
    if (event.key === "Enter") {
      // Enter adds the name here, rather than saving the track.
      event.preventDefault();
      event.stopPropagation();
      if (!value.trim()) {
        close(true);
        return;
      }
      onAdd([value]);
      setValue("");
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    }
  };
  // A paste is split in Python, as SoundCloud's artist field is, so "gunk, zayok, edyn" gives
  // three names and "Tyler, The Creator" stays one. A single name waits in the input for Enter.
  const onPaste = async (event) => {
    const pasted = event.clipboardData?.getData("text/plain");
    if (!pasted) return;
    event.preventDefault();
    const input = event.currentTarget;
    const text = value.slice(0, input.selectionStart) + pasted + value.slice(input.selectionEnd);
    const answer = await api.split_artist_names(text);
    if (reportError(answer)) return;
    if (answer.names.length > 1) {
      onAdd(answer.names);
      setValue("");
      return;
    }
    setValue(answer.names[0] ?? "");
  };
  // Leaving the input keeps what was typed, so a name is never lost to a stray click.
  const onBlur = () => {
    if (closing.current) return;
    if (value.trim()) onAdd([value]);
    close(false);
  };
  return html`
    <input
      ref=${ref}
      class="artist-add-input"
      type="text"
      aria-label=${text.addLabel[row]}
      placeholder=${text.addPlaceholder}
      data-drop=${drop ? "before" : undefined}
      value=${value}
      onInput=${onInput}
      onPaste=${onPaste}
      onKeyDown=${onKeyDown}
      onBlur=${onBlur}
    />
  `;
}
