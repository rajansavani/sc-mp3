// Stands in for Python's drop handler on the page's body (app/drops.py), which adds dropped
// files by their full paths. The mock only learns their names, which its made-up queue accepts.
import { add_inputs } from "./jobs.js";
import { emit } from "./queue-sim.js";

// The browser calls the second of two same-named files "song (1).mp3", as WebView2 does.
const RENAMED_TWIN = / \(\d+\)(\.[^.]+)$/;

function onDrop(event) {
  const names = [...(event.dataTransfer?.files ?? [])].map((file) => file.name);
  if (names.length === 0) return;
  const twins = names.map((name) => name.replace(RENAMED_TWIN, "$1"));
  if (new Set(twins).size < names.length) {
    emit({ type: "rejected", rejected: [{ input: "", reason: "SAME_NAME_DROP" }] });
    return;
  }
  const { rejected } = add_inputs(names.join("\n"));
  if (rejected.length > 0) emit({ type: "rejected", rejected });
}

document.body.addEventListener("drop", onDrop);
