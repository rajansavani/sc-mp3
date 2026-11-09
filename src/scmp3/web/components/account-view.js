// What the UI says about the SoundCloud account, shared by the top bar, the sign-in dialog, setup
// and Settings. `account` is the view Python sends: {status, user, stored_in, running, finishing,
// window_profile, problem}.
import { html } from "htm/preact";
import { useRef } from "preact/hooks";

import { api } from "../api.js";
import { reportError } from "../store.js";
import { strings } from "../strings/index.js";

const text = strings.account;

export function isSignedIn(account) {
  return account?.status === "signed_in";
}

export function hasEnded(account) {
  return account?.status === "ended";
}

// Sign out is offered whenever sc-mp3 keeps something from a sign-in: the tokens, or the browser
// profile where Google remembers the person.
export function canSignOut(account) {
  return isSignedIn(account) || hasEnded(account) || Boolean(account?.window_profile);
}

// The account's handle, as in its soundcloud.com/<permalink> address.
export function handle(user) {
  return `@${user.permalink}`;
}

export function chipLabel(user) {
  if (!user) return text.chipUnnamed;
  return text.chip(handle(user), text.plansShort[user.tier] ?? text.plansShort.free);
}

export function signedInLabel(user) {
  if (!user) return text.signedInUnnamed;
  return text.signedInAs(handle(user), text.plans[user.tier] ?? text.plans.free);
}

// Where the sign-in is kept, in the words of the system it runs on.
export function storedInLabel(account, os) {
  if (account.stored_in === "file") return text.storedInFile;
  return text.storedInKeyring(text.keyringNames[os]);
}

// Each task's own words for what went wrong. Signing in from a browser uses text.problems.
const PROBLEM_TEXTS = { window: text.windowProblems, sign_out: text.signOutProblems };

function texts(problem) {
  return PROBLEM_TEXTS[problem.task] ?? text.problems;
}

// What went wrong with a try, for the box it's about or for the whole form. A refusal names a
// pasted box, except for the window, which pastes nothing.
export function problemText(problem) {
  if (problem.kind === "refused" && problem.task !== "window") {
    return text.refused[problem.reason] ?? text.problems.other;
  }
  const words = texts(problem);
  return words[problem.kind] ?? words.other;
}

// The latest problem, unless it was there before this component first showed, so an old one
// isn't shown again when the dialog or a section opens.
export function useNewProblem(account) {
  const before = useRef(account?.problem?.id ?? null);
  const problem = account?.problem ?? null;
  return problem && problem.id !== before.current ? problem : null;
}

// Returns at once, and the account events say how it went.
export async function signOut() {
  reportError(await api.sign_out());
}

// Unavailable while any task runs, since the account takes one at a time.
export function SignOutButton({ account }) {
  const busy = Boolean(account.running);
  return html`
    <button
      type="button"
      class="control-button is-ghost"
      aria-disabled=${busy ? "true" : null}
      onClick=${busy ? null : signOut}
    >
      ${account.running === "sign_out" ? text.signingOut : text.signOut}
    </button>
  `;
}
