// Saving settings as they change, one patch at a time, and previews that follow what was saved.
import { useEffect, useRef, useState } from "preact/hooks";

import { api } from "../../api.js";
import { setSettings } from "../../store.js";
import { strings } from "../../strings/index.js";

const text = strings.settings;

function withoutKeys(object, keys) {
  return Object.fromEntries(Object.entries(object).filter(([key]) => !keys.includes(key)));
}

// Saves settings in the order they change, one call at a time, so an older value never lands
// after a newer one. Keeps each setting's problem, and a banner for errors that aren't a field's.
export function useSettingsSaver() {
  const [errors, setErrors] = useState({});
  const [banner, setBanner] = useState(null);
  const queue = useRef(Promise.resolve());

  // Resolves to {saved, problems}, with the problem of each setting that was refused. It never
  // rejects.
  function save(patch) {
    const saving = queue.current.then(() => saveNow(patch, setErrors, setBanner));
    queue.current = saving;
    return saving;
  }

  return { errors, banner, save, dismissBanner: () => setBanner(null) };
}

async function saveNow(patch, setErrors, setBanner) {
  const result = await api.update_settings(patch);
  const problems = result.fields ?? {};
  if (result.error === "invalid_settings" && Object.keys(problems).length > 0) {
    setErrors((current) => ({ ...current, ...problems }));
    return { saved: false, problems };
  }
  if (result.error) {
    setBanner(text.saveErrors[result.error] ?? strings.shell.unknownError(result.error));
    return { saved: false, problems: {} };
  }
  setSettings(result.settings);
  setErrors((current) => withoutKeys(current, Object.keys(patch)));
  setBanner(null);
  return { saved: true, problems: {} };
}

// The preview_render answer for a title style, or for the style setting when it's null. It's
// asked again whenever the saved settings change.
export function usePreview(settings, style = null) {
  const [preview, setPreview] = useState(null);
  useEffect(() => {
    // An answer to an older request may arrive after a newer one, so only the latest counts.
    let latest = true;
    api.preview_render(null, style).then((result) => {
      if (latest) setPreview(result.error ? null : result);
    });
    return () => {
      latest = false;
    };
  }, [settings, style]);
  return preview;
}
