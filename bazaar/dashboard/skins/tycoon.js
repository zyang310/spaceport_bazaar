// Retro tycoon: a space-age board game.  Chunky ink outlines, flip-digit
// counters, starburst stickers, and a sunburst behind our planet with a health
// dial pinned to it.
//
// The shapes only this skin draws.  Colours and type are in tycoon.css.
(() => {
"use strict";

const GEOMETRY = { laneR: 112, planet: { far: 56, near: 64 }, ship: 34 };

const SPARKS = `
<svg width="720" height="600" viewBox="0 0 720 600">
  <g class="tw"><use href="#tycoon-spark" x="32" y="63" width="24" height="24" fill="#f4b531"></use></g>
  <use href="#tycoon-spark" x="560" y="54" width="18" height="18" fill="#fff6e0"></use>
  <g class="tw"><use href="#tycoon-spark" x="684" y="475" width="20" height="20" fill="#5ec4e8"></use></g>
  <use href="#tycoon-spark" x="30" y="490" width="16" height="16" fill="#fff6e0"></use>
  <g class="tw"><use href="#tycoon-spark" x="125" y="564" width="18" height="18" fill="#f4b531"></use></g>
  <use href="#tycoon-atom" x="666" y="107" width="36" height="36" stroke="#fff6e0" stroke-opacity="0.35" stroke-width="1.3" fill="#fff6e0" fill-opacity="0.35"></use>
  <use href="#tycoon-atom" x="144" y="128" width="30" height="30" stroke="#5ec4e8" stroke-opacity="0.4" stroke-width="1.3" fill="#5ec4e8" fill-opacity="0.4"></use>
  <circle cx="604" cy="571" r="3" fill="#fff6e0" fill-opacity="0.45"></circle>
  <circle cx="549" cy="315" r="2.5" fill="#fff6e0" fill-opacity="0.55"></circle>
  <circle cx="150" cy="330" r="2.5" fill="#fff6e0" fill-opacity="0.5"></circle>
</svg>`;

// The station orbit, a faint outer one, and a dotted ring where the lanes start.
const orbits = () => `
<svg width="720" height="600" viewBox="0 0 720 600">
  <ellipse cx="${STAGE.cx}" cy="${STAGE.cy}" rx="${STAGE.rx + 46}" ry="${STAGE.ry + 40}" fill="none" stroke="#fff6e0" stroke-opacity="0.06" stroke-width="2"></ellipse>
  <ellipse cx="${STAGE.cx}" cy="${STAGE.cy}" rx="${STAGE.rx}" ry="${STAGE.ry}" fill="none" stroke="#fff6e0" stroke-opacity="0.3" stroke-width="2.5" stroke-dasharray="2 10" stroke-linecap="round"></ellipse>
  <circle cx="${STAGE.cx}" cy="${STAGE.cy}" r="${GEOMETRY.laneR}" fill="none" stroke="#fff6e0" stroke-opacity="0.35" stroke-width="2.5" stroke-dasharray="1 9" stroke-linecap="round"></circle>
</svg>`;

// A robot in a straw boater and a bow tie.
const MASCOT = `
<svg class="mascot" width="50" height="50" viewBox="0 0 50 50" aria-hidden="true">
  <circle cx="25" cy="27" r="22" fill="#fff6e0" stroke="#1b2a3a" stroke-width="2.5"></circle>
  <rect x="13" y="19" width="24" height="20" rx="7" fill="#5ec4e8" stroke="#1b2a3a" stroke-width="2.2"></rect>
  <rect x="17" y="23" width="16" height="9" rx="4.5" fill="#fff6e0" stroke="#1b2a3a" stroke-width="1.6"></rect>
  <circle cx="21.5" cy="27.5" r="2.2" fill="#1b2a3a"></circle>
  <circle cx="28.5" cy="27.5" r="2.2" fill="#1b2a3a"></circle>
  <path d="M22 35 Q25 37 28 35" fill="none" stroke="#1b2a3a" stroke-width="1.6" stroke-linecap="round"></path>
  <path d="M19 38.5 L25 41 L19 43.5 Z M31 38.5 L25 41 L31 43.5 Z" fill="#e8483b" stroke="#1b2a3a" stroke-width="1.4" stroke-linejoin="round"></path>
  <circle cx="25" cy="41" r="1.6" fill="#e8483b" stroke="#1b2a3a" stroke-width="1.2"></circle>
  <rect x="17" y="10" width="16" height="9" rx="1.5" fill="#f4d35e" stroke="#1b2a3a" stroke-width="1.8"></rect>
  <rect x="17" y="14.5" width="16" height="3" fill="#e8483b"></rect>
  <ellipse cx="25" cy="19" rx="14" ry="3.2" fill="#f4d35e" stroke="#1b2a3a" stroke-width="1.8"></ellipse>
</svg>`;

// A starburst sticker with a word on it.
function burst(cls, text, hidden) {
  return h("span", { class: `burst ${cls}`, "aria-hidden": hidden ? "true" : null },
    s("svg", { class: "burst-shape", viewBox: "0 0 48 48", "aria-hidden": "true" }, s("use", { href: "#tycoon-burst", width: 48, height: 48 })),
    h("span", { class: "burst-text" }, text));
}

const icon = (...paths) => s("svg", { width: 13, height: 13, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", "stroke-width": 2.2,
                                      "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" },
  paths.map((d) => s("path", { d })));
const EVENT_ICONS = {
  trade: () => icon("M2.5 5.5 H12.5 L10 3", "M13.5 10.5 H3.5 L6 13"),   // goods both ways
  info: () => icon("M8 7.2 L8 12.4", "M8 3.8 L8 3.9"),                  // an "i"
};

// The health dial: a point on its arc, from 0 at the left to 1 at the right.
const dial = (f, r) => `${f1(50 - r * Math.cos(Math.PI * f))} ${f1(50 - r * Math.sin(Math.PI * f))}`;
const band = (from, to, colour) => s("path", { d: `M${dial(from, 38)} A38 38 0 0 1 ${dial(to, 38)}`, fill: "none", stroke: colour, "stroke-width": 9 });

// Our planet: a slowly turning sunburst behind it, a ribbon with our name, and
// a dial whose bands change colour where the page's health tones do.
function stage() {
  const alarm = h("div", { class: "home-alarm", hidden: true });
  const sel = h("div", { class: "home-sel", hidden: true });
  const ribbon = h("span", { class: "ribbon home-ribbon" });
  const needle = s("path", { stroke: "#1b2a3a", "stroke-width": 3.5, "stroke-linecap": "round" });
  const ticks = Array.from({ length: 11 }, (_, i) => `M${dial(i / 10, 44)} L${dial(i / 10, 48)}`).join(" ");
  const face = s("svg", { class: "dial", width: 100, height: 56, viewBox: "0 0 100 56", role: "img" },
    band(0, 0.3, "#e8483b"), band(0.3, 0.6, "#f4b531"), band(0.6, 1, "#3aa655"),
    s("path", { d: ticks, stroke: "#1b2a3a", "stroke-width": 2, "stroke-linecap": "round" }),
    needle,
    s("circle", { cx: 50, cy: 50, r: 5, fill: "#e8483b", stroke: "#1b2a3a", "stroke-width": 2 }));
  const value = h("span", { class: "v" }), of = h("span", { class: "of" });
  return {
    back: [h("div", { class: "sunburst" }, h("div", { class: "rays" })), markup(SPARKS), markup(orbits())],
    mid: [alarm, sel],
    front: [ribbon, h("div", { class: "home-gauge" }, face, h("div", { class: "reading" }, h("span", { class: "k" }, "Health"), value, of))],
    update({ hd, hl, pct, tone, alarm: alarming, selected: chosen }) {
      alarm.hidden = !alarming;
      sel.hidden = !chosen;
      ribbon.textContent = `${hd.station} · us`;
      needle.setAttribute("d", `M50 50 L${dial(Math.min(1, pct), 30)}`);
      face.setAttribute("aria-label", `Health ${hl.health} of ${hl.max_health}`);
      value.className = `v ${tone}`;
      value.textContent = fmt(hl.health);
      of.textContent = `/${fmt(hl.max_health)}`;
    },
  };
}

registerSkin({
  id: "tycoon",
  label: "Tycoon",
  title: "Retro tycoon",
  rocket: [40, 20],
  geometry: GEOMETRY,
  wordmark: () => [
    s("svg", { class: "logo-rocket", viewBox: "0 0 40 20", width: 46, height: 23, "aria-hidden": "true" },
      s("use", { href: "#tycoon-rocket", width: 40, height: 20 })),
    h("span", { class: "logo-words" }, h("span", { class: "logo-script" }, "Spaceport"), h("span", { class: "wordmark" }, "Bazaar")),
  ],
  tick: (hd) => h("span", { class: "tickno" }, h("span", { class: "k" }, "Tick"), digits(hd.tick), h("span", { class: "of" }, `/ ${fmt(hd.duration_ticks)}`)),
  track: (pct) => [
    h("span", { class: "rail" }, h("span", { class: "fill", style: `width:${pct}%` })),
    h("span", { class: "marker", style: `left:${pct}%` }, shipIcon(null, 38)),
  ],
  levelBadge: (level) => burst(level.tone, `${level.icon} ${level.label}`),
  verdictMark: (tone) => burst("mark", VERDICT_ICON[tone], true),
  mascot: () => markup(MASCOT),
  stage,
  eventIcons: EVENT_ICONS,
  symbols: `
<pattern id="tycoon-dots" width="5" height="5" patternUnits="userSpaceOnUse"><circle cx="2.5" cy="2.5" r="1.25" fill="#1b2a3a"></circle></pattern>
<symbol id="tycoon-ocean" viewBox="0 0 100 100">
<path d="M18 32 C24 20 40 20 46 28 C52 36 44 44 36 44 C28 44 26 52 20 50 C12 48 13 40 18 32 Z" fill="#9ad48a" stroke="#1b2a3a" stroke-linejoin="round" vector-effect="non-scaling-stroke"></path>
<path d="M60 60 C66 52 80 56 84 64 C88 74 78 82 70 80 C62 78 64 70 58 68 C54 66 55 62 60 60 Z" fill="#9ad48a" stroke="#1b2a3a" stroke-linejoin="round" vector-effect="non-scaling-stroke"></path>
<path d="M28 74 C32 70 40 71 41 76 C42 80 36 82 32 80 C28 79 26 77 28 74 Z" fill="#9ad48a" stroke="#1b2a3a" stroke-linejoin="round" vector-effect="non-scaling-stroke"></path>
<path d="M6 62 C18 56 32 60 46 55" fill="none" stroke="#ffffff" stroke-width="3.5" stroke-linecap="round"></path>
<path d="M54 22 C64 17 76 20 84 26" fill="none" stroke="#ffffff" stroke-width="3.5" stroke-linecap="round"></path>
<path d="M-10 -10 H110 V110 H-10 Z M-14 40 A54 54 0 1 0 94 40 A54 54 0 1 0 -14 40 Z" fill="url(#tycoon-dots)" fill-opacity="0.45" fill-rule="evenodd"></path>
<ellipse cx="28" cy="20" rx="9" ry="4.5" transform="rotate(-32 28 20)" fill="#ffffff" fill-opacity="0.85"></ellipse>
</symbol>
<symbol id="tycoon-farm" viewBox="0 0 100 100">
<path d="M18 30 C28 20 48 22 54 32 C50 44 30 46 18 40 Z" fill="#f4d35e" stroke="#1b2a3a" stroke-linejoin="round" vector-effect="non-scaling-stroke"></path>
<path d="M23 31 C33 26 44 28 50 32 M22 37 C32 34 43 36 48 39" fill="none" stroke="#c9a227" stroke-width="2.2" stroke-linecap="round"></path>
<path d="M50 56 C62 48 82 52 88 62 C84 76 62 78 52 70 Z" fill="#3f9a45" stroke="#1b2a3a" stroke-linejoin="round" vector-effect="non-scaling-stroke"></path>
<path d="M55 60 C66 55 77 57 84 62 M55 66 C66 62 76 64 83 68 M57 72 C66 70 74 71 78 73" fill="none" stroke="#2c7a33" stroke-width="2.2" stroke-linecap="round"></path>
<path d="M24 78 L24 69 L31 63 L38 69 L38 78 Z" fill="#e8483b" stroke="#1b2a3a" stroke-linejoin="round" vector-effect="non-scaling-stroke"></path>
<rect x="29" y="71" width="4" height="7" fill="#fff6e0"></rect>
<path d="M-10 -10 H110 V110 H-10 Z M-14 40 A54 54 0 1 0 94 40 A54 54 0 1 0 -14 40 Z" fill="url(#tycoon-dots)" fill-opacity="0.45" fill-rule="evenodd"></path>
<ellipse cx="28" cy="18" rx="8" ry="4" transform="rotate(-32 28 18)" fill="#ffffff" fill-opacity="0.8"></ellipse>
</symbol>
<symbol id="tycoon-forge" viewBox="0 0 100 100">
<path d="M-5 56 C20 50 40 62 60 56 S95 50 105 54 L105 66 C85 72 60 62 40 68 S10 70 -5 66 Z" fill="#c8631a" stroke="#1b2a3a" stroke-linejoin="round" vector-effect="non-scaling-stroke"></path>
<path d="M-5 80 C20 76 40 86 60 80 S95 76 105 78" fill="none" stroke="#c8631a" stroke-width="4" stroke-linecap="round"></path>
<path d="M34 42 L34 30 L41 25 L41 30 L48 25 L48 30 L55 25 L55 42 Z" fill="#fff6e0" stroke="#1b2a3a" stroke-linejoin="round" vector-effect="non-scaling-stroke"></path>
<rect x="58" y="18" width="6" height="24" fill="#e8483b" stroke="#1b2a3a" vector-effect="non-scaling-stroke"></rect>
<circle cx="64" cy="12" r="4" fill="#fff6e0" fill-opacity="0.9"></circle>
<circle cx="71" cy="8" r="3" fill="#fff6e0" fill-opacity="0.75"></circle>
<path d="M30 42 H62" stroke="#1b2a3a" stroke-linecap="round" vector-effect="non-scaling-stroke"></path>
<path d="M-10 -10 H110 V110 H-10 Z M-14 40 A54 54 0 1 0 94 40 A54 54 0 1 0 -14 40 Z" fill="url(#tycoon-dots)" fill-opacity="0.45" fill-rule="evenodd"></path>
<ellipse cx="22" cy="24" rx="7" ry="3.5" transform="rotate(-32 22 24)" fill="#ffffff" fill-opacity="0.75"></ellipse>
</symbol>
<symbol id="tycoon-rock" viewBox="0 0 100 100">
<circle cx="30" cy="34" r="10" fill="#7a8f96" stroke="#1b2a3a" vector-effect="non-scaling-stroke"></circle>
<circle cx="66" cy="24" r="6" fill="#7a8f96" stroke="#1b2a3a" vector-effect="non-scaling-stroke"></circle>
<circle cx="58" cy="60" r="13" fill="#7a8f96" stroke="#1b2a3a" vector-effect="non-scaling-stroke"></circle>
<circle cx="24" cy="70" r="6.5" fill="#7a8f96" stroke="#1b2a3a" vector-effect="non-scaling-stroke"></circle>
<circle cx="83" cy="50" r="5" fill="#7a8f96" stroke="#1b2a3a" vector-effect="non-scaling-stroke"></circle>
<path d="M-10 -10 H110 V110 H-10 Z M-14 40 A54 54 0 1 0 94 40 A54 54 0 1 0 -14 40 Z" fill="url(#tycoon-dots)" fill-opacity="0.45" fill-rule="evenodd"></path>
<ellipse cx="30" cy="17" rx="8" ry="4" transform="rotate(-32 30 17)" fill="#ffffff" fill-opacity="0.7"></ellipse>
</symbol>
<symbol id="tycoon-burst" viewBox="0 0 48 48">
<path d="M24.0 1.0 L27.6 5.9 L32.8 2.8 L34.3 8.6 L40.3 7.7 L39.4 13.7 L45.2 15.2 L42.1 20.4 L47.0 24.0 L42.1 27.6 L45.2 32.8 L39.4 34.3 L40.3 40.3 L34.3 39.4 L32.8 45.2 L27.6 42.1 L24.0 47.0 L20.4 42.1 L15.2 45.2 L13.7 39.4 L7.7 40.3 L8.6 34.3 L2.8 32.8 L5.9 27.6 L1.0 24.0 L5.9 20.4 L2.8 15.2 L8.6 13.7 L7.7 7.7 L13.7 8.6 L15.2 2.8 L20.4 5.9 Z" stroke="#1b2a3a" stroke-width="2" stroke-linejoin="round"></path>
</symbol>
<symbol id="tycoon-spark" viewBox="-12 -12 24 24">
<path d="M0 -11 C1 -3 3 -1 11 0 C3 1 1 3 0 11 C-1 3 -3 1 -11 0 C-3 -1 -1 -3 0 -11 Z"></path>
<path d="M5.5 -5.5 L1.8 -1.2 L1.2 -1.8 Z M5.5 5.5 L1.2 1.8 L1.8 1.2 Z M-5.5 5.5 L-1.8 1.2 L-1.2 1.8 Z M-5.5 -5.5 L-1.2 -1.8 L-1.8 -1.2 Z"></path>
</symbol>
<symbol id="tycoon-atom" viewBox="-12 -12 24 24">
<ellipse cx="0" cy="0" rx="11" ry="4" fill="none"></ellipse>
<ellipse cx="0" cy="0" rx="11" ry="4" fill="none" transform="rotate(60)"></ellipse>
<ellipse cx="0" cy="0" rx="11" ry="4" fill="none" transform="rotate(120)"></ellipse>
<circle cx="0" cy="0" r="1.8" stroke="none"></circle>
</symbol>
<symbol id="tycoon-rocket" viewBox="0 0 40 20">
<path d="M7 7.5 L0 10 L7 12.5 Z" fill="#f4b531" stroke="#1b2a3a" stroke-width="1.2" stroke-linejoin="round"></path>
<path d="M15 6 L6 0.8 L8.5 6 Z" fill="#e8483b" stroke="#1b2a3a" stroke-width="1.4" stroke-linejoin="round"></path>
<path d="M15 14 L6 19.2 L8.5 14 Z" fill="#e8483b" stroke="#1b2a3a" stroke-width="1.4" stroke-linejoin="round"></path>
<path d="M8 6 H27 C33 6 37 8 39.5 10 C37 12 33 14 27 14 H8 C6.5 14 6 13 6 12 V8 C6 7 6.5 6 8 6 Z" fill="#fff6e0" stroke="#1b2a3a" stroke-width="1.6" stroke-linejoin="round"></path>
<path d="M29 6.2 C34 6.8 37.5 8.4 39.5 10 C37.5 11.6 34 13.2 29 13.8 Z" fill="#e8483b" stroke="#1b2a3a" stroke-width="1.4" stroke-linejoin="round"></path>
<rect x="10" y="6.8" width="6" height="6.4" stroke="#1b2a3a" stroke-width="1.2"></rect>
<circle cx="22.5" cy="10" r="2.6" fill="#5ec4e8" stroke="#1b2a3a" stroke-width="1.3"></circle>
</symbol>`,
});
})();
