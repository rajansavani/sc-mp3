// The SoundCloud sign-in dialog, shown in the dialog layer: the sign-in window, or two values
// copied from a browser. With someone signed in, it shows the account and Sign out instead, and
// Sign out stays below the ways in while anything of an earlier sign-in is kept.
import { html } from "htm/preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { api } from "../api.js";
import { addToast, closeDialog, reportError, useStore } from "../store.js";
import { strings } from "../strings/index.js";
import {
  canSignOut,
  handle,
  hasEnded,
  isSignedIn,
  problemText,
  SignOutButton,
  signedInLabel,
  storedInLabel,
  useNewProblem,
} from "./account-view.js";
import { currentOs } from "./FirstRun.js";
import { Icon } from "./icons.js";

const text = strings.account;

const WAYS = ["window", "browser"];
const TAB_KEYS = {
  ArrowRight: (index) => (index + 1) % WAYS.length,
  ArrowLeft: (index) => (index - 1 + WAYS.length) % WAYS.length,
  Home: () => 0,
  End: () => WAYS.length - 1,
};

// Closes the dialog once a sign-in started here has worked, and says who is signed in.
function useCloseOnSignIn(account) {
  const wasRunning = useRef(account.running);
  useEffect(() => {
    const was = wasRunning.current;
    wasRunning.current = account.running;
    if (!WAYS.includes(was) || account.running || !isSignedIn(account)) return;
    closeDialog();
    if (account.user) addToast("info", text.signedInToast(handle(account.user)));
  }, [account.running, account.status]);
}

function Head({ title, intro }) {
  return html`
    <div class="dialog-head">
      <div class="sign-in-heading">
        <h2 id="dialog-title" class="dialog-title sign-in-title">${title}</h2>
        ${intro && html`<p class="dialog-text">${intro}</p>`}
      </div>
      <button
        type="button"
        class="control-button is-ghost is-icon"
        aria-label=${strings.shell.close}
        onClick=${closeDialog}
      >
        <${Icon} name="close" size=${18} />
      </button>
    </div>
  `;
}

function WayTabs({ current, onPick }) {
  const buttons = useRef({});
  const onKeyDown = (event) => {
    const move = TAB_KEYS[event.key];
    if (!move) return;
    event.preventDefault();
    const next = WAYS[move(WAYS.indexOf(current))];
    onPick(next);
    buttons.current[next]?.focus();
  };
  return html`
    <div class="sign-in-tabs" role="tablist" aria-label=${text.waysLabel} onKeyDown=${onKeyDown}>
      ${WAYS.map(
        (way) => html`
          <button
            key=${way}
            ref=${(button) => (buttons.current[way] = button)}
            type="button"
            role="tab"
            id=${`sign-in-tab-${way}`}
            class="sign-in-tab"
            aria-selected=${String(way === current)}
            aria-controls="sign-in-panel"
            tabindex=${way === current ? 0 : -1}
            onClick=${() => onPick(way)}
          >
            ${text.ways[way]}
          </button>
        `,
      )}
    </div>
  `;
}

function FormProblem({ problem }) {
  if (!problem) return null;
  return html`
    <div class="banner" data-tone="danger" role="alert">
      <span class="banner-message">
        <${Icon} name="error" size=${20} className="tone-icon" />
        <span>${problemText(problem)}</span>
      </span>
    </div>
  `;
}

const openWindow = async () => reportError(await api.sign_in_window());
const finishWindow = async () => reportError(await api.finish_sign_in());
const cancelWindow = async () => reportError(await api.cancel_sign_in());

// The words while the window is open: whether it closes by itself, or that it's being read.
function waitingWords(account) {
  if (account.finishing) return text.window.finishing;
  const { waiting } = text.window;
  return account.window_closes_itself ? waiting.closesItself : waiting.byHand;
}

// While the window is open, with Cancel kept while finishing in case the browser won't close.
function WindowWaiting({ account }) {
  return html`
    <div class="sign-in-waiting" role="status">
      <span class="sign-in-waiting-title">${text.window.waitingTitle}</span>
      <span class="dialog-text">${waitingWords(account)}</span>
      <div class="sign-in-waiting-actions">
        ${!account.finishing &&
        html`
          <button type="button" class="control-button is-primary" onClick=${finishWindow}>
            ${text.window.finish}
          </button>
        `}
        <button type="button" class="control-button" onClick=${cancelWindow}>
          ${text.window.cancel}
        </button>
      </div>
    </div>
  `;
}

// Why the window gave no sign-in, with the two ways on from there.
function WindowProblem({ problem, onPick }) {
  return html`
    <div class="banner" data-tone="danger" role="alert">
      <span class="banner-message">
        <${Icon} name="error" size=${20} className="tone-icon" />
        <span>${problemText(problem)}</span>
      </span>
      <span class="banner-actions">
        <button type="button" class="control-button is-small" onClick=${openWindow}>
          ${text.window.tryAgain}
        </button>
        <button type="button" class="control-button is-small" onClick=${() => onPick("browser")}>
          ${text.window.useBrowser}
        </button>
      </span>
    </div>
  `;
}

function WindowWay({ account, problem, onPick }) {
  if (account.running === "window") return html`<${WindowWaiting} account=${account} />`;
  const failed = problem?.task === "window" ? problem : null;
  // After a failed try, the banner's own Try again takes the button's place.
  const next = failed
    ? html`<${WindowProblem} problem=${failed} onPick=${onPick} />`
    : html`
        <div>
          <button type="button" class="control-button is-large is-primary" onClick=${openWindow}>
            ${text.window.open}
          </button>
        </div>
      `;
  return html`
    <p>${text.window.intro}</p>
    <p class="dialog-text">${text.window.googleNote}</p>
    ${next}
    <p class="dialog-text sign-in-note">${text.window.note}</p>
  `;
}

function CopySteps() {
  return html`
    <p>${text.browser.intro}</p>
    <ol class="sign-in-steps">
      ${text.browser.steps.map((step, index) => {
        if (typeof step === "string") return html`<li key=${index}>${step}</li>`;
        const [before, name, after] = step;
        return html`<li key=${index}>${before}<code class="sign-in-name">${name}</code>${after}</li>`;
      })}
    </ol>
    <img
      class="sign-in-shot"
      src="images/signin-cookies.png"
      alt=${text.browser.screenshotAlt}
      width="616"
      height="336"
    />
  `;
}

function TokenField({ id, label, value, onInput, error }) {
  const errorId = `${id}-error`;
  return html`
    <div class="field">
      <label class="field-label sign-in-name" for=${id}>${label}</label>
      <input
        id=${id}
        class="text-field sign-in-value"
        type="password"
        autocomplete="off"
        spellcheck="false"
        value=${value}
        aria-invalid=${error ? "true" : null}
        aria-describedby=${error ? errorId : null}
        onInput=${(event) => onInput(event.currentTarget.value)}
      />
      ${error && html`<span id=${errorId} class="field-error">${error}</span>`}
    </div>
  `;
}

function BrowserWay({ account, problem }) {
  const [access, setAccess] = useState("");
  const [refresh, setRefresh] = useState("");
  const running = account.running === "browser";
  // The window's own refusal has its own words, so only a pasted value's marks a box.
  const refused = problem?.task === "browser" && problem.kind === "refused" ? problem : null;
  const fieldError = (field) => (refused?.field === field ? problemText(refused) : null);
  const submit = async (event) => {
    event.preventDefault();
    if (!running) reportError(await api.sign_in_from_browser(access, refresh));
  };
  return html`
    <${CopySteps} />
    <form class="sign-in-form" onSubmit=${submit}>
      <${TokenField}
        id="sign-in-access"
        label=${text.browser.accessLabel}
        value=${access}
        onInput=${setAccess}
        error=${fieldError("access")}
      />
      <${TokenField}
        id="sign-in-refresh"
        label=${text.browser.refreshLabel}
        value=${refresh}
        onInput=${setRefresh}
        error=${fieldError("refresh")}
      />
      <${FormProblem} problem=${problem?.task === "browser" && !refused ? problem : null} />
      <div>
        <button
          type="submit"
          class="control-button is-large is-primary"
          aria-disabled=${running ? "true" : null}
        >
          ${running ? text.browser.checking : text.browser.submit}
        </button>
      </div>
    </form>
  `;
}

const WAY_PANELS = { window: WindowWay, browser: BrowserWay };

function Footer({ account }) {
  const words = text.footer(text.keyringNames[currentOs()]);
  return html`<p class="sign-in-footer">${account.stored_in === "file" ? text.footerFile : words}</p>`;
}

// With no sign-in showing, what an earlier one left can still be deleted here.
function KeptSignIn({ account, problem }) {
  if (!canSignOut(account)) return null;
  return html`
    <div class="sign-in-kept">
      <span class="dialog-text">${text.keptNote}</span>
      <${SignOutButton} account=${account} />
    </div>
    <${FormProblem} problem=${problem?.task === "sign_out" ? problem : null} />
  `;
}

function SignInForm({ account, problem }) {
  // Without a browser the window can use, copying from a browser is the only way, so no tabs.
  const ways = account.window_browser ? WAYS : ["browser"];
  const [picked, setWay] = useState(ways[0]);
  const way = ways.includes(picked) ? picked : ways[0];
  const Panel = WAY_PANELS[way];
  return html`
    <div class="sign-in">
      <${Head} title=${text.signInTitle} intro=${text.intro} />
      ${hasEnded(account) &&
      html`
        <div class="banner" data-tone="warn" role="status">
          <span class="banner-message">
            <${Icon} name="warning" size=${20} className="tone-icon" />
            <span class="banner-title">${text.ended}</span>
          </span>
        </div>
      `}
      ${ways.length > 1 &&
      html`<${WayTabs} current=${way} onPick=${setWay} />`}
      <div
        id="sign-in-panel"
        class="sign-in-panel"
        role=${ways.length > 1 ? "tabpanel" : null}
        aria-labelledby=${ways.length > 1 ? `sign-in-tab-${way}` : null}
      >
        <${Panel} account=${account} problem=${problem} onPick=${setWay} />
      </div>
      <${Footer} account=${account} />
      <${KeptSignIn} account=${account} problem=${problem} />
    </div>
  `;
}

function AccountPanel({ account, problem }) {
  return html`
    <div class="sign-in">
      <${Head} title=${text.accountTitle} />
      <div class="settings-card">
        <span class="settings-card-text">
          <span class="settings-card-title">${signedInLabel(account.user)}</span>
          <span class="settings-card-detail">${storedInLabel(account, currentOs())}</span>
        </span>
        <${SignOutButton} account=${account} />
      </div>
      <${FormProblem} problem=${problem} />
    </div>
  `;
}

export function SignInDialog() {
  const { account } = useStore();
  useCloseOnSignIn(account);
  // Kept here, so a sign-out's problem still shows once the panel turns into the sign-in form.
  const problem = useNewProblem(account);
  if (isSignedIn(account)) {
    return html`<${AccountPanel} account=${account} problem=${problem} />`;
  }
  return html`<${SignInForm} account=${account} problem=${problem} />`;
}
