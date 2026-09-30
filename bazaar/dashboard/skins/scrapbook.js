// Sticker scrapbook: cream cards taped onto a starry purple page, planets as
// round stickers, and a health ring round our own.
//
// The shapes only this skin draws.  Colours and type are in scrapbook.css.
(() => {
"use strict";

const STAR = (x, y, size, colour, extra = "") =>
  `<use href="#scrapbook-star" x="${x}" y="${y}" width="${size}" height="${size}" fill="${colour}" stroke="${colour}"${extra}></use>`;

const STARS = `
<svg class="stagestars" width="720" height="600" viewBox="0 0 720 600">
  <g class="tw">${STAR(26, 44, 22, "#ffd35a", ' transform="rotate(-12 40 60)"')}</g>
  ${STAR(556, 34, 16, "#ff8ab2")}
  <g class="tw">${STAR(672, 452, 18, "#9fd4ff")}</g>
  ${STAR(26, 466, 15, "#fdf3e1")}
  <g class="tw">${STAR(118, 540, 16, "#ff8ab2")}</g>
  ${STAR(160, 270, 13, "#ffd35a")}
  <circle cx="156" cy="136" r="2.6" fill="#fdf3e1" fill-opacity="0.5"></circle>
  <circle cx="676" cy="108" r="2.2" fill="#fdf3e1" fill-opacity="0.55"></circle>
  <circle cx="590" cy="546" r="2.6" fill="#fdf3e1" fill-opacity="0.4"></circle>
  <circle cx="540" cy="296" r="2.2" fill="#fdf3e1" fill-opacity="0.5"></circle>
</svg>`;

const ORBITS = `
<svg width="720" height="600" viewBox="0 0 720 600">
  <ellipse cx="360" cy="300" rx="346" ry="276" fill="none" stroke="rgba(253,243,225,0.07)"></ellipse>
  <ellipse cx="360" cy="300" rx="300" ry="236" fill="none" stroke="rgba(253,243,225,0.25)" stroke-dasharray="2 7" stroke-linecap="round"></ellipse>
  <ellipse cx="360" cy="300" rx="214" ry="168" fill="none" stroke="rgba(253,243,225,0.06)"></ellipse>
</svg>`;

// A ringed pink planet.
function logoMark() {
  return s("svg", { width: 42, height: 34, viewBox: "0 0 46 38", "aria-hidden": "true", style: "transform: rotate(-8deg)" },
    s("g", { transform: "translate(23 20) rotate(-16)" },
      s("ellipse", { cx: 0, cy: 0, rx: 20, ry: 6, fill: "none", stroke: "#fffaf0", "stroke-width": 7 }),
      s("ellipse", { cx: 0, cy: 0, rx: 20, ry: 6, fill: "none", stroke: "#9fd4ff", "stroke-width": 3 })),
    s("circle", { cx: 23, cy: 19, r: 13, fill: "#ff8ab2", stroke: "#fffaf0", "stroke-width": 3.5 }),
    s("path", { d: "M12 16 C17 14 27 18 34 15", fill: "none", stroke: "#e0578c", "stroke-width": 2.2, "stroke-linecap": "round" }),
    s("path", { d: "M13 23 C18 21 26 25 32 22", fill: "none", stroke: "#ffc2d8", "stroke-width": 2.2, "stroke-linecap": "round" }),
    s("g", { transform: "translate(23 20) rotate(-16)" },
      s("path", { d: "M-20 0 A20 6 0 0 0 20 0", fill: "none", stroke: "#fffaf0", "stroke-width": 7, "stroke-linecap": "round" }),
      s("path", { d: "M-20 0 A20 6 0 0 0 20 0", fill: "none", stroke: "#9fd4ff", "stroke-width": 3, "stroke-linecap": "round" })));
}
// The chequered flag at the end of the run.
function flagMark() {
  return s("svg", { width: 16, height: 23, viewBox: "0 0 18 26", "aria-hidden": "true" },
    s("path", { d: "M3 25 L3 3", stroke: "#fdf3e1", "stroke-width": 2.2, "stroke-linecap": "round" }),
    s("rect", { x: 3, y: 3, width: 13, height: 10, fill: "#fdf3e1" }),
    s("rect", { x: 3, y: 3, width: 4.3, height: 5, fill: "#2a2446" }),
    s("rect", { x: 11.6, y: 3, width: 4.4, height: 5, fill: "#2a2446" }),
    s("rect", { x: 7.3, y: 8, width: 4.3, height: 5, fill: "#2a2446" }));
}
function heartIcon(size = 16) {
  return s("svg", { width: size, height: size, viewBox: "0 0 24 24", "aria-hidden": "true" },
    s("path", { d: "M12 21 C5 15 2 11.5 2 8 C2 5 4.5 3 7.2 3 C9.2 3 10.8 4.2 12 6 C13.2 4.2 14.8 3 16.8 3 C19.5 3 22 5 22 8 C22 11.5 19 15 12 21 Z", fill: "#e0485f" }));
}
function verdictIcon(tone) {
  if (tone === "warning") return s("svg", { width: 18, height: 18, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linejoin": "round", "stroke-linecap": "round", "aria-hidden": "true" },
    s("path", { d: "M8 2 L14.5 13.5 L1.5 13.5 Z" }), s("path", { d: "M8 6.5 L8 9.4 M8 11.6 L8 11.7" }));
  if (tone === "critical") return s("svg", { width: 18, height: 18, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", "stroke-width": 2.2, "stroke-linecap": "round", "aria-hidden": "true" },
    s("path", { d: "M4.5 4.5 L11.5 11.5 M11.5 4.5 L4.5 11.5" }));
  if (tone === "good") return s("svg", { width: 18, height: 18, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" },
    s("path", { d: "M2.5 8.5 L6.2 12 L13.5 4" }));
  return s("svg", { width: 18, height: 18, viewBox: "0 0 16 16", fill: "none", stroke: "currentColor", "stroke-width": 1.8, "aria-hidden": "true" }, s("circle", { cx: 8, cy: 8, r: 5.5 }));
}
// A friendly robot.
function mascotIcon() {
  return s("svg", { width: 44, height: 44, viewBox: "0 0 48 48", "aria-hidden": "true", style: "transform: rotate(-6deg); filter: drop-shadow(0 2px 2px rgba(42,36,70,0.35))" },
    s("circle", { cx: 24, cy: 24, r: 23, fill: "#fffaf0" }),
    s("circle", { cx: 24, cy: 24, r: 19.5, fill: "#ffd9e6" }),
    s("path", { d: "M24 14 L24 8.5", stroke: "#2a2446", "stroke-width": 2, "stroke-linecap": "round" }),
    s("circle", { cx: 24, cy: 7.5, r: 3, fill: "#ffc83a", stroke: "#2a2446", "stroke-width": 1.5 }),
    s("rect", { x: 10, y: 21, width: 4, height: 9, rx: 2, fill: "#9fd4ff", stroke: "#2a2446", "stroke-width": 1.5 }),
    s("rect", { x: 34, y: 21, width: 4, height: 9, rx: 2, fill: "#9fd4ff", stroke: "#2a2446", "stroke-width": 1.5 }),
    s("rect", { x: 13, y: 14, width: 22, height: 21, rx: 8, fill: "#bfe3ff", stroke: "#2a2446", "stroke-width": 1.8 }),
    s("circle", { cx: 19.5, cy: 23.5, r: 3, fill: "#2a2446" }),
    s("circle", { cx: 28.5, cy: 23.5, r: 3, fill: "#2a2446" }),
    s("circle", { cx: 20.5, cy: 22.5, r: 1, fill: "#ffffff" }),
    s("circle", { cx: 29.5, cy: 22.5, r: 1, fill: "#ffffff" }),
    s("path", { d: "M20.5 29.5 Q24 32 27.5 29.5", fill: "none", stroke: "#2a2446", "stroke-width": 1.8, "stroke-linecap": "round" }));
}

// Our planet: a halo in its own colour, a ring that fills with health, a pin
// saying where we are, and a heart chip with the number.
function stage() {
  const RING_R = 124, RING = 2 * Math.PI * RING_R;
  const halo = h("div", { class: "home-halo" });
  const alarm = h("div", { class: "home-alarm", hidden: true });
  const sel = h("div", { class: "home-sel", hidden: true });
  const arc = s("circle", { class: "arc good", cx: 140, cy: 140, r: RING_R, fill: "none", "stroke-width": 7, "stroke-linecap": "round",
                            "stroke-dasharray": `0 ${f1(RING)}`, transform: "rotate(-90 140 140)" });
  const ring = s("svg", { class: "home-ring", viewBox: "0 0 280 280" },
    s("circle", { cx: 140, cy: 140, r: 134, fill: "none", stroke: "rgba(253,243,225,0.35)", "stroke-dasharray": "1 9.5" }),
    s("circle", { cx: 140, cy: 140, r: RING_R, fill: "none", stroke: "rgba(253,243,225,0.14)", "stroke-width": 7 }),
    arc);
  const tagName = h("span", { class: "n" });
  const chip = h("div", { class: "home-chip card scaled-text good" });
  const name = h("span", { class: "n" }), world = h("span", { class: "w" });
  return {
    back: [markup(STARS), markup(ORBITS)],
    mid: [halo, alarm, sel, ring],
    front: [
      h("div", { class: "home-tag card scaled-text" }, h("span", { class: "pin", "aria-hidden": "true" }), h("span", { class: "k" }, "YOU ARE HERE"), tagName),
      chip,
      h("div", { class: "home-label scaled-text" }, name, world),
    ],
    update({ hd, hl, pct, tone, kind, alarm: alarming, selected: chosen }) {
      halo.className = `home-halo ${kind}`;
      alarm.hidden = !alarming;
      sel.hidden = !chosen;
      arc.setAttribute("class", `arc ${tone}`);
      arc.setAttribute("stroke-dasharray", `${f1(RING * pct)} ${f1(RING)}`);
      chip.className = `home-chip card scaled-text ${tone}`;
      chip.replaceChildren(heartIcon(18), h("span", { class: "v" }, fmt(hl.health)), h("span", { class: "of" }, `/ ${fmt(hl.max_health)}`));
      tagName.textContent = `${hd.station} · us`;
      name.textContent = `${hd.station} · us`;
      world.textContent = WORLD[hd.specialty] || hd.specialty;
    },
  };
}

registerSkin({
  id: "scrapbook",
  label: "Scrapbook",
  title: "Sticker scrapbook",
  rocket: [34, 20],
  geometry: { laneR: 136, planet: { far: 44, near: 52 }, ship: 26 },
  wordmark: () => [logoMark(), h("span", { class: "wordmark" }, "Bazaar")],
  tick: (hd) => h("span", { class: "nowrap" }, "Tick ", h("b", { class: "num tick-n" }, fmt(hd.tick)), ` of ${fmt(hd.duration_ticks)}`),
  track: (pct) => [
    h("span", { class: "fill", style: `width:${pct}%` }),
    h("span", { class: "marker", style: `left:${pct}%` }, shipIcon("water", 30)),
    h("span", { class: "flag" }, flagMark()),
  ],
  decor: () => h("span", { class: "tape", "aria-hidden": "true" }),
  verdictMark: (tone) => h("span", { class: "mark", "aria-hidden": "true" }, verdictIcon(tone)),
  mascot: mascotIcon,
  stage,
  symbols: `
<symbol id="scrapbook-ocean" viewBox="0 0 100 100">
<path d="M8 16 C22 8 36 12 46 7" fill="none" stroke="#8fcbff" stroke-linecap="round" vector-effect="non-scaling-stroke"></path>
<path d="M16 30 C22 17 42 15 50 23 C58 31 52 42 42 44 C32 46 30 57 21 55 C11 53 11 40 16 30 Z" fill="#a8daff" stroke="#2f7fcf" stroke-linejoin="round" vector-effect="non-scaling-stroke"></path>
<path d="M23 33 C27 25 39 23 43 29 C46 34 39 37 33 38 C27 39 21 41 23 33 Z" fill="none" stroke="#6fb4f0" vector-effect="non-scaling-stroke"></path>
<path d="M57 58 C65 49 82 53 87 63 C91 73 81 85 70 82 C61 80 66 72 57 70 C51 68 51 62 57 58 Z" fill="#a8daff" stroke="#2f7fcf" stroke-linejoin="round" vector-effect="non-scaling-stroke"></path>
<path d="M63 62 C69 58 78 61 80 67 C82 73 76 77 70 75 C66 74 68 69 63 67 Z" fill="none" stroke="#6fb4f0" vector-effect="non-scaling-stroke"></path>
<ellipse cx="32" cy="77" rx="8" ry="4.5" fill="#a8daff" stroke="#2f7fcf" vector-effect="non-scaling-stroke"></ellipse>
<path d="M68 16 C76 19 82 25 85 33" fill="none" stroke="#8fcbff" stroke-linecap="round" vector-effect="non-scaling-stroke"></path>
<path d="M-10 -10 H110 V110 H-10 Z M-14 40 A54 54 0 1 0 94 40 A54 54 0 1 0 -14 40 Z" fill="#1d3f7a" fill-opacity="0.2" fill-rule="evenodd"></path>
<ellipse cx="30" cy="21" rx="10" ry="5" transform="rotate(-32 30 21)" fill="#ffffff" fill-opacity="0.8"></ellipse>
</symbol>
<symbol id="scrapbook-farm" viewBox="0 0 100 100">
<path d="M-5 58 C14 50 30 66 50 58 S86 48 105 56" fill="none" stroke="#2f9e5e" stroke-linecap="round" vector-effect="non-scaling-stroke"></path>
<path d="M20 24 C26 17 37 19 38 27 C39 35 29 38 24 34 C19 31 16 28 20 24 Z" fill="#cbf6d8" stroke="#3a9e63" vector-effect="non-scaling-stroke"></path>
<path d="M55 28 C60 23 69 26 69 33 C69 40 60 42 57 38 C54 35 52 31 55 28 Z" fill="#cbf6d8" stroke="#3a9e63" vector-effect="non-scaling-stroke"></path>
<path d="M38 42 C42 39 49 41 49 46 C49 51 43 52 40 49 C37 47 36 44 38 42 Z" fill="#cbf6d8" stroke="#3a9e63" vector-effect="non-scaling-stroke"></path>
<path d="M26 76 C31 71 40 72 41 78 C42 84 34 87 30 84 C26 82 23 79 26 76 Z" fill="#cbf6d8" stroke="#3a9e63" vector-effect="non-scaling-stroke"></path>
<path d="M64 74 C69 69 78 71 78 77 C78 83 70 85 66 81 C63 79 62 76 64 74 Z" fill="#cbf6d8" stroke="#3a9e63" vector-effect="non-scaling-stroke"></path>
<circle cx="80" cy="44" r="4.5" fill="#cbf6d8" stroke="#3a9e63" vector-effect="non-scaling-stroke"></circle>
<path d="M-10 -10 H110 V110 H-10 Z M-14 40 A54 54 0 1 0 94 40 A54 54 0 1 0 -14 40 Z" fill="#1d5a3a" fill-opacity="0.2" fill-rule="evenodd"></path>
<ellipse cx="30" cy="21" rx="10" ry="5" transform="rotate(-32 30 21)" fill="#ffffff" fill-opacity="0.8"></ellipse>
</symbol>
<symbol id="scrapbook-forge" viewBox="0 0 100 100">
<path d="M-5 28 C15 22 35 34 55 28 S90 22 105 28 L105 40 C85 46 70 36 50 42 S15 46 -5 40 Z" fill="#f07c2e" stroke="#c95a1a" stroke-linejoin="round" vector-effect="non-scaling-stroke"></path>
<path d="M-5 62 C20 56 35 68 55 62 S88 56 105 62 L105 72 C85 78 65 68 45 74 S10 76 -5 72 Z" fill="#f07c2e" stroke="#c95a1a" stroke-linejoin="round" vector-effect="non-scaling-stroke"></path>
<path d="M10 16 C24 12 40 18 56 14" fill="none" stroke="#ffe3c0" stroke-linecap="round" vector-effect="non-scaling-stroke"></path>
<path d="M28 51 C42 47 60 53 80 49" fill="none" stroke="#ffe3c0" stroke-linecap="round" vector-effect="non-scaling-stroke"></path>
<path d="M18 86 C32 82 48 88 66 84" fill="none" stroke="#ffe3c0" stroke-linecap="round" vector-effect="non-scaling-stroke"></path>
<path d="M-10 -10 H110 V110 H-10 Z M-14 40 A54 54 0 1 0 94 40 A54 54 0 1 0 -14 40 Z" fill="#7a2e0a" fill-opacity="0.18" fill-rule="evenodd"></path>
<ellipse cx="30" cy="21" rx="10" ry="5" transform="rotate(-32 30 21)" fill="#ffffff" fill-opacity="0.75"></ellipse>
</symbol>
<symbol id="scrapbook-rock" viewBox="0 0 100 100">
<circle cx="30" cy="34" r="10" fill="#8f84c4" stroke="#6d62a3" vector-effect="non-scaling-stroke"></circle>
<circle cx="66" cy="24" r="6" fill="#8f84c4" stroke="#6d62a3" vector-effect="non-scaling-stroke"></circle>
<circle cx="58" cy="60" r="13" fill="#8f84c4" stroke="#6d62a3" vector-effect="non-scaling-stroke"></circle>
<circle cx="24" cy="70" r="6.5" fill="#8f84c4" stroke="#6d62a3" vector-effect="non-scaling-stroke"></circle>
<circle cx="83" cy="50" r="5" fill="#8f84c4" stroke="#6d62a3" vector-effect="non-scaling-stroke"></circle>
<circle cx="42" cy="86" r="5" fill="#8f84c4" stroke="#6d62a3" vector-effect="non-scaling-stroke"></circle>
<path d="M-10 -10 H110 V110 H-10 Z M-14 40 A54 54 0 1 0 94 40 A54 54 0 1 0 -14 40 Z" fill="#2a2446" fill-opacity="0.18" fill-rule="evenodd"></path>
<ellipse cx="30" cy="17" rx="8" ry="4" transform="rotate(-32 30 17)" fill="#ffffff" fill-opacity="0.7"></ellipse>
</symbol>
<symbol id="scrapbook-star" viewBox="-12 -12 24 24">
<path d="M0 -10 L3 -3.4 L10 -3.1 L4.6 1.7 L6.4 8.8 L0 4.8 L-6.4 8.8 L-4.6 1.7 L-10 -3.1 L-3 -3.4 Z" stroke-width="2.2" stroke-linejoin="round"></path>
</symbol>
<symbol id="scrapbook-rocket" viewBox="0 0 34 20">
<path d="M5.5 10 L0 5.5 L1.8 10 L0 14.5 Z" fill="#ffc83a"></path>
<path d="M9 5 L4.5 1 L4.5 7 Z" fill="#ff7aa5"></path>
<path d="M9 15 L4.5 19 L4.5 13 Z" fill="#ff7aa5"></path>
<path d="M6 4.5 H21 C27 4.5 31 7.5 33 10 C31 12.5 27 15.5 21 15.5 H6 C5 15.5 4.5 15 4.5 14 V6 C4.5 5 5 4.5 6 4.5 Z" fill="#fdf3e1" stroke="#2a2446" stroke-width="1.3" stroke-linejoin="round"></path>
<rect x="8.5" y="7" width="8" height="6" rx="1.8"></rect>
<circle cx="23.5" cy="10" r="2.6" fill="#9fd4ff" stroke="#2a2446" stroke-width="1.1"></circle>
</symbol>`,
});
})();
