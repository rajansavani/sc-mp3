// What's typed into the editor's fields, sent as hand edits a moment after typing stops.
import { useEffect, useRef, useState } from "preact/hooks";

import { api } from "../api.js";
import { reportError } from "../store.js";

// Typing waits this long before it's sent, so the queue updates without a call per key.
const COMMIT_DELAY_MS = 350;

// Each field's draft: what's typed, what was last sent, why it was refused, the timer waiting
// to send it, and the call sending it.
function newDraft() {
  return { typed: undefined, sent: undefined, reason: null, timer: null, sending: null };
}

// A draft stays until the job shows it as the hand edit, so a slow event can't undo typing.
function isSettled(draft, override, focused) {
  if (draft.typed === undefined || focused) return false;
  if (draft.timer || draft.sending || draft.reason) return false;
  return override === draft.typed;
}

// The editor is keyed by job, so each job gets its own drafts.
export function useFieldDrafts(job, renderedValue) {
  const [, setVersion] = useState(0);
  const rerender = () => setVersion((version) => version + 1);
  const drafts = useRef(new Map()).current;
  // Other edits on their way, such as an artist list, which Save waits for too.
  const calls = useRef(new Set()).current;
  const focused = useRef(null);

  function draftOf(field) {
    if (!drafts.has(field)) drafts.set(field, newDraft());
    return drafts.get(field);
  }

  function answer(draft, field, value, result) {
    // An older answer can arrive after a newer one, since each call runs on its own thread.
    if (draft.sent !== value) return;
    const refused = result?.error === "invalid_fields";
    draft.reason = refused ? (result.fields?.[field]?.reason ?? "other") : null;
    if (!refused) reportError(result);
  }

  // Sends a field's typed text now, unless that exact text was sent already.
  function send(field) {
    const draft = draftOf(field);
    clearTimeout(draft.timer);
    draft.timer = null;
    if (draft.typed === undefined || draft.typed === draft.sent) return draft.sending;
    const value = draft.typed;
    draft.sent = value;
    const sending = api
      .update_fields(job.id, { [field]: value })
      .then((result) => answer(draft, field, value, result))
      .finally(() => {
        if (draft.sending === sending) draft.sending = null;
        rerender();
      });
    draft.sending = sending;
    return sending;
  }

  // Leaving the job sends whatever was still waiting.
  useEffect(() => {
    return () => {
      for (const [field, draft] of drafts) if (draft.timer) send(field);
    };
  }, []);

  useEffect(() => {
    let cleared = false;
    for (const [field, draft] of drafts) {
      if (!isSettled(draft, job.overrides[field], focused.current === field)) continue;
      drafts.set(field, newDraft());
      cleared = true;
    }
    if (cleared) rerender();
  });

  // Keeps a call in the edits Save waits for, and hands it back.
  function track(call) {
    calls.add(call);
    call.finally(() => calls.delete(call));
    return call;
  }

  const firstRefused = () => [...drafts].find(([, draft]) => draft.reason)?.[0] ?? null;
  return {
    value: (field) => draftOf(field).typed ?? renderedValue(job, field),
    reason: (field) => draftOf(field).reason,
    isOverridden: (field) => field in job.overrides,
    firstRefused,
    type(field, value) {
      const draft = draftOf(field);
      draft.typed = value;
      clearTimeout(draft.timer);
      draft.timer = setTimeout(() => send(field), COMMIT_DELAY_MS);
      rerender();
    },
    focus(field) {
      focused.current = field;
    },
    blur(field) {
      focused.current = null;
      send(field);
      rerender();
    },
    // A value sent and not yet shown on the job, as when a click blurs the field, would land
    // after a reset sent at once, so the reset waits for it and then drops it too.
    reset(field) {
      const draft = draftOf(field);
      clearTimeout(draft.timer);
      drafts.set(field, newDraft());
      if (field in job.overrides || draft.sent !== undefined) {
        const resetting = Promise.resolve(draft.sending).then(() => {
          return api.reset_field(job.id, field);
        });
        track(resetting).then(reportError);
      }
      rerender();
    },
    track,
    // Sends everything typed so far. Resolves to false when a field holds a refused value.
    async flush() {
      const waiting = [...drafts.keys()].map((field) => send(field));
      await Promise.all([...waiting, ...calls]);
      return firstRefused() === null;
    },
  };
}
