// The options menu, as a full-window view: a list of sections beside every option. Each option
// saves as soon as it changes, and the previews come from the real suggestion engine.
import { html } from "htm/preact";
import { useEffect, useRef, useState } from "preact/hooks";

import { closeView, useStore } from "../store.js";
import { strings } from "../strings/index.js";
import { AccountSection } from "./settings/account.js";
import { AudioSection } from "./settings/audio.js";
import { EngineSection } from "./settings/engine.js";
import { SpotifyHelpSection } from "./settings/help.js";
import { LibrarySection } from "./settings/library.js";
import { Logo, SaveBanner } from "./settings/parts.js";
import { usePreview, useSettingsSaver } from "./settings/saving.js";
import { TitleSection } from "./settings/titles.js";
import { WorkflowSection } from "./settings/workflow.js";

const text = strings.settings;

// The sections in order. A new section is its own module in settings/ and one entry here. A
// section that's only rows of switches sits tighter, as in the design.
const SECTIONS = [
  { id: "library", Section: LibrarySection },
  { id: "titles", Section: TitleSection },
  { id: "audio", Section: AudioSection },
  { id: "workflow", Section: WorkflowSection, rows: true },
  { id: "account", Section: AccountSection },
  { id: "engine", Section: EngineSection },
  { id: "help", Section: SpotifyHelpSection },
];
const SECTION_IDS = SECTIONS.map((section) => section.id);

// How far below the top of the scrolling area a section's top can be and still count as current.
const CURRENT_SECTION_OFFSET = 40;

function sectionId(id) {
  return `settings-${id}`;
}

function titleId(id) {
  return `settings-${id}-title`;
}

// The section the list marks: the last one whose top has scrolled up to the top. At the end of
// the page, where the last few can't reach the top, it's the one picked in the list, if any.
function currentSection(scroller, picked) {
  const atEnd = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2;
  if (atEnd) return picked.current ?? SECTION_IDS.at(-1);
  picked.current = null;
  const top = scroller.getBoundingClientRect().top + CURRENT_SECTION_OFFSET;
  const reached = SECTION_IDS.filter(
    (id) => document.getElementById(sectionId(id)).getBoundingClientRect().top <= top,
  );
  return reached.at(-1) ?? SECTION_IDS[0];
}

function useCurrentSection(scrollerRef) {
  const [current, setCurrent] = useState(SECTION_IDS[0]);
  const picked = useRef(null);
  useEffect(() => {
    const scroller = scrollerRef.current;
    const onScroll = () => setCurrent(currentSection(scroller, picked));
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", onScroll);
  }, []);

  // Scrolls to a section and moves focus to its heading, without changing the page's address.
  function jumpTo(event, id) {
    event.preventDefault();
    picked.current = id;
    setCurrent(id);
    const heading = document.getElementById(titleId(id));
    document.getElementById(sectionId(id)).scrollIntoView({ block: "start" });
    heading.focus({ preventScroll: true });
  }
  return [current, jumpTo];
}

function SectionList({ current, jumpTo }) {
  return html`
    <nav class="settings-nav" aria-label=${text.sectionsLabel}>
      <ul class="settings-nav-list">
        ${SECTION_IDS.map(
          (id) => html`
            <li key=${id}>
              <a
                class="settings-nav-link"
                href=${`#${sectionId(id)}`}
                aria-current=${current === id ? "true" : null}
                onClick=${(event) => jumpTo(event, id)}
              >
                ${text.sections[id]}
              </a>
            </li>
          `,
        )}
      </ul>
    </nav>
  `;
}

export function Settings() {
  const state = useStore();
  const form = useSettingsSaver();
  const scroller = useRef(null);
  const [current, jumpTo] = useCurrentSection(scroller);
  // One preview of the sample in the style setting, for every section that shows it.
  const preview = usePreview(state.settings);
  return html`
    <div class="settings">
      <header class="settings-header">
        <div class="settings-brand">
          <${Logo} />
          <span class="settings-brand-name">${text.title}</span>
        </div>
        <button type="button" class="control-button is-ghost" onClick=${closeView}>
          ${text.backToQueue}
        </button>
      </header>
      <div class="settings-body">
        <${SectionList} current=${current} jumpTo=${jumpTo} />
        <div class="settings-scroll" ref=${scroller}>
          <main class="settings-main">
            <${SaveBanner} message=${form.banner} onDismiss=${form.dismissBanner} />
            ${SECTIONS.map(
              ({ id, Section, rows }) => html`
                <section
                  key=${id}
                  id=${sectionId(id)}
                  class=${rows ? "settings-section settings-section-rows" : "settings-section"}
                  aria-labelledby=${titleId(id)}
                >
                  <h2 id=${titleId(id)} class="settings-section-title" tabindex="-1">
                    ${text.sections[id]}
                  </h2>
                  <${Section}
                    settings=${state.settings}
                    form=${form}
                    preview=${preview}
                    components=${state.components}
                  />
                </section>
              `,
            )}
          </main>
        </div>
      </div>
    </div>
  `;
}
