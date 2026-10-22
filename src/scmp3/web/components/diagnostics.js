// Copying the diagnostics for a bug report, from Settings and from the page shown when the
// window couldn't start.
import { api } from "../api.js";

// Copies the diagnostics, and says whether that worked.
export async function copyDiagnostics() {
  try {
    const result = await api.diagnostics();
    if (result?.error) return false;
    await navigator.clipboard.writeText(result.text);
    return true;
  } catch (error) {
    console.error("The diagnostics couldn't be copied", error);
    return false;
  }
}
