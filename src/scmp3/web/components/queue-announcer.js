// What the queue's live region says: items that now need review, were saved, or stopped with
// an error. A burst of changes, such as a paste of many links, becomes one message.
import { useEffect, useRef, useState } from "preact/hooks";

import { strings } from "../strings/index.js";
import { jobStatus, jobTitle } from "./job-view.js";

const text = strings.main;

// Changes that arrive this close together are announced together.
const GATHER_MS = 800;
// The states someone acts on. Fetching, downloading and saving aren't announced.
const ANNOUNCED = new Set(["needs_review", "saved", "error"]);

function statusWords(job) {
  // The download's percentage changes too often to be worth reading out.
  return job.state === "needs_review" ? text.status.needsReview : jobStatus(job).text;
}

// One sentence per state: the item itself when it's alone, or a count.
export function announcement(changed) {
  const sentences = [...ANNOUNCED].map((state) => {
    const jobs = changed.filter((job) => job.state === state);
    if (jobs.length === 0) return null;
    if (jobs.length === 1) return text.announce(jobTitle(jobs[0]), statusWords(jobs[0]));
    return text.announceMany[state](jobs.length);
  });
  return sentences.filter(Boolean).join(". ");
}

// Errors are announced here too when their job is selected, once, whatever their tone, since a
// banner that appears with its text already in it isn't reliably read out.
export function useAnnouncements(jobs) {
  const [message, setMessage] = useState("");
  const seen = useRef(new Map());
  const waiting = useRef(new Map());
  const timer = useRef(null);
  useEffect(() => {
    for (const job of jobs) {
      const before = seen.current.get(job.id);
      const turned = before !== undefined && before !== job.state && ANNOUNCED.has(job.state);
      if (turned) waiting.current.set(job.id, job);
    }
    seen.current = new Map(jobs.map((job) => [job.id, job.state]));
    if (waiting.current.size === 0 || timer.current) return;
    timer.current = setTimeout(() => {
      setMessage(announcement([...waiting.current.values()]));
      waiting.current.clear();
      timer.current = null;
    }, GATHER_MS);
  }, [jobs]);
  useEffect(() => () => clearTimeout(timer.current), []);
  return message;
}
