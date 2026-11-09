// The SoundCloud account section: who is signed in and where it's kept, or a way to sign in, with
// Sign out whenever something of a sign-in is kept, and the browser the sign-in window opens in.
import { html } from "htm/preact";

import { openDialog, useStore } from "../../store.js";
import { strings } from "../../strings/index.js";
import {
  hasEnded,
  isSignedIn,
  problemText,
  SignOutButton,
  signedInLabel,
  storedInLabel,
  useNewProblem,
} from "../account-view.js";
import { currentOs } from "../FirstRun.js";
import { describedBy, FieldNotes, fieldId } from "./fields.js";

const text = strings.settings.account;

function Card({ title, detail, children }) {
  return html`
    <div class="settings-card">
      <span class="settings-card-text">
        <span class="settings-card-title">${title}</span>
        <span class="settings-hint">${detail}</span>
      </span>
      <span class="settings-card-actions">${children}</span>
    </div>
  `;
}

function SignInButton({ label }) {
  return html`
    <button type="button" class="control-button is-ghost" onClick=${() => openDialog("sign_in")}>
      ${label}
    </button>
  `;
}

function AccountCard({ account }) {
  if (isSignedIn(account)) {
    return html`
      <${Card} title=${signedInLabel(account.user)} detail=${storedInLabel(account, currentOs())}>
        <${SignOutButton} account=${account} />
      <//>
    `;
  }
  if (hasEnded(account)) {
    return html`
      <${Card} title=${text.ended} detail=${text.endedHint}>
        <${SignInButton} label=${text.signInAgain} />
        <${SignOutButton} account=${account} />
      <//>
    `;
  }
  // Signed out, with a browser profile for signing in still kept.
  if (account.window_profile) {
    return html`
      <${Card} title=${text.notSignedIn} detail=${text.profileKept}>
        <${SignInButton} label=${text.signIn} />
        <${SignOutButton} account=${account} />
      <//>
    `;
  }
  return html`
    <${Card} title=${text.notSignedIn} detail=${text.notSignedInHint}>
      <${SignInButton} label=${text.signIn} />
    <//>
  `;
}

const DEFAULT_BROWSER = "default";

// Which browser "Your default browser" opens, or why sc-mp3 uses another one.
function defaultHint(account) {
  const used = account.window_browser?.name;
  if (account.default_browser) return text.defaultIs(account.default_browser.name);
  return used ? text.defaultUnusable(used) : null;
}

// The browsers found on this computer, with the person's default first. Hidden when there are none.
function BrowserSetting({ account, settings, form }) {
  if (!account.browsers?.length) return null;
  const id = fieldId("signin_browser");
  const ids = account.browsers.map((browser) => browser.id);
  // A browser picked earlier and since removed shows as the default, which is what's used.
  const value = ids.includes(settings.signin_browser) ? settings.signin_browser : DEFAULT_BROWSER;
  const hint = value === DEFAULT_BROWSER ? defaultHint(account) : null;
  const error = form.errors.signin_browser;
  return html`
    <div class="field">
      <label class="field-label" for=${id}>${text.browserLabel}</label>
      <select
        id=${id}
        class="text-field"
        value=${value}
        onChange=${(event) => form.save({ signin_browser: event.currentTarget.value })}
        aria-invalid=${error ? "true" : null}
        aria-describedby=${describedBy(id, error, hint)}
      >
        <option value=${DEFAULT_BROWSER}>${text.defaultBrowser}</option>
        ${account.browsers.map(
          (browser) => html`<option key=${browser.id} value=${browser.id}>${browser.name}</option>`,
        )}
      </select>
      <${FieldNotes} id=${id} error=${error} hint=${hint} />
    </div>
  `;
}

export function AccountSection({ settings, form }) {
  const { account } = useStore();
  const problem = useNewProblem(account);
  return html`
    <${AccountCard} account=${account} />
    <${BrowserSetting} account=${account} settings=${settings} form=${form} />
    ${problem?.task === "sign_out" &&
    html`<p class="field-error" role="alert">${problemText(problem)}</p>`}
  `;
}
