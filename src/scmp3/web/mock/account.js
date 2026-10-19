// The SoundCloud account calls. Every way of signing in signs in the same made-up account.

const SAMPLE_ACCOUNT = { username: "Sam", permalink: "sam-listens", avatar_url: null, tier: "go_plus" };

// How long the pretend sign-in window stays open.
const SIGN_IN_WINDOW_MS = 800;

let account = null;

function emit(event) {
  window.scmp3.emit(JSON.parse(JSON.stringify(event)));
}

function signIn() {
  account = { ...SAMPLE_ACCOUNT };
  emit({ type: "account", account });
}

export function snapshotPart() {
  return { account };
}

export function sign_in_window() {
  setTimeout(signIn, SIGN_IN_WINDOW_MS);
  return {};
}

export function sign_in_firefox() {
  signIn();
  return {};
}

export function sign_in_token(token) {
  if (!token || !token.trim()) return { error: "invalid_token" };
  signIn();
  return {};
}

export function sign_out() {
  account = null;
  emit({ type: "account", account });
  return {};
}
