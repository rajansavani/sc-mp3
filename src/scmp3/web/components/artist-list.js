// A track's artist list as the editor changes it: main and featured names, in order. Each edit
// shows at once and goes to Python, which cleans the names and renders the tags from them.
import { useEffect, useRef, useState } from "preact/hooks";

import { api } from "../api.js";
import { reportError } from "../store.js";

export const ROWS = ["main", "featured"];

export function otherRow(row) {
  return row === "main" ? "featured" : "main";
}

// Two spellings that differ only in case name the same artist. Python checks again as it cleans
// the names, and its answer is what the rows show.
function sameName(a, b) {
  return a.toLocaleLowerCase() === b.toLocaleLowerCase();
}

export function isCredited(list, name) {
  return ROWS.some((row) => list[row].some((credited) => sameName(credited, name)));
}

export function removed(list, name) {
  return {
    main: list.main.filter((credited) => credited !== name),
    featured: list.featured.filter((credited) => credited !== name),
  };
}

// The list with `name` taken out and put back in `row` at `index`, counted without it.
export function moved(list, name, row, index) {
  const without = removed(list, name);
  const names = [...without[row]];
  names.splice(Math.max(0, Math.min(index, names.length)), 0, name);
  return { ...without, [row]: names };
}

// The list with typed names added at the end of `row`, leaving out any already credited.
export function added(list, row, names) {
  let result = list;
  for (const name of names.map((typed) => typed.trim())) {
    if (!name || isCredited(result, name)) continue;
    result = { ...result, [row]: [...result[row], name] };
  }
  return result;
}

function sameList(a, b) {
  return ROWS.every((row) => {
    return a[row].length === b[row].length && a[row].every((name, index) => name === b[row][index]);
  });
}

function listOf(job) {
  return { main: job.suggestion.main_artists, featured: job.suggestion.featured };
}

// Sends the artist calls one at a time, so Python always ends on the newest edit. Each call
// answers with whether it's still the newest, and Save waits for the ones on their way.
function useCallsInOrder(drafts) {
  const last = useRef(Promise.resolve());
  const newest = useRef(0);
  const [waiting, setWaiting] = useState(0);
  function send(makeCall) {
    const number = ++newest.current;
    setWaiting((count) => count + 1);
    const call = last.current.then(makeCall);
    last.current = call;
    drafts.track(call);
    return call.then((answer) => {
      setWaiting((count) => count - 1);
      return { answer, newest: number === newest.current };
    });
  }
  return { send, waiting };
}

// The list the editor shows: the job's, or an edit on its way, until the job credits it.
export function useArtistList(job, drafts) {
  const [shown, setShown] = useState(null);
  const calls = useCallsInOrder(drafts);
  const current = listOf(job);
  useEffect(() => {
    if (shown && calls.waiting === 0 && sameList(shown, current)) setShown(null);
  });

  async function change(list) {
    // A tag dropped back where it was changes nothing, so it doesn't count as an edit.
    if (sameList(list, shown ?? current)) return;
    setShown(list);
    const { answer, newest } = await calls.send(() => {
      return api.set_artists(job.id, list.main, list.featured);
    });
    // A refused edit goes back to what the job credits.
    if (reportError(answer)) {
      if (newest) setShown(null);
      return;
    }
    // The names as Python cleaned them, which the job's event will credit.
    if (newest && answer.artists) setShown(answer.artists);
  }

  function reset() {
    setShown(null);
    calls.send(() => api.reset_artists(job.id)).then(({ answer }) => reportError(answer));
  }

  return {
    list: shown ?? current,
    edited: shown !== null || Boolean(job.artists_edited),
    change,
    reset,
  };
}
