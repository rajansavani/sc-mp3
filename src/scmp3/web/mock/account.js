// The SoundCloud account calls. Every way of signing in signs in the same made-up account.
// ?account=signed_in, ?account=ended or ?account=file starts in that state. The pretend computer
// has Edge and Brave, with Brave the default, ?browser=edge makes Edge the default, and
// ?browser=none has neither. The pretend window closes by itself, or with ?closes=by_hand the
// person closes it. ?window=closed_early and ?window=timed_out show those endings. ?profile=kept
// starts signed out with the browser profile from an earlier sign-in still kept.
// ?account_fails=<kind>, such as offline or no_browser, makes every sign-in and sign-out fail
// with that problem kind, so each message can be seen.

const SAMPLE_USER = {
  username: "Sam",
  permalink: "sam-listens",
  avatar_url: null,
  tier: "go_plus",
};

// How long the pretend window stays open, and how long closing and reading it takes.
const SIGN_IN_WINDOW_MS = 4000;
const FINISH_MS = 1200;
const CHECK_MS = 600;

const JWT = /^[\w-]+\.[\w-]+\.[\w-]*$/;
// A refresh token is 32 characters, like a client_id.
const REFRESH_LENGTH = 32;

const query = new URLSearchParams(window.location.search);
const start = query.get("account");
const WINDOW_PROBLEMS = { closed_early: "no_sign_in", timed_out: "timed_out" };
const EDGE = { id: "edge", name: "Edge" };
const BRAVE = { id: "brave", name: "Brave" };
const DEFAULT_BROWSERS = { edge: EDGE, none: null };
const defaultBrowser = query.has("browser") ? DEFAULT_BROWSERS[query.get("browser")] : BRAVE;
const failsWith = query.get("account_fails");
let windowTimer = null;

let account = {
  status: start === "ended" ? "ended" : start ? "signed_in" : "signed_out",
  window_browser: defaultBrowser,
  default_browser: defaultBrowser,
  browsers: defaultBrowser ? [EDGE, BRAVE] : [],
  window_closes_itself: query.get("closes") !== "by_hand",
  // A sign-in through the window, or one that ended, leaves the window's profile behind.
  window_profile: start === "ended" || query.get("profile") === "kept",
  user: start ? { ...SAMPLE_USER } : null,
  stored_in: start === "file" ? "file" : start ? "keyring" : null,
  running: null,
  finishing: false,
  problem: null,
};
let problemId = 0;

function emit() {
  window.scmp3.emit(JSON.parse(JSON.stringify({ type: "account", account })));
}

function change(patch) {
  account = { ...account, ...patch };
  emit();
}

function begin(task, finish, delay) {
  if (account.running) return {};
  change({ running: task, problem: null });
  const timer = setTimeout(finish, delay);
  if (task === "window") windowTimer = timer;
  return {};
}

function signedIn(patch = {}) {
  change({
    ...patch,
    status: "signed_in",
    user: { ...SAMPLE_USER },
    stored_in: "keyring",
    running: null,
    finishing: false,
  });
}

function failed(task, kind, field = null, reason = null) {
  problemId += 1;
  const problem = { id: problemId, task, kind, field, reason };
  change({ running: null, finishing: false, problem });
}

// The same shape rules as core/tokens.py, roughly, so each refusal can be seen in the dialog.
function refusal(access, refresh) {
  const a = access.trim();
  const r = refresh.trim();
  if (!a) return ["access", "access_empty"];
  if (a.length === REFRESH_LENGTH) return ["access", "access_is_refresh"];
  if (!JWT.test(a)) return ["access", "access_not_token"];
  if (!r) return ["refresh", "refresh_empty"];
  if (JWT.test(r)) return ["refresh", "refresh_is_access"];
  if (r.length < 16 || /[\s",;\\]/.test(r)) return ["refresh", "refresh_not_token"];
  return null;
}

export function snapshotPart() {
  return { account };
}

// How the pretend window ends, as ?window= asks.
function windowEnding() {
  const problem = WINDOW_PROBLEMS[query.get("window")];
  return problem ? () => failed("window", problem) : () => signedIn({ window_profile: true });
}

// The window closes, by itself or as asked, and its sign-in is read.
function closeAndRead() {
  clearTimeout(windowTimer);
  change({ finishing: true });
  windowTimer = setTimeout(windowEnding(), FINISH_MS);
}

export function sign_in_window() {
  if (failsWith) return begin("window", () => failed("window", failsWith), CHECK_MS);
  // The pretend window closes by itself and is read, or the person closes it after a while.
  const ending = account.window_closes_itself ? closeAndRead : windowEnding();
  return begin("window", ending, SIGN_IN_WINDOW_MS);
}

export function finish_sign_in() {
  if (account.running !== "window" || account.finishing) return {};
  closeAndRead();
  return {};
}

export function cancel_sign_in() {
  if (account.running !== "window") return {};
  clearTimeout(windowTimer);
  change({ running: null, finishing: false });
  return {};
}

export function sign_in_from_browser(access_token, refresh_token) {
  const refused = refusal(access_token, refresh_token);
  if (refused) return begin("browser", () => failed("browser", "refused", ...refused), 0);
  if (failsWith) return begin("browser", () => failed("browser", failsWith), CHECK_MS);
  // A second value that starts with "offline" shows how a failure that isn't the shape looks.
  if (refresh_token.trim().startsWith("offline")) {
    return begin("browser", () => failed("browser", "offline"), CHECK_MS);
  }
  return begin("browser", signedIn, CHECK_MS);
}

export function sign_out() {
  if (failsWith) return begin("sign_out", () => failed("sign_out", failsWith), CHECK_MS);
  const finish = () =>
    change({
      status: "signed_out",
      user: null,
      stored_in: null,
      window_profile: false,
      running: null,
    });
  return begin("sign_out", finish, CHECK_MS);
}
