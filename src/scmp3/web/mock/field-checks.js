// The checks update_fields makes before it keeps a hand edit, with the reason codes the bridge
// sends. strings/main.js has the text for each.

const YEAR = /^\d{1,4}$/;
const TRACK = /^(\d{1,3})(?:\s*\/\s*(\d{1,3}))?$/;

// Why update_fields refuses a value, by field, or null when it's fine. Blank clears a field.
const FIELD_CHECKS = {
  year: (value) => (YEAR.test(value) && Number(value) >= 1 ? null : "NOT_A_YEAR"),
  track_no: (value) => {
    const match = TRACK.exec(value);
    if (!match || Number(match[1]) < 1) return "NOT_A_TRACK";
    return match[2] && Number(match[2]) < Number(match[1]) ? "TOTAL_BEFORE_TRACK" : null;
  },
};

export function refusedFields(patch) {
  const fields = {};
  for (const [field, value] of Object.entries(patch)) {
    const reason = value.trim() ? FIELD_CHECKS[field]?.(value.trim()) : null;
    if (reason) fields[field] = { reason };
  }
  return fields;
}
