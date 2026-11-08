(async () => {
  const MODE = "__MODE__";
  const STATUS = [
    { key: "both", label: "Conforms to both", short: "both", chip: "ok", color: "--s-both" },
    { key: "pos", label: "⌖ only", short: "⌖ only", chip: "mid", color: "--s-pos" },
    { key: "prof", label: "⌒ only", short: "⌒ only", chip: "mid", color: "--s-prof" },
    { key: "apart", label: "Each alone, not together", short: "apart", chip: "amber", color: "--s-apart" },
    { key: "none", label: "Neither", short: "neither", chip: "bad", color: "--s-none" },
    { key: "few", label: "Too few runs (< 3)", short: "few", chip: "mid", color: "--s-few" },
  ];
  const SYM_POS = '<svg viewBox="0 0 16 16" aria-label="position"><circle cx="8" cy="8" r="4.5"/><path d="M8 1v14M1 8h14"/></svg>';
  const SYM_PROF = '<svg viewBox="0 0 16 16" aria-label="profile of a line"><path d="M2 12a6 6 0 0 1 12 0"/></svg>';
  // ⌖ is located from datum A (the cafe). ⌒ is form only: the band follows the street, and R just picks the stretch inspected.
  const datumCell = sym => sym === SYM_PROF ? "" : "<span>A</span>";
  const $ = id => document.getElementById(id);
  const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  // Status reads by shape as well as colour: ● both, ◐ ⌖ only, ◑ ⌒ only, ◎ apart, ○ neither, · few.
  // The same shapes are drawn on the map pins (Pin below).
  const ring = (c, w = 1.4) => `<circle cx="6" cy="6" r="4.4" style="fill:none;stroke:${c};stroke-width:${w}"/>`;
  const SHAPES = [
    c => `<circle cx="6" cy="6" r="5" style="fill:${c}"/>`,
    c => ring(c) + `<path d="M6 1.6A4.4 4.4 0 0 0 6 10.4Z" style="fill:${c}"/>`,
    c => ring(c) + `<path d="M6 1.6A4.4 4.4 0 0 1 6 10.4Z" style="fill:${c}"/>`,
    c => ring(c) + `<circle cx="6" cy="6" r="1.8" style="fill:${c}"/>`,
    c => ring(c, 1.8),
    c => `<circle cx="6" cy="6" r="2" style="fill:${c}"/>`,
  ];
  const sym = s => `<svg class="sym" viewBox="0 0 12 12" aria-hidden="true">${SHAPES[s](`var(${STATUS[s].color})`)}</svg>`;
  const mark = b => b ? `<span class="yes">✓</span>` : `<span class="no">✗</span>`;
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  // a table head; each column is a label or [label, tip]
  const thead = cols => "<thead><tr>" + cols.map(c => Array.isArray(c) ? `<th data-tip="${c[1]}">${c[0]}</th>` : `<th>${c}</th>`).join("") + "</tr></thead>";
  // metres per degree of longitude at a latitude, and of latitude: the engine's equirectangular projection
  const kx = lat => 111320 * Math.cos(lat * Math.PI / 180), KY = 110540;

  // ---------- the sheet: the border's zone marks, the sliders and the callout symbols are drawn from data ----------
  document.querySelectorAll(".zones").forEach(z => {
    z.innerHTML = [...(z.classList.contains("h") ? "87654321" : "FEDCBA")].map(c => `<span>${c}</span>`).join("");
  });
  document.querySelectorAll("[data-sym]").forEach(s => { s.innerHTML = s.dataset.sym === "pos" ? SYM_POS : SYM_PROF; });
  // each slider's key in state, its range and default; the tips (TIPS) share its id
  const SLIDERS = [
    { id: "r", key: "R", label: "Search radius R", min: 30, max: 150, step: 5, value: 75, fmt: v => v + " m", recut: true },
    { id: "tol", key: "tol", label: "Position zone Ø", min: 10, max: 200, step: 5, value: 50, fmt: v => "Ø" + v + " m" },
    { id: "band", key: "band", label: "Profile band", min: 10, max: 60, step: 1, value: 30, fmt: v => v + " m" },
    { id: "acc", key: "acc", label: "Acceptance yield", min: 50, max: 100, step: 5, value: 75, fmt: v => v + "%" },
  ];
  $("controls").innerHTML = SLIDERS.map(s => `<div class="ctl"><label for="${s.id}" data-tip="${s.id}">${s.label}</label><output id="${s.id}-out" for="${s.id}"></output>
    <input type="range" data-tip="${s.id}" id="${s.id}" min="${s.min}" max="${s.max}" step="${s.step}" value="${s.value}"></div>`).join("");

  $("foot-mode").textContent = MODE === "public"
    ? "Public build: the first and last 500 m of every run are removed and dates kept to month and year before the data is compiled in."
    : "Private build: contains full GPS tracks. Do not publish this file.";

  // ---------- tooltips for every parameter and readout ----------
  const TIPS = {
    "r": ["Search radius R", "How far from a cafe a run still counts as passing it. Each run's stretch inside this circle is a pass. Below 30 m R is narrower than many cafes' own street (a cafe sits 11–50 m from its centreline). Past 150 m it reaches the next parallel street for 9 in 10 cafes, so most of what it adds are runs on other streets.", "Default 75 m · range 30–150 m"],
    "tol": ["Position zone Ø (⌖ true position)", "A circular tolerance zone centred on the cafe (datum A). A run's Ø is twice its closest approach to the cafe, and the run conforms when that Ø fits inside the zone.", "Default Ø50 m (within 25 m) · range Ø10–200 m, at most 2R (at Ø = 2R every run within R would conform)", figPos],
    "band": ["Profile band (⌒ profile of a line)", "The total width of a strip centred on the City street centreline. Every GPS point of a pass must stay inside it (±half the band). A run's actual is 2 × its largest sideways offset on its worst street.", "Default 30 m (±15 m) · range 10–60 m", figPro],
    "acc": ["Acceptance yield", "The share of a cafe's runs that must be accepted (meeting ⌖ and ⌒ at once) for the cafe to conform to both. A cafe needs at least 3 runs.", "Default 75% · range 50–100%"],
    "years": ["Years", "Include runs from these years only. Click a year to turn it off or on; at least one stays on. Tolerances are re-checked straight away."],
    "show-all": ["All cafes", "Show every cafe, with pins coloured by status."],
    "show-both": ["Conform to both", "Show only the cafes whose runs meet both callouts at the acceptance yield. Their names are labelled when there are 40 or fewer."],
    "annot": ["GD&T annotations", "Draw callouts for the selected cafe on the map: the datum A, a ⌖ frame on the position zone, and a band-width dimension with a ⌒ frame for each of the main streets."],
    "search": ["Find a cafe", "Filter the table by cafe name. Click a row to select the cafe and zoom to it."],
    "count": ["Conforming cafes", "How many cafes conform to both callouts at the current settings, out of every cafe within reach of a run."],
    "col-runs": ["Runs", "Runs that came within R of the cafe. A run counts once, even if it passed on two streets."],
    "col-pos": ["⌖ yield", "Share of runs whose closest approach gives Ø within the position zone. Green when it reaches the acceptance yield."],
    "col-pro": ["⌒ yield", "Share of runs whose worst pass stays inside the profile band. Green when it reaches the acceptance yield."],
    "col-both": ["Combined yield", "Share of runs accepted, meaning they meet ⌖ and ⌒ at once. This decides whether the cafe conforms to both."],
    "col-status": ["Status", "Conforms to both, ⌖ only, ⌒ only, apart (each callout passes alone but on different runs), neither, or few (fewer than 3 runs). A yellow ① marks a cafe with a checker's note."],
    "notes": ["Checker's notes", "Written only when a cafe's runs show something worth pointing out: a run right at the door, a favoured side of the street, callouts that fail on different runs, a p95 just over the band, one or two detours, or every run accepted. At most three per cafe, and they change with the settings."],
    "frame-pos": ["⌖ | Ø | A: true position", "Read: each run must pass through a circle of this diameter centred on datum A, the cafe. The actual shows the best and median Ø and how many runs conform."],
    "frame-pro": ["⌒ | band: profile of a line", "Read: every GPS point must stay inside a strip of this width centred on the street. There is no datum: the band follows the street, and the cafe's R circle only picks which stretch is inspected. The actual shows the 95th-percentile run and how many runs conform."],
    "frame-both": ["Both callouts", "Runs accepted because they meet both callouts at once. This combined yield decides the cafe's status."],
    "st-street": ["Street (nominal)", "The City centreline the passes are measured against: the drawing's nominal line."],
    "st-passes": ["Passes", "Stretches of runs on this street inside R. A run that went by twice counts twice here."],
    "st-pos": ["⌖ passes", "Passes whose closest approach to the cafe is inside the position zone."],
    "st-pro": ["⌒ passes", "Passes whose every point stays inside the profile band."],
    "st-both": ["Both", "Passes meeting both callouts."],
    "st-p95": ["⌒ p95", "95th-percentile profile actual on this street, in metres: 95% of passes stay within this band width."],
    "st-bias": ["Bias", "Average sideways offset from the centreline. N/S for east-west streets, W/E for north-south ones. A sidewalk run sits a few metres off by design."],
    "view-cafe": ["Cafe view (datum)", "Zoomed on one cafe: its search circle, band and zone, every run as the purple glow, every cafe as a status symbol. Selecting any cafe shows this view."],
    "view-run": ["Run view (part)", "The whole run in orange, everything else dimmed. The cafes it passed within R are numbered in route order (filled = accepted, hollow = rejected); every other cafe is a faint dot. Selecting any run shows this view."],
    "runs-table": ["Runs at this cafe", "One row per run (part) that came within R: its Ø (twice the closest approach), its ⌒ actual (twice its largest offset), the two verdicts and the result. Click a row to inspect that run."],
    "run-strip": ["Route strip", "The run as one line from start to finish. Each mark is a cafe, placed where along the run it was passed: filled = accepted, hollow = rejected. Click one to select that cafe."],
    "run-profile": ["This run at the cafe", "The run's sideways offset from the centreline along the street inside R, against the profile band. Its ⌒ actual is twice the largest offset."],
    "lot": ["The lot", "In inspection, the lot is every part the process made. Here it is every run in the selected years, scored at every cafe with the current settings. It follows the sliders and the year chips."],
    "lot-runs": ["Runs", "Runs inside the City in the selected years, whether or not they passed a cafe."],
    "lot-km": ["Distance", "Total distance of those runs, inside the City only."],
    "lot-cafes": ["Cafes per run", "How many cafes the median run passed within R, and the run that passed the most. Its date opens it in the run view."],
    "lot-acc": ["Accepted at a cafe", "Of every run–cafe pair within R, the share where the run met both callouts (⌖ and ⌒) at that cafe."],
    "hist": ["Ø of each run", "Distribution of the runs' closest approach as a diameter. Green bars are inside the position zone (dashed red line), red bars are outside."],
    "status-0": ["Conforms to both", "At least the acceptance yield of runs meet both callouts at once."],
    "status-1": ["⌖ only", "Enough runs come close enough, but too many stray from the street."],
    "status-2": ["⌒ only", "Runs follow the street tightly, but too few come close enough to the cafe."],
    "status-3": ["Each alone, not together", "Each callout passes on its own, but they fail on different runs, so too few runs pass both."],
    "status-4": ["Neither", "Neither callout reaches the acceptance yield."],
    "status-5": ["Too few runs", "Fewer than 3 runs within R: not enough to judge."],
  };
  // infographics for the two callouts: one conforming run and one failing run, scaled to the current slider value
  const fmt1 = v => (Math.round(v * 10) / 10).toString();
  function figFrame(x, y, cells) {  // a mini feature control frame
    let s = "";
    for (const c of cells) {
      const w = 12 + [...c].length * 6.6;
      s += `<rect class="fr" x="${x}" y="${y}" width="${w}" height="18"/><text x="${x + w / 2}" y="${y + 13}" text-anchor="middle">${c}</text>`;
      x += w;
    }
    return s;
  }
  const figVerdict = (y, cls, run, text) =>
    `<line class="${cls}" x1="168" x2="184" y1="${y - 4}" y2="${y - 4}"/><text x="190" y="${y}">${run}</text>` +
    `<text class="${cls}t" x="168" y="${y + 15}">${text}</text>`;
  function figPos() {
    const tol = state.tol, cx = 62, cy = 62, rz = 36, d1 = rz * 0.56, d2 = rz * 1.25;
    const a1 = Math.round(tol * 0.56), a2 = Math.round(tol * 1.25);  // whole metres, as in the ⌖ frame's actual
    const lx = cx + rz * 0.71, ly = cy - rz * 0.71;
    return `<svg class="fig" viewBox="0 0 300 128" aria-hidden="true">` +
      `<circle class="zone" cx="${cx}" cy="${cy}" r="${rz}"/>` +
      `<circle class="ok thin" cx="${cx}" cy="${cy}" r="${d1}"/><circle class="bad thin" cx="${cx}" cy="${cy}" r="${d2}"/>` +
      `<line class="ok" x1="4" x2="150" y1="${cy - d1}" y2="${cy - d1}"/><line class="bad" x1="4" x2="150" y1="${cy + d2}" y2="${cy + d2}"/>` +
      `<line class="dim" stroke-dasharray="2 2" x1="${cx}" y1="${cy}" x2="${cx}" y2="${cy - d1}"/><text x="${cx - 9}" y="${cy - d1 / 2 + 4}">d</text>` +
      `<circle class="dot" cx="${cx}" cy="${cy - d1}" r="2"/><circle class="dot" cx="${cx}" cy="${cy}" r="3"/>` +
      `<rect class="fr" x="${cx + 6}" y="${cy - 1}" width="13" height="14"/><text x="${cx + 12.5}" y="${cy + 10}" text-anchor="middle">A</text>` +
      `<path class="dim" d="M${lx},${ly} L${lx + 12},${ly - 16} H${lx + 18}"/><text class="zt" x="${lx + 21}" y="${ly - 12}">zone Ø${tol}</text>` +
      figFrame(168, 4, ["⌖", "Ø" + tol, "A"]) +
      figVerdict(42, "ok", "run 1", `Ø${a1} ≤ Ø${tol} ✓`) +
      figVerdict(78, "bad", "run 2", `Ø${a2} > Ø${tol} ✗`) +
      `<text x="168" y="110" opacity=".8">Ø = 2 × d</text><text x="168" y="124" opacity=".8">d: closest to A</text></svg>`;
  }
  function figPro() {
    const band = state.band, y0 = 52, hb = 22, x0 = 20, x1 = 156, k = band / (2 * hb);
    const off1 = x => hb * (-0.18 + 0.3 * Math.sin(x / 15) + 0.12 * Math.sin(x / 6.5));
    const off2 = x => hb * (0.15 + 0.3 * Math.sin(x / 21 + 1) + 1.3 * Math.exp(-(((x - 112) / 13) ** 2)));
    const trace = f => {
      const pts = []; let m = { v: -1 };
      for (let x = x0; x <= x1; x += 2) {
        const o = f(x); pts.push(`${x},${(y0 + o).toFixed(1)}`);
        if (Math.abs(o) > m.v) m = { v: Math.abs(o), x, o };
      }
      return { pts: pts.join(" "), m };
    };
    const t1 = trace(off1), t2 = trace(off2), a1 = fmt1(2 * t1.m.v * k), a2 = fmt1(2 * t2.m.v * k), my = y0 + t2.m.o;
    return `<svg class="fig" viewBox="0 0 300 128" aria-hidden="true">` +
      `<rect class="zfill" x="${x0 - 6}" y="${y0 - hb}" width="${x1 - x0 + 12}" height="${2 * hb}"/>` +
      `<line class="zedge" x1="${x0 - 6}" x2="${x1 + 6}" y1="${y0 - hb}" y2="${y0 - hb}"/><line class="zedge" x1="${x0 - 6}" x2="${x1 + 6}" y1="${y0 + hb}" y2="${y0 + hb}"/>` +
      `<line class="nom" x1="${x0 - 6}" x2="${x1 + 6}" y1="${y0}" y2="${y0}"/>` +
      `<path class="dim" d="M4,${y0 - hb} H11 M4,${y0 + hb} H11 M7.5,${y0 - hb} V${y0 + hb}"/>` +
      `<text class="zt" x="4" y="${y0 - hb - 7}">band ${band} m (±${fmt1(band / 2)})</text>` +
      `<polyline class="ok" points="${t1.pts}"/><polyline class="bad" points="${t2.pts}"/>` +
      `<line class="dim" stroke-dasharray="2 2" x1="${t2.m.x}" y1="${y0}" x2="${t2.m.x}" y2="${my}"/>` +
      `<circle class="bdot" cx="${t2.m.x}" cy="${my}" r="3"/><text x="${t2.m.x + 7}" y="${my + 4}">max</text>` +
      `<line class="nom" x1="4" x2="30" y1="${y0 + hb + 44}" y2="${y0 + hb + 44}"/><text x="36" y="${y0 + hb + 48}" opacity=".8">street centreline</text>` +
      figFrame(168, 4, ["⌒", String(band)]) +  // form only: no datum
      figVerdict(42, "ok", "run 1", `${a1} ≤ ${band} ✓`) +
      figVerdict(78, "bad", "run 2", `${a2} > ${band} ✗`) +
      `<text x="168" y="110" opacity=".8">2 × max |offset|</text><text x="168" y="124" opacity=".8">on the worst pass</text></svg>`;
  }
  const tipEl = document.createElement("div");
  tipEl.id = "tip"; tipEl.setAttribute("role", "tooltip"); tipEl.hidden = true;
  document.body.append(tipEl);
  let tipFor = null;
  function showTip(el) {
    const t = TIPS[el.dataset.tip];
    if (!t) return;
    const b = document.createElement("b"); b.textContent = t[0];
    tipEl.replaceChildren(b, document.createTextNode(t[1]));
    if (t[3]) tipEl.insertAdjacentHTML("beforeend", t[3]());
    if (t[2]) { const d = document.createElement("div"); d.className = "def"; d.textContent = t[2]; tipEl.append(d); }
    placeTip(el.getBoundingClientRect());
    if (tipFor && tipFor !== el) tipFor.removeAttribute("aria-describedby");
    el.setAttribute("aria-describedby", "tip");
    tipFor = el;
  }
  // below the rect r when it fits, above it otherwise, kept inside the window
  function placeTip(r) {
    tipEl.hidden = false;
    const w = tipEl.offsetWidth, h = tipEl.offsetHeight;
    const x = Math.min(Math.max(8, r.left), window.innerWidth - w - 8);
    const y = r.bottom + 6 + h > window.innerHeight - 8 ? Math.max(8, r.top - h - 6) : r.bottom + 6;
    tipEl.style.left = x + "px"; tipEl.style.top = y + "px";
  }
  // a tip for one data mark (a bar, a heat cell): a title and a line of text at rect r
  function showDataTip(r, title, text) {
    if (tipFor) { tipFor.removeAttribute("aria-describedby"); tipFor = null; }
    const b = document.createElement("b"); b.textContent = title;
    tipEl.replaceChildren(b, document.createTextNode(text));
    placeTip(r);
  }
  function hideTip() {
    tipEl.hidden = true;
    if (tipFor) tipFor.removeAttribute("aria-describedby");
    tipFor = null;
  }
  // non-interactive elements with a tip get a dotted underline and become focusable for the keyboard
  function decorateTips(root = document) {
    root.querySelectorAll("[data-tip]").forEach(el => {
      if (el.matches("input, button, select, textarea, label")) return;
      el.classList.add("tipped");
      if (!el.hasAttribute("tabindex")) el.tabIndex = 0;
    });
  }
  document.addEventListener("pointerover", e => {
    if (e.pointerType !== "mouse") return;
    const el = e.target.closest("[data-tip]"); if (el) showTip(el);
  });
  document.addEventListener("pointerout", e => {
    if (e.pointerType !== "mouse") return;
    const el = e.target.closest("[data-tip]");
    if (el && !el.contains(e.relatedTarget)) hideTip();
  });
  document.addEventListener("pointerdown", e => {
    const el = e.target.closest("[data-tip]");
    if (e.pointerType === "mouse") { if (el && el.matches("input")) hideTip(); return; }
    if (el && el.classList.contains("tipped")) { if (tipFor === el) hideTip(); else showTip(el); }  // tap to show on touch
    else if (el && el.matches('label, input[type="range"]')) showTip(el);  // a tapped slider takes no focus, so show its tip here
    else if (!el) hideTip();
  });
  document.addEventListener("focusin", e => { const el = e.target.closest("[data-tip]"); if (el) showTip(el); });
  document.addEventListener("focusout", () => hideTip());
  document.addEventListener("keydown", e => { if (e.key === "Escape") hideTip(); });
  // a slider moved by keyboard or touch redraws its tip (shown for it or its label), so the infographic follows the value
  document.addEventListener("input", e => { if (tipFor && tipFor.dataset.tip === e.target.dataset.tip) showTip(e.target); });
  // keep a keyboard-focused tip attached while the page scrolls (focus itself scrolls into view); drop hover tips
  window.addEventListener("scroll", () => {
    if (tipFor && tipFor === document.activeElement) showTip(tipFor); else hideTip();
  }, true);
  decorateTips();

  // ---------- about: scroll link (keeps the #cafe=... hash) and data sources ----------
  $("about-link").addEventListener("click", e => { e.preventDefault(); $("about").scrollIntoView({ behavior: "smooth", block: "start" }); });
  const PRIVACY_PUBLIC = "This public copy trims the first and last 500 m of every run, keeps only the month and year each run was made, and leaves out the names of the neighbourhoods I run in most. Before publishing, a check decodes the data inside this page and refuses to publish if it holds one of those names or an exact date.";
  const PRIVACY_PRIVATE = "Private build: full GPS tracks, including where runs start and finish. Do not share this file.";
  // display assets that are not part of the dataset
  const PAGE_SOURCES = [
    { data: "Map tiles (display only)", source: "Esri World Dark Gray Canvas", source_url: "https://www.esri.com/",
      licence: "Esri terms of use", licence_url: "https://www.esri.com/en-us/legal/terms/full-master-agreement",
      date_kind: "", date: "loaded live", detail: "Not part of the dataset." },
    { data: "Map library", source: "Leaflet 1.9.4", source_url: "https://leafletjs.com/",
      licence: "BSD-2-Clause", licence_url: "https://github.com/Leaflet/Leaflet/blob/main/LICENSE",
      date_kind: "", date: "", detail: "Draws the map." },
  ];
  const httpsUrl = u => { try { const x = new URL(u); return x.protocol === "https:" ? x.href : null; } catch { return null; } };
  const cell = (tr, text, url) => {
    const td = tr.insertCell(), href = url && httpsUrl(url);
    if (href) { const a = document.createElement("a"); a.href = href; a.rel = "noopener noreferrer"; a.textContent = text; td.append(a); }
    else td.textContent = text;
  };
  function renderSources(meta) {  // DOM nodes only: metadata strings are never parsed as HTML
    const t = $("sources"), str = v => (typeof v === "string" ? v : v == null ? "" : String(v));
    t.replaceChildren();
    const head = t.createTHead().insertRow();
    ["Data", "Source", "Licence", "Date", "What is used"].forEach(h => { const th = document.createElement("th"); th.textContent = h; head.append(th); });
    const body = t.createTBody();
    (meta && Array.isArray(meta.sources) ? meta.sources : []).concat(PAGE_SOURCES).forEach(s => {
      const tr = body.insertRow();
      cell(tr, str(s.data)); cell(tr, str(s.source), s.source_url); cell(tr, str(s.licence), s.licence_url);
      cell(tr, [str(s.date_kind), str(s.date)].filter(Boolean).join(": ")); cell(tr, str(s.detail));
    });
    $("sources-note").textContent = meta
      ? `Recorded in the dataset when this page was built (${str(meta.built)}, ${str(meta.build)} build).`
      : "This build carries no embedded source notes.";
  }
  $("about-privacy").textContent = MODE === "public" ? PRIVACY_PUBLIC : PRIVACY_PRIVATE;

  // ---------- the engine ----------
  const M = await CafeTol();
  if (M._ct_init() !== 0) {
    $("detail").innerHTML = `<p>Engine failed to load: ${esc(M.UTF8ToString(M._ct_error()))}</p>`;
    renderSources(null);
    return;
  }
  let meta = null;
  try { meta = JSON.parse(M.UTF8ToString(M._ct_meta()) || "null"); } catch { meta = null; }
  renderSources(meta);
  $("about-privacy").textContent = (M._ct_flags() & 1) ? PRIVACY_PUBLIC : PRIVACY_PRIVATE;  // what is actually compiled in
  const f64 = (p, n) => new Float64Array(M.HEAPF64.buffer, p, n);
  const f32 = (p, n) => new Float32Array(M.HEAPF32.buffer, p, n);
  const i32 = (p, n) => new Int32Array(M.HEAP32.buffer, p, n);
  // n engine records of keys.length doubles each, copied out as objects. Call the engine function that
  // fills the buffer (and returns n) before reading its data pointer.
  const records = (p, n, keys) => {
    const D = f64(p, keys.length * n);
    return Array.from({ length: n }, (_, j) => Object.fromEntries(keys.map((k, c) => [k, D[keys.length * j + c]])));
  };

  const nRuns = M._ct_run_count(), nParts = M._ct_part_count(), nPts = M._ct_point_count(), nCafes = M._ct_cafe_count();
  const runYears = Array.from(i32(M._ct_run_years(), nRuns));
  const pts = Float32Array.from(f32(M._ct_points(), 2 * nPts));
  const partStart = Int32Array.from(i32(M._ct_part_start(), nParts + 1));
  const partRun = Int32Array.from(i32(M._ct_part_run(), nParts));
  const cafeLL = Array.from(f64(M._ct_cafe_latlon(), 2 * nCafes));
  const cafeOsm = Array.from(f64(M._ct_cafe_osm(), nCafes));
  const cafeName = Array.from({ length: nCafes }, (_, i) => M.UTF8ToString(M._ct_cafe_name(i)));
  const streetName = i => M.UTF8ToString(M._ct_street_name(i));
  // runs as parts: dates (yyyymmdd; a public build stores day 00, so only month and year can show) and lengths
  const runDates = Array.from(i32(M._ct_run_dates(), nRuns));
  const runLen = Array.from(f64(M._ct_run_lengths(), nRuns));
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const runDate = r => { const v = runDates[r], d = v % 100, m = Math.floor(v / 100) % 100, y = Math.floor(v / 10000);
    return (d ? d + " " : "") + MON[Math.max(0, m - 1)] + " " + y; };
  const km = m => (m / 1000).toFixed(m < 10000 ? 2 : 1) + " km";

  // ---------- state ----------
  const years = [...new Set(runYears)].sort();
  const hash = new URLSearchParams(location.hash.slice(1));
  const state = { ...Object.fromEntries(SLIDERS.map(s => [s.key, s.value])), years: new Set(years), show: hash.get("show") === "both" ? "both" : "all",
    sel: -1, query: "", res: [], annot: true, run: -1, hover: -1, mode: "cafe", lastRun: -1 };  // always opens in the cafe view
  const yearMask = () => [...state.years].reduce((m, y) => m | (1 << Math.max(0, Math.min(31, y - 2000))), 0) >>> 0;

  // ---------- map ----------
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas";
  const WHITE = css("--white");  // the run view's colour, and the callouts' ink on the map
  const map = L.map("map", { preferCanvas: true, zoomControl: true, maxZoom: 19, fadeAnimation: false, maxBoundsViscosity: 1 });
  // Zoom-out stops where the view shows only the City of Toronto, and panning stays inside it.
  // Keep in sync with opendata.TORONTO.
  const CITY = L.latLngBounds([43.581, -79.639], [43.856, -79.115]);
  map.setMaxBounds(CITY);
  const capZoom = () => map.setMinZoom(map.getBoundsZoom(CITY, true));
  capZoom();
  map.on("resize", capZoom);
  const attr = "Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors";
  L.tileLayer(`${ESRI}/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`, { maxNativeZoom: 16, maxZoom: 19, attribution: attr }).addTo(map);
  L.tileLayer(`${ESRI}/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, { maxNativeZoom: 16, maxZoom: 19, pane: "overlayPane" }).addTo(map);

  // Raw runs with additive glow (the route heatmap look); nothing is merged.
  const Glow = L.Layer.extend({
    onAdd(m) {
      this._c = L.DomUtil.create("canvas", "leaflet-zoom-hide");
      this._c.style.pointerEvents = "none";
      m.getPanes().overlayPane.appendChild(this._c);
      m.on("moveend zoomend resize", this._draw, this);
      this._draw();
    },
    onRemove(m) { this._c.remove(); m.off("moveend zoomend resize", this._draw, this); },
    _draw() {
      const m = this._map, size = m.getSize(), dpr = window.devicePixelRatio || 1, c = this._c;
      L.DomUtil.setPosition(c, m.containerPointToLayerPoint([0, 0]));
      c.width = size.x * dpr; c.height = size.y * dpr; c.style.width = size.x + "px"; c.style.height = size.y + "px";
      const ctx = c.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.globalCompositeOperation = "lighter";
      const z = m.getZoom();
      const dim = state.mode === "run" ? 0.3 : 1;  // in the run view the run stands out against a dimmed glow
      ctx.strokeStyle = `rgba(144,133,233,${(z >= 15 ? 0.3 : z >= 13 ? 0.38 : 0.5) * dim})`;
      ctx.lineWidth = z >= 15 ? 2 : z >= 13 ? 1.5 : 1.2;
      ctx.lineJoin = ctx.lineCap = "round";
      const b = m.getBounds().pad(0.1);
      for (let k = 0; k < nParts; k++) {
        if (!state.years.has(runYears[partRun[k]])) continue;
        ctx.beginPath();
        let started = false;
        for (let i = partStart[k]; i < partStart[k + 1]; i++) {
          const lat = pts[2 * i], lon = pts[2 * i + 1];
          if (lat < b._southWest.lat || lat > b._northEast.lat || lon < b._southWest.lng || lon > b._northEast.lng) { started = false; continue; }
          const p = m.latLngToContainerPoint([lat, lon]);
          if (started) ctx.lineTo(p.x, p.y); else { ctx.moveTo(p.x, p.y); started = true; }
        }
        ctx.stroke();
      }
    },
  });
  const glow = new Glow().addTo(map);

  const lls = []; for (let i = 0; i < nCafes; i++) lls.push([cafeLL[2 * i], cafeLL[2 * i + 1]]);
  map.fitBounds(L.latLngBounds(lls).pad(0.05));
  // Pins get their own pane above the selection overlay: stacked Leaflet canvases don't pass
  // mouse events down, so pins drawn under the overlay canvas could never be clicked.
  map.createPane("pins").style.zIndex = 620;
  const pinRenderer = L.canvas({ pane: "pins", padding: 0.3, tolerance: 6 });
  const LABEL_ZOOM = 16;  // from this zoom in, every shown cafe is labelled with its name
  // A cafe pin drawn as its status symbol (see SHAPES), on a dark halo; hit-testing stays the circle's.
  const Pin = L.CircleMarker.extend({
    _updatePath() {
      const R = this._renderer;
      if (!R._drawing || this._empty()) return;
      const ctx = R._ctx, p = this._point, r = Math.max(Math.round(this._radius), 1), o = this.options, c = o.fillColor;
      const disc = (rad, fill) => { ctx.beginPath(); ctx.arc(p.x, p.y, rad, 0, 2 * Math.PI); ctx.fillStyle = fill; ctx.fill(); };
      const circ = (rad, w) => { ctx.beginPath(); ctx.arc(p.x, p.y, rad, 0, 2 * Math.PI); ctx.strokeStyle = c; ctx.lineWidth = w; ctx.stroke(); };
      const half = from => { ctx.beginPath(); ctx.arc(p.x, p.y, r * .88, from, from + Math.PI); ctx.closePath(); ctx.fillStyle = c; ctx.fill(); };
      ctx.save();
      if (o.kind === "faint") {  // run view: a cafe this run didn't pass, still clickable
        disc(r + 1.5, "rgba(28,32,38,.9)");
        ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, 2 * Math.PI); ctx.strokeStyle = "#97a3b0"; ctx.lineWidth = 1.5; ctx.stroke();
        ctx.restore(); return;
      }
      if (o.kind === "num") {  // run view: a cafe this run passed, numbered in route order (filled = accepted)
        disc(r + 2.5, "rgba(12,15,19,.85)");
        if (o.ok) disc(r, c); else { disc(r, "#151a20"); circ(r - 1, 2.2); }
        ctx.fillStyle = o.ok ? "#0c0f13" : c; ctx.font = `700 ${Math.round(r * 1.05)}px "B612 Mono", monospace`;
        ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(String(o.num), p.x, p.y + 0.5);
        ctx.restore(); return;
      }
      disc(r + 2.2, "rgba(28,32,38,.92)");
      if (o.kind === 0) disc(r, c);
      else if (o.kind === 5) disc(Math.max(2, r * .5), c);
      else {
        circ(r * .88, o.kind === 4 ? 2.2 : 1.7);
        if (o.kind === 1) half(Math.PI / 2);        // ⌖ only: left half
        if (o.kind === 2) half(-Math.PI / 2);       // ⌒ only: right half
        if (o.kind === 3) disc(r * .38, c);         // apart: centre dot
      }
      if (o.sel) { ctx.beginPath(); ctx.arc(p.x, p.y, r + 3.5, 0, 2 * Math.PI); ctx.strokeStyle = o.color; ctx.lineWidth = 2; ctx.stroke(); }
      ctx.restore();
    },
  });
  const pins = lls.map((ll, i) => new Pin(ll, { renderer: pinRenderer, radius: 5, weight: 1.5, color: "#e6ebf0", fillOpacity: 1, kind: 0, sel: false })
    .bindTooltip(esc(cafeName[i]), { className: "cafe-tip", direction: "top", offset: [0, -6] })
    .on("click", e => { L.DomEvent.stopPropagation(e); select(i, false); }));  // a pin click is not a run click
  const overlay = L.layerGroup().addTo(map);
  // GD&T annotations: their own pane above the pins, never catching the mouse; laid out in screen
  // pixels, so they are redrawn on every zoom to keep a constant size.
  map.createPane("annot").style.zIndex = 660;  // above pins (620) and cafe-name labels (tooltips, 650)
  map.getPane("annot").style.pointerEvents = "none";
  const annotRenderer = L.svg({ pane: "annot", padding: 0.5 });
  const annot = L.layerGroup().addTo(map);
  // The selected (and hovered) run: above the cafe overlay, below the pins (620).
  map.createPane("runs").style.zIndex = 610;
  map.getPane("runs").style.pointerEvents = "none";
  const runRenderer = L.canvas({ pane: "runs", padding: 0.3 });
  const runLayer = L.layerGroup().addTo(map);
  let lastDetail = null;
  let labelled = null;
  map.on("zoomend", () => {
    const on = map.getZoom() >= LABEL_ZOOM; if (on !== labelled) { labelled = on; drawPins(); }
    drawAnnotations();
  });

  // ---------- engine calls ----------
  function evaluate() {
    state.res = records(M._ct_evaluate(state.tol, state.band, state.acc / 100, yearMask()), nCafes,
      ["runs", "status", "pos", "pro", "both", "best", "med"]).map((r, i) => ({ i, ...r }));
  }
  // Checker's notes. The engine owns the rules, thresholds and numbers (Engine::notes, ct_notes);
  // the page only words them. Each note is [kind, a, b, c, street] (see NoteKind in gdt.hpp).
  const SIDES = ["north", "south", "west", "east"];
  const NOTE_TEXT = {
    1: n => `closest run came within ${Math.max(1, Math.round(n.a))} m of the door`,
    2: n => `${SIDES[n.a]} side of ${streetName(n.street)} on ${n.b} of ${n.c} passes`,
    3: () => "each callout passes alone; they fail on different runs",
    4: n => `p95 ${n.a.toFixed(0)} is just over the band: a few strays, not the line`,
    5: n => `one run in ${n.a} misses both: a detour, not a drift`,
    6: n => `all ${n.a} runs accepted`,
  };
  function notesOf(i) {
    const k = M._ct_notes(i);
    return records(M._ct_notes_data(), k, ["kind", "a", "b", "c", "street"]).filter(n => NOTE_TEXT[n.kind]).map(n => NOTE_TEXT[n.kind](n));
  }
  // The table's ① marks: one engine call counts every cafe's notes, cached per setting.
  let noteCounts = null, noteKey = "";
  const settingsKey = () => [state.R, state.tol, state.band, state.acc, yearMask()].join(",");
  // inside the name's button, in front of the name, so it never wraps onto a line of its own;
  // the legend and the Status tip explain it
  const MARK = `<span class="mark" aria-hidden="true">①</span><span class="vh">(checker's note) </span>`;
  function markNotes() {
    if (noteKey !== settingsKey()) { noteCounts = Int32Array.from(i32(M._ct_note_counts(), nCafes)); noteKey = settingsKey(); }
    $("cafes").querySelectorAll("button[data-i]").forEach(b => {
      if (noteCounts[+b.dataset.i] && !b.querySelector(".mark")) b.insertAdjacentHTML("afterbegin", MARK);
    });
  }
  function detail(i) {
    const n = M._ct_detail(i);
    const passes = records(M._ct_detail_passes(), n, ["run", "name", "start", "end", "d", "qlat", "qlon", "prof", "mean"])
      .map(p => ({ ...p, q: [p.qlat, p.qlon] }));
    const np = M._ct_detail_part_count();
    const parts = records(M._ct_detail_parts(), np, ["run", "d", "prof", "pos", "pro"]).map(p => ({ ...p, pos: !!p.pos, pro: !!p.pro }));
    const nf = M._ct_detail_feature_count();
    const feats = records(M._ct_detail_features(), nf, ["street", "passes", "pos", "pro", "both", "p95", "bias", "nx", "ny"])
      .map(f => ({ ...f, name: streetName(f.street) }));
    const polys = (count, startFn, dataFn) => {
      const c = count(); if (c <= 0) return [];
      const s = Int32Array.from(i32(startFn(), c + 1)), d = f32(dataFn(), 2 * s[c]);
      return Array.from({ length: c }, (_, k) => { const out = []; for (let j = s[k]; j < s[k + 1]; j++) out.push([d[2 * j], d[2 * j + 1]]); return out; });
    };
    const lines = polys(M._ct_detail_line_count, M._ct_detail_line_start, M._ct_detail_lines);
    const bands = polys(M._ct_detail_band_count, M._ct_detail_band_start, M._ct_detail_bands);
    const lineNames = lines.length ? Array.from(i32(M._ct_detail_line_names(), lines.length), streetName) : [];
    return { passes, parts, feats, lines, bands, lineNames };
  }

  // ---------- rendering ----------
  const visible = r => state.show === "all" || r.status === 0;
  const pct = y => Math.round(100 * y) + "%";
  const ychip = (y, n) => n ? `<span class="chip ${y >= state.acc / 100 - 1e-9 ? "ok" : "bad"}">${pct(y)}</span>` : `<span class="chip mid">–</span>`;
  const schip = (s, short) => `<span class="chip st" data-tip="status-${s}">${sym(s)}${esc(short ? STATUS[s].short : STATUS[s].label)}</span>`;

  // rebinding a tooltip is costly, so only when its text or permanence changes
  function setPinTip(pin, html, permanent) {
    const key = (permanent ? "P" : "T") + html;
    if (pin._tipKey === key) return;
    pin._tipKey = key;
    pin.unbindTooltip().bindTooltip(html, { className: "cafe-tip" + (permanent ? " label" : ""),
      direction: permanent ? "right" : "top", offset: permanent ? [10, 0] : [0, -6], permanent });
  }
  function drawPins() {
    if (state.mode === "run") {  // the run's cafes numbered in route order; every other cafe a faint, clickable dot
      const order = new Map(runReport(state.run).map((w, k) => [w.cafe, { k: k + 1, w }]));
      state.res.forEach(r => {
        const pin = pins[r.i], o = order.get(r.i);
        if (o) {
          const ok = o.w.pos && o.w.pro, col = css(ok ? "--s-both" : "--s-none");
          pin.setStyle({ kind: "num", num: o.k, ok, fillColor: col, sel: false, color: "#e6ebf0", weight: 1.2 });
          pin.setRadius(9);
          // labels stay on from zoom 16, as in the cafe view; at a whole-run zoom they'd overlap, so hover shows them
          setPinTip(pin, `<b style="color:${col}">${o.k}</b> ${esc(cafeName[r.i])} · Ø${(2 * o.w.d).toFixed(0)} ${ok ? "✓" : "✗"}`,
            map.getZoom() >= LABEL_ZOOM);
          pin.bringToFront();
        } else {
          pin.setStyle({ kind: "faint", fillColor: "#97a3b0", sel: false, color: "#97a3b0", weight: 1 });
          pin.setRadius(4);
          setPinTip(pin, esc(cafeName[r.i]), false);
        }
        if (!map.hasLayer(pin)) pin.addTo(map);
      });
      return;
    }
    const both = state.res.filter(r => r.status === 0).length;
    state.res.forEach(r => {
      const pin = pins[r.i], show = visible(r) || r.i === state.sel;
      if (!show) { if (map.hasLayer(pin)) pin.remove(); return; }
      pin.setStyle({ fillColor: css(STATUS[r.status].color), kind: r.status, sel: r.i === state.sel, color: "#e6ebf0",
        weight: r.i === state.sel ? 2.5 : 1.2 });
      pin.setRadius(r.i === state.sel ? 7.5 : (r.status === 5 ? 5 : 6));
      if (!map.hasLayer(pin)) pin.addTo(map);
      const permanent = map.getZoom() >= LABEL_ZOOM || (state.show === "both" && both <= 40);
      setPinTip(pin, esc(cafeName[r.i]), permanent);
    });
  }

  function table() {
    const res = state.res, nBoth = res.filter(r => r.status === 0).length;
    $("count").innerHTML = `<b>${nBoth}</b> of ${nCafes} cafes conform to both at ${state.acc}% acceptance · Ø${state.tol} · band ${state.band} m · R ${state.R} m`;
    const q = state.query.toLowerCase();
    const rows = res.filter(r => visible(r) && (!q || cafeName[r.i].toLowerCase().includes(q)))
      .sort((a, b) => (a.status === 0 ? 0 : 1) - (b.status === 0 ? 0 : 1)
        || (a.status === 0 ? b.runs - a.runs : b.both - a.both) || b.runs - a.runs || a.i - b.i);
    const shown = rows.slice(0, 300);
    $("cafes").innerHTML = rows.length
      ? thead(["Cafe", ["Runs", "col-runs"], ["⌖", "col-pos"], ["⌒", "col-pro"], ["Both", "col-both"], ["Status", "col-status"]]) + "<tbody>" +
        shown.map(r => `<tr class="${r.i === state.sel ? "sel" : ""}"><td><button type="button" class="linkbtn" data-i="${r.i}">${esc(cafeName[r.i])}</button></td>
          <td class="num">${r.runs}</td><td class="num">${ychip(r.pos, r.runs)}</td><td class="num">${ychip(r.pro, r.runs)}</td><td class="num">${ychip(r.both, r.runs)}</td>
          <td>${schip(r.status, true)}</td></tr>`).join("") + "</tbody>"
      : `<tbody><tr><td class="muted">No cafe matches. ${state.show === "both" ? "Widen a tolerance or lower the acceptance yield." : ""}</td></tr></tbody>`;
    $("status-line").textContent = rows.length > shown.length ? `Showing the first ${shown.length} of ${rows.length}; search to narrow.` : "";
    decorateTips($("cafes"));
    markNotes();
  }

  function histogram(parts) {
    const maxD = 2 * state.R, bw = Math.max(5, Math.round(maxD / 40 / 5) * 5), nb = Math.ceil(maxD / bw), bins = new Array(nb).fill(0);
    parts.forEach(p => bins[Math.min(nb - 1, Math.floor(2 * p.d / bw))]++);
    const top = Math.max(1, ...bins), W = 320, H = 72, bx = W / nb, tx = Math.min(W, state.tol / maxD * W);
    let s = `<svg class="hist" data-tip="hist" viewBox="0 0 ${W} ${H + 30}" role="img" aria-label="Histogram of Ø per run"><text x="0" y="10">Ø of each run (closest approach)</text>`;
    bins.forEach((c, i) => { if (!c) return; const h = c / top * (H - 18);
      s += `<rect x="${(i * bx + .5).toFixed(1)}" y="${(H - h).toFixed(1)}" width="${Math.max(1, bx - 1).toFixed(1)}" height="${h.toFixed(1)}" class="${(i + .5) * bw <= state.tol ? "ok" : "bad"}"/>`; });
    s += `<line x1="0" y1="${H}" x2="${W}" y2="${H}" class="axis"/><line x1="${tx}" y1="14" x2="${tx}" y2="${H}" class="tol"/>`;
    s += `<text x="0" y="${H + 14}">Ø0</text><text x="${W}" y="${H + 14}" text-anchor="end">Ø${maxD}</text><text x="${Math.min(W - 44, tx + 4)}" y="26" class="z">Ø${state.tol}</text></svg>`;
    return s;
  }

  const side = f => { const ew = Math.abs(f.ny) >= Math.abs(f.nx); const b = f.bias;
    return `${b >= 0 ? "+" : "−"}${Math.abs(b).toFixed(1)} m ${ew ? (b >= 0 ? "N" : "S") : (b >= 0 ? "W" : "E")}`; };

  function drawDetail() {
    overlay.clearLayers();
    $("detail").hidden = state.mode === "run";  // the run view has its own report; the cafe's detail is for the cafe view
    if (state.sel < 0) { $("detail").innerHTML = `<p class="muted">Click a cafe on the map or in the table to inspect it.</p>`; return; }
    const i = state.sel, r = state.res[i], d = detail(i), c = lls[i];
    // drawing on the map: R, nominal + band, passes, closest approaches, Ø zone
    L.circle(c, { radius: state.R, color: "#9aa4b0", weight: 1, dashArray: "3 5", fill: false, interactive: false }).addTo(overlay);
    d.bands.forEach(b => L.polygon(b, { color: css("--zone"), weight: 1, dashArray: "5 4", fillColor: "#79a8ff", fillOpacity: .12, interactive: false }).addTo(overlay));
    d.lines.forEach(l => L.polyline(l, { color: "#79a8ff", weight: 1.6, dashArray: "12 3 3 3", interactive: false }).addTo(overlay));
    const partOf = new Map(d.parts.map(p => [p.run, p]));
    d.passes.forEach(p => {
      const ll = []; for (let k = p.start; k <= p.end; k++) ll.push([pts[2 * k], pts[2 * k + 1]]);
      const part = partOf.get(p.run), ok = part && part.pos && part.pro;
      L.polyline(ll, { color: ok ? css("--s-both") : css("--s-none"), weight: ok ? 1.4 : 2, opacity: .9, interactive: false }).addTo(overlay);
      L.circleMarker(p.q, { radius: 2.5, stroke: false, fillColor: 2 * p.d <= state.tol ? css("--s-both") : css("--s-none"), fillOpacity: 1, interactive: false }).addTo(overlay);
    });
    L.circle(c, { radius: state.tol / 2, color: "#ff6a6a", weight: 1.6, dashArray: "6 4", fill: false, interactive: false }).addTo(overlay);
    const notes = notesOf(i);
    lastDetail = { i, r, d, notes };
    if (state.mode === "run") overlay.clearLayers();  // kept for the step list and picker, but not drawn
    drawAnnotations();

    const n = d.parts.length, posN = d.parts.filter(p => p.pos).length, proN = d.parts.filter(p => p.pro).length, bothN = d.parts.filter(p => p.pos && p.pro).length;
    const prof = d.parts.map(p => p.prof).sort((a, b) => a - b), p95 = n ? prof[Math.min(n - 1, Math.round(.95 * (n - 1)))] : 0;
    const frame = (sym, tol, actual, y, tip) => `<div class="frame-row"><span class="fcf" data-tip="${tip}"><span>${sym}</span><span>${tol}</span>${datumCell(sym)}</span><span class="actual">${actual}</span>${ychip(y, n)}</div>`;
    $("detail").innerHTML = `<span class="eyebrow">Selected cafe · datum A</span>
      <h3>${esc(cafeName[i])} ${schip(r.status)}</h3>` +
      (n ? frame(SYM_POS, "Ø" + state.tol, `best Ø${r.best.toFixed(0)} · median Ø${r.med.toFixed(0)} · ${posN}/${n} runs`, r.pos, "frame-pos") +
           frame(SYM_PROF, state.band, `p95 ${p95.toFixed(1)} · ${proN}/${n} runs`, r.pro, "frame-pro") +
           `<div class="frame-row"><span class="actual" data-tip="frame-both"><b>both callouts:</b> ${bothN}/${n} runs accepted</span>${ychip(r.both, n)}</div>` +
           (notes.length ? `<div class="notes"><span class="eyebrow" data-tip="notes">Checker's notes</span>` +
             notes.map((t, k) => `<span class="pen">${"①②③"[k]} ${esc(t)}</span>`).join("") + `</div>` : "") +
           `
            <span class="hint">Counts are runs. A run that passes on two streets counts once: its closest approach for ⌖ and its worst pass for ⌒. The street table counts passes.</span>
            <div class="tablewrap"><table>` + thead([["Street (nominal)", "st-street"], ["Passes", "st-passes"], ["⌖", "st-pos"], ["⌒", "st-pro"],
             ["Both", "st-both"], ["⌒ p95", "st-p95"], ["Bias", "st-bias"]]) + "<tbody>" +
           d.feats.map(f => `<tr><td>${esc(f.name)}</td><td class="num">${f.passes}</td><td class="num">${f.pos}</td><td class="num">${f.pro}</td><td class="num">${f.both}</td><td class="num">${f.p95.toFixed(1)}</td><td class="num">${side(f)}</td></tr>`).join("") +
           `</tbody></table></div>` + histogram(d.parts) + runsTable(d.parts)
         : `<p class="muted">No run passes within R = ${state.R} m of this cafe in the selected years.</p>`);
    decorateTips($("detail"));
  }

  // one row per run (part) at the selected cafe, by date; a row opens the run report
  function runsTable(parts) {
    const rows = parts.slice().sort((a, b) => runDates[a.run] - runDates[b.run] || a.run - b.run);
    return `<h3 data-tip="runs-table">Runs at this cafe <span class="hint">click a row to inspect the run</span></h3>
      <div class="tablewrap runs-table"><table>` + thead(["Run", "Ø (2d)", "⌒ actual", "⌖", "⌒", "Result"]) + "<tbody>" +
      rows.map(p => `<tr class="clickable${p.run === state.run ? " sel" : ""}" data-run="${p.run}">
        <td><button type="button" class="linkbtn">${runDate(p.run)}</button></td>
        <td class="num">Ø${(2 * p.d).toFixed(0)}</td><td class="num">${p.prof.toFixed(1)}</td><td class="num">${mark(p.pos)}</td><td class="num">${mark(p.pro)}</td>
        <td class="${p.pos && p.pro ? "yes" : "no"}">${p.pos && p.pro ? "accepted" : "rejected"}</td></tr>`).join("") +
      `</tbody></table></div>`;
  }

  // ---------- GD&T annotations on the map ----------
  const toPt = ll => map.latLngToLayerPoint(ll), toLL = p => map.layerPointToLatLng(p);
  const pxPerMetre = ll => {
    const a = toPt(ll), b = toPt([ll[0], ll[1] + 100 / kx(ll[0])]);
    return (b.x - a.x) / 100;
  };
  const annLine = (pts, o = {}) => L.polyline(pts.map(toLL), Object.assign({ renderer: annotRenderer, color: WHITE,
    weight: 1.2, opacity: .95, interactive: false }, o)).addTo(annot);
  const annHtml = (p, html, cls = "") => L.marker(toLL(p), { pane: "annot", interactive: false, keyboard: false,
    icon: L.divIcon({ className: "anno " + cls, html, iconSize: [0, 0] }) }).addTo(annot);
  const annFrame = (p, sym, tol, act, left) => annHtml(p,
    `<div class="box"><div class="fr"><span>${sym}</span><span>${esc(tol)}</span>${datumCell(sym)}</div><div class="act">${act}</div></div>`, left ? "left" : "");
  const yieldTxt = (k, n) => { const y = n ? k / n : 0; return `<b class="${y >= state.acc / 100 - 1e-9 ? "ok" : "bad"}">${k}/${n} runs · ${pct(y)}</b>`; };
  // Callouts are laid out like a drawing: frames sit in two columns just outside the R circle (left
  // and right of the cafe), each at the height of the feature it points to, stacked so they don't
  // overlap, and a frame switches columns rather than run off the map.
  const FRAME_GAP = 46, SHOULDER = 17;
  const plain = html => html.replace(/<[^>]*>/g, "").replace(/&amp;/g, "&").replace(/&[a-z]+;/g, "x");
  function layoutCallouts(C, ringR, callouts) {
    const size = map.getSize(), cc = map.layerPointToContainerPoint(C);
    const roomL = cc.x - ringR - SHOULDER - 8, roomR = size.x - (cc.x + ringR) - SHOULDER - 8;
    callouts.forEach(o => {
      const v = o.from.subtract(C), l = Math.hypot(v.x, v.y);
      const ux = l > 1 ? v.x / l : o.ux, uy = l > 1 ? v.y / l : o.uy;
      const w = 20 + 6.6 * plain(o.act).length;  // estimated frame width (mono 10.5 px)
      o.left = ux < 0;
      if ((o.left ? roomL : roomR) < w) o.left = roomL >= w ? true : roomR >= w ? false : roomL > roomR;
      // the column sits just outside the R circle; slide it inwards if the frame would leave the map
      const shift = Math.max(0, w - (o.left ? roomL : roomR));
      const x = C.x + (o.left ? -(ringR - shift) : ringR - shift);
      const y = Math.min(Math.max(C.y + uy * ringR * 0.8, C.y - cc.y + 24), C.y - cc.y + size.y - 40);
      o.ring = L.point(x, y);
    });
    [true, false].forEach(side => {  // stack each side top to bottom
      const col = callouts.filter(o => o.left === side).sort((a, b) => a.ring.y - b.ring.y);
      for (let k = 1; k < col.length; k++)
        if (col[k].ring.y - col[k - 1].ring.y < FRAME_GAP) col[k].ring = L.point(col[k].ring.x, col[k - 1].ring.y + FRAME_GAP);
    });
    callouts.forEach(o => {
      const end = o.ring.add(L.point(o.left ? -14 : 14, 0));
      annLine([o.from, o.ring, end]);
      L.circleMarker(toLL(o.from), { renderer: annotRenderer, radius: 2.5, stroke: false, fillColor: WHITE, fillOpacity: 1,
        interactive: false }).addTo(annot);  // dot where the leader touches the feature
      annFrame(end.add(L.point(o.left ? -3 : 3, 0)), o.sym, o.tol, o.act, o.left);
    });
  }

  function drawAnnotations() {
    annot.clearLayers();
    if (state.mode !== "cafe" || !state.annot || state.sel < 0 || !lastDetail || lastDetail.i !== state.sel) return;
    const { i, r, d } = lastDetail;
    const c = lls[i], C = toPt(c), ppm = pxPerMetre(c);
    if (!d.parts.length) return;

    // datum A: feature symbol on the cafe
    const dA = C.add(L.point(-26, 26));
    annLine([C, dA]);
    annHtml(dA.add(L.point(-2, 0)), `<div class="box"><div class="datum">A</div></div>`, "left");

    // ⌖ true position: frame on a leader from the Ø zone, actual = the run Ø values
    const callouts = [];
    const rz = Math.max(6, state.tol / 2 * ppm), ang = -Math.PI / 4;
    const posN = d.parts.filter(p => p.pos).length;
    callouts.push({ from: C.add(L.point(Math.cos(ang) * rz, Math.sin(ang) * rz)), ux: Math.SQRT1_2, uy: -Math.SQRT1_2,
      sym: SYM_POS, tol: "Ø" + state.tol,
      act: `best Ø${r.best.toFixed(0)} · median Ø${r.med.toFixed(0)} · ${yieldTxt(posN, d.parts.length)}` });
    // dimension: the best run's closest approach, Ø = 2d
    const best = d.passes.reduce((m, p) => (!m || p.d < m.d ? p : m), null);
    if (best) {
      const Q = toPt(best.q);
      if (C.distanceTo(Q) > 10) {
        annLine([C, Q], { dashArray: "3 3" });
        annHtml(C.add(Q).divideBy(2), `<div class="dim">Ø${(2 * best.d).toFixed(0)}</div>`);
      }
    }

    // ⌒ profile of a line: per street (most passes first, up to 3), a band-width dimension and a frame
    d.feats.slice(0, 3).forEach(f => {
      const cand = d.lines.map((l, k) => ({ l, k })).filter(o => d.lineNames[o.k] === f.name && o.l.length >= 3);
      if (!cand.length) return;
      const line = cand.reduce((a, b) => (b.l.length > a.l.length ? b : a)).l.map(toPt);
      let k = 1, far = -1;  // the point farthest from the cafe (clear of the other annotations)
      for (let j = 1; j < line.length - 1; j++) { const dd = line[j].distanceTo(C); if (dd > far) { far = dd; k = j; } }
      const P = line[k], t = line[k + 1].subtract(line[k - 1]), tl = Math.hypot(t.x, t.y) || 1;
      const tx = t.x / tl, ty = t.y / tl;
      let nx = -ty, ny = tx;
      if ((P.x - C.x) * nx + (P.y - C.y) * ny < 0) { nx = -nx; ny = -ny; }  // put the callout on the side away from the cafe
      const half = state.band / 2 * ppm, a = P.subtract(L.point(nx * half, ny * half)), b = P.add(L.point(nx * half, ny * half));
      annLine([a, b]);
      [a, b].forEach(e => annLine([e.subtract(L.point(tx * 6, ty * 6)), e.add(L.point(tx * 6, ty * 6))], { weight: 1.6 }));
      const ok = f.pro / f.passes >= state.acc / 100 - 1e-9;
      callouts.push({ from: b, ux: nx, uy: ny, sym: SYM_PROF, tol: String(state.band),
        act: `${esc(f.name)} · p95 ${f.p95.toFixed(1)} · <b class="${ok ? "ok" : "bad"}">${f.pro}/${f.passes}</b> passes` });
    });
    layoutCallouts(C, state.R * ppm + 30, callouts);

    // the first checker's note, written above the R circle with a dashed leader down to the cafe
    if (lastDetail.notes.length && state.R * ppm >= 30) {  // zoomed far out, the note would pile onto the callouts
      let P = C.add(L.point(0, -(state.R * ppm) - 26));
      const top = map.layerPointToContainerPoint(P).y;
      if (top < 34) P = P.add(L.point(0, 34 - top));
      annLine([P.add(L.point(0, 4)), C.add(L.point(0, -10))], { color: css("--note"), weight: 1.4, dashArray: "4 3" });
      annHtml(P, `<div class="pen">① ${esc(lastDetail.notes[0])}</div>`, "note");
    }
  }

  function writeHash() {
    const h = new URLSearchParams();
    if (state.sel >= 0) h.set("cafe", String(cafeOsm[state.sel]));
    if (state.show === "both") h.set("show", "both");
    if (state.mode === "run") h.set("run", String(state.run + 1));  // the run's position in the build, never an activity id
    history.replaceState(null, "", "#" + h.toString());
  }

  // Selecting a cafe always shows the cafe view; leaving the run view zooms in on the cafe.
  function select(i, pan) {
    const wasRun = state.mode === "run";
    state.sel = i;
    if (wasRun) { state.lastRun = state.run; state.run = -1; state.mode = "cafe"; state.hover = -1; map.closePopup(); glow._draw(); }
    if (pan || wasRun) map.setView(lls[i], Math.max(wasRun ? 0 : map.getZoom(), 17), { animate: false });
    writeHash();
    drawPins(); table(); drawDetail(); drawRun(); renderRunReport(); viewChrome();
  }

  // ---------- the lot (About): the engine sums the runs (LotStats, ct_lot) and grids them (ct_heat); the page draws ----------
  const HEAT_CELL_M = 200, KX = kx(43.7);  // the engine's projection
  const HEAT_COLS = Math.round((CITY.getEast() - CITY.getWest()) * KX / HEAT_CELL_M);
  const HEAT_ROWS = Math.round((CITY.getNorth() - CITY.getSouth()) * KY / HEAT_CELL_M);
  const HEAT_PAD = 4, HEAT_NEAR_M = 50;  // margin (coarse cells) around the runs; hover radius
  let hv = null, heatW = 0;  // the drawn box (lat / lon, metres, CSS px) and the width it was drawn at
  // one hue, darker = fewer runs; validated as an ordinal ramp against the sheet (lowest step 2.2:1)
  const HEAT_RAMP = ["#4d4794", "#6259b6", "#7a6fd6", "#9a90f0", "#c0b8ff", "#e8e4ff"];
  const monthName = m => MON[m % 12] + " " + Math.floor(m / 12);
  const fmtInt = n => Math.round(n).toLocaleString("en-CA");
  let heatMask = null, lotMonths = null;

  function renderLot() {
    const p = M._ct_lot(), H = f64(p, 11), nm = H[10], D = Array.from(f64(p, 11 + nm));
    const [runs, kmTot, kmMed, kmMax, parts, acc, medCafes, most, mostRun, m0] = D;
    const perMonth = D.slice(11);
    lotMonths = { m0, perMonth };
    const ys = years.filter(y => state.years.has(y));
    $("lot-scope").textContent = runs
      ? `${monthName(m0)} – ${monthName(m0 + nm - 1)} · years ${ys.join(", ")} · scored with the current settings`
      : "No runs in the selected years.";
    const tile = (tip, k, v, s) => `<div class="kpi"><span class="k" data-tip="${tip}">${k}</span><span class="v">${v}</span><span class="s">${s}</span></div>`;
    $("lot-kpis").innerHTML = runs ? [
      tile("lot-runs", "Runs", fmtInt(runs), `${perMonth.filter(n => n).length} of ${nm} months`),
      tile("lot-km", "Distance", fmtInt(kmTot) + " km", `median ${kmMed.toFixed(1)} · longest ${kmMax.toFixed(1)} km`),
      tile("lot-cafes", "Cafes per run", fmtInt(medCafes),
        most ? `median · most ${most}, <button type="button" class="linkbtn" data-run="${mostRun}">${runDate(mostRun)}</button>` : "median"),
      tile("lot-acc", "Accepted at a cafe", parts ? Math.round(100 * acc / parts) + "%" : "–", `${fmtInt(acc)} of ${fmtInt(parts)} run–cafe pairs`),
    ].join("") : "";
    decorateTips($("lot-kpis"));
    drawMonths(m0, perMonth);
    if (heatMask !== yearMask()) { heatMask = yearMask(); drawHeat(); }
  }
  $("lot-kpis").addEventListener("click", e => {
    const b = e.target.closest("button[data-run]"); if (!b) return;
    selectRun(+b.dataset.run);
    document.querySelector(".mapwrap").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  // runs per calendar month: one series, so no legend; each bar has a hover tip
  function drawMonths(m0, perMonth) {
    // drawn at its real width, so the labels keep their size on a phone
    const svg = $("lot-months"), n = perMonth.length, W = Math.max(240, Math.round(svg.parentElement.clientWidth) || 480), BH = 64, top = 4;
    if (!n) { svg.innerHTML = ""; $("lot-peak").textContent = ""; svg.setAttribute("viewBox", `0 0 ${W} 1`); return; }
    const max = Math.max(...perMonth), slot = W / n, bw = Math.max(1, Math.min(14, slot - 2));
    const peak = perMonth.indexOf(max);
    $("lot-peak").textContent = `peak ${max} in ${monthName(m0 + peak)}`;
    let s = "";
    perMonth.forEach((v, k) => {
      const x = k * slot + (slot - bw) / 2, h = v ? Math.max(2, BH * v / max) : 0;
      if (h) s += `<path class="bar" data-k="${k}" d="M${x},${top + BH} V${top + BH - h + 2} q0,-2 2,-2 H${x + bw - 2} q2,0 2,2 V${top + BH} Z"/>`;
      s += `<rect class="hit" data-k="${k}" x="${k * slot}" y="0" width="${slot}" height="${top + BH + 18}"/>`;
      const m = m0 + k;
      if (m % 12 === 0 || (k === 0 && 12 - (m % 12) >= 3)) s += `<text x="${k * slot + 1}" y="${top + BH + 14}">${m % 12 === 0 ? Math.floor(m / 12) : monthName(m)}</text>`;
    });
    s += `<line class="base" x1="0" x2="${W}" y1="${top + BH + 0.5}" y2="${top + BH + 0.5}"/>`;
    svg.setAttribute("viewBox", `0 0 ${W} ${top + BH + 18}`);
    svg.setAttribute("aria-label", `Runs per month, ${monthName(m0)} to ${monthName(m0 + n - 1)}; peak ${max} in ${monthName(m0 + peak)}`);
    svg.innerHTML = s;
  }
  let monthsW = 0;
  new ResizeObserver(() => {
    const w = Math.round($("lot-months").parentElement.clientWidth);
    if (lotMonths && w !== monthsW) { monthsW = w; drawMonths(lotMonths.m0, lotMonths.perMonth); }
    if (hv && Math.abs(Math.round($("lot-heat-wrap").clientWidth) - heatW) > 2) drawHeat();  // redrawn sharp at the new width
  }).observe($("lot"));
  const unlightBars = () => $("lot-months").querySelectorAll(".bar.on").forEach(b => b.classList.remove("on"));
  $("lot-months").addEventListener("pointermove", e => {
    const hit = e.target.closest("[data-k]");
    unlightBars();
    if (!hit || !lotMonths) { hideTip(); return; }
    const k = +hit.dataset.k;
    const bar = $("lot-months").querySelector(`.bar[data-k="${k}"]`); if (bar) bar.classList.add("on");
    showDataTip((bar || hit).getBoundingClientRect(), monthName(lotMonths.m0 + k), plural(lotMonths.perMonth[k], "run"));
  });
  $("lot-months").addEventListener("pointerleave", () => { unlightBars(); hideTip(); });

  // run density, like a route heatmap: the engine counts distinct runs per cell (ct_heat), the page paints.
  // A coarse pass over the City finds where the runs go; a fine pass over that box puts one cell on each
  // device pixel, so the density reads as route lines, not blocks.
  function drawHeat() {
    const wrap = $("lot-heat-wrap"), c = $("lot-heat"), dpr = Math.min(2, window.devicePixelRatio || 1);
    const cssW = Math.max(200, Math.round(wrap.clientWidth) || 400);
    heatW = cssW;
    // 1. coarse: which 200 m cells do the runs reach?
    const n = HEAT_COLS * HEAT_ROWS, coarse = i32(M._ct_heat(HEAT_COLS, HEAT_ROWS, CITY.getSouth(), CITY.getWest(), CITY.getNorth(), CITY.getEast()), n);
    let c0 = HEAT_COLS, c1 = -1, r0 = HEAT_ROWS, r1 = -1;
    for (let i = 0; i < n; i++) {
      if (!coarse[i]) continue;
      const col = i % HEAT_COLS, row = Math.floor(i / HEAT_COLS);
      c0 = Math.min(c0, col); c1 = Math.max(c1, col); r0 = Math.min(r0, row); r1 = Math.max(r1, row);
    }
    if (c1 < 0) { c0 = 0; c1 = HEAT_COLS - 1; r0 = 0; r1 = HEAT_ROWS - 1; }
    const dLat = (CITY.getNorth() - CITY.getSouth()) / HEAT_ROWS, dLon = (CITY.getEast() - CITY.getWest()) / HEAT_COLS;
    let north = CITY.getNorth() - (r0 - HEAT_PAD) * dLat, south = CITY.getNorth() - (r1 + 1 + HEAT_PAD) * dLat;
    let west = CITY.getWest() + (c0 - HEAT_PAD) * dLon, east = CITY.getWest() + (c1 + 1 + HEAT_PAD) * dLon;
    // keep the drawing between 0.6 and 1.1 times as tall as it is wide, growing the shorter side about its centre
    let wM = (east - west) * KX, hM = (north - south) * KY;
    if (hM > 1.1 * wM) { const g = (hM / 1.1 - wM) / 2 / KX; west -= g; east += g; wM = hM / 1.1; }
    if (hM < 0.6 * wM) { const g = (0.6 * wM - hM) / 2 / KY; south -= g; north += g; hM = 0.6 * wM; }
    // 2. fine: one cell per device pixel
    const cols = Math.round(cssW * dpr), rows = Math.max(1, Math.round(cols * hM / wM));
    const fine = i32(M._ct_heat(cols, rows, south, west, north, east), cols * rows);
    let max = 0;
    for (let i = 0; i < fine.length; i++) if (fine[i] > max) max = fine[i];
    const img = new ImageData(cols, rows), px = img.data, lmax = Math.log(Math.max(2, max));
    for (let i = 0; i < fine.length; i++) {
      const v = fine[i]; if (!v) continue;
      const [r, g, b] = heatColour(Math.log(v) / lmax);
      px[4 * i] = r; px[4 * i + 1] = g; px[4 * i + 2] = b; px[4 * i + 3] = 255;
    }
    hv = { south, west, north, east, wM, hM, cssW, cssH: cssW * hM / wM, max };
    c.width = cols; c.height = rows;
    const ctx = c.getContext("2d"), off = document.createElement("canvas");
    off.width = cols; off.height = rows; off.getContext("2d").putImageData(img, 0, 0);
    ctx.clearRect(0, 0, cols, rows);
    // a soft glow under the lines, then the lines themselves, added on top so busy stretches bloom
    ctx.globalAlpha = 0.7; ctx.filter = `blur(${2 * dpr}px)`; ctx.drawImage(off, 0, 0);
    ctx.globalAlpha = 1; ctx.filter = "none"; ctx.globalCompositeOperation = "lighter"; ctx.drawImage(off, 0, 0);
    ctx.globalCompositeOperation = "source-over";
    c.setAttribute("aria-label", `Map of run density in the City: brighter means more runs, up to ${max} distinct runs on one stretch`);
    // a drawing's scale bar and north arrow, in CSS pixels
    const W = hv.cssW, Hh = hv.cssH, km = wM > 12000 ? 2000 : 1000, kmPx = km / wM * W, x0 = W - kmPx - 14, y0 = Hh - 12;
    $("lot-heat-over").setAttribute("viewBox", `0 0 ${W} ${Hh}`);
    $("lot-heat-over").innerHTML =
      `<path class="scale" d="M${x0},${y0 - 5} V${y0} H${x0 + kmPx} V${y0 - 5}"/><text x="${x0 + kmPx / 2}" y="${y0 - 8}" text-anchor="middle">${km / 1000} km</text>` +
      `<path class="scale" d="M18,34 V14 M12,22 L18,12 L24,22"/><text x="18" y="48" text-anchor="middle">N</text>` +
      `<circle class="ring" id="heat-ring" r="0"/>`;
    $("heat-legend").innerHTML = `<span>1 run</span><i class="grad" style="background:linear-gradient(90deg,${HEAT_RAMP.join(",")})"></i>` +
      `<span>${max} runs</span><span class="hint">log scale</span>`;
  }
  // the ramp's stops, blended: t = 0 is one run, t = 1 the busiest stretch (log scale)
  const HEAT_RGB = HEAT_RAMP.map(h => [1, 3, 5].map(k => parseInt(h.slice(k, k + 2), 16)));
  function heatColour(t) {
    const x = Math.min(1, Math.max(0, t)) * (HEAT_RGB.length - 1), k = Math.min(HEAT_RGB.length - 2, Math.floor(x)), f = x - k;
    return HEAT_RGB[k].map((v, j) => Math.round(v + (HEAT_RGB[k + 1][j] - v) * f));
  }
  // the point under the pointer, in the drawing's box
  function heatPointAt(e) {
    if (!hv) return null;
    const r = $("lot-heat").getBoundingClientRect(), fx = (e.clientX - r.left) / r.width, fy = (e.clientY - r.top) / r.height;
    if (fx < 0 || fy < 0 || fx > 1 || fy > 1) return null;
    return { lat: hv.north - fy * (hv.north - hv.south), lon: hv.west + fx * (hv.east - hv.west), x: fx * hv.cssW, y: fy * hv.cssH, cx: e.clientX, cy: e.clientY };
  }
  $("lot-heat-wrap").addEventListener("pointermove", e => {
    const p = heatPointAt(e), ring = $("heat-ring");
    const k = p ? M._ct_runs_at(p.lat, p.lon, HEAT_NEAR_M) : 0;  // the engine's own query: distinct runs within 50 m
    if (!k) { if (ring) ring.setAttribute("r", 0); hideTip(); return; }
    ring.setAttribute("cx", p.x); ring.setAttribute("cy", p.y); ring.setAttribute("r", Math.max(4, HEAT_NEAR_M / hv.wM * hv.cssW));
    showDataTip({ left: p.cx + 12, right: p.cx + 12, top: p.cy - 6, bottom: p.cy + 6 }, plural(k, "run"),
      `pass within ${HEAT_NEAR_M} m of here. Click to zoom the map here.`);
  });
  $("lot-heat-wrap").addEventListener("pointerleave", () => { const ring = $("heat-ring"); if (ring) ring.setAttribute("r", 0); hideTip(); });
  $("lot-heat-wrap").addEventListener("click", e => {
    const p = heatPointAt(e); if (!p || !M._ct_runs_at(p.lat, p.lon, HEAT_NEAR_M)) return;
    hideTip();
    map.setView([p.lat, p.lon], 15);
    document.querySelector(".mapwrap").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  function refresh() { evaluate(); drawPins(); table(); drawDetail(); drawRun(); renderRunReport(); viewChrome(); renderLot(); }

  // ---------- the two views: chrome around the map and panel ----------
  const CAFE_LEGEND = () => STATUS.map((s, k) => `<span data-tip="status-${k}">${sym(k)}${esc(s.label)}</span>`).join("") +
    `<span class="pen" data-tip="notes">① checker's note</span>`;
  const RUN_LEGEND = () => `<span><i class="runline"></i>this run (arrows: direction; S / F: start, finish)</span>
    <span><b class="badge ok">1</b><b class="badge no">2</b>cafes it passed, in route order: filled accepted, hollow rejected</span>
    <span><i class="faintdot"></i>any other cafe (click to open its cafe view)</span>
    <span>faint purple: other runs (click one to open it)</span>`;
  function viewChrome() {
    const run = state.mode === "run";
    $("mapwrap").classList.toggle("run", run);
    $("view-cafe").setAttribute("aria-selected", !run);
    $("view-run").setAttribute("aria-selected", run);
    $("view-run").setAttribute("aria-disabled", !run && state.lastRun < 0);
    $("view-run-hint").textContent = run ? "· " + runDate(state.run) : state.lastRun >= 0 ? "· last: " + runDate(state.lastRun) : "· click a run";
    $("view-label").textContent = run ? "VIEW B · RUN (PART)" : "VIEW A · CAFE (DATUM A)";
    $("back-run").hidden = run || state.lastRun < 0;
    if (state.lastRun >= 0) $("back-run").textContent = "↩ back to run · " + runDate(state.lastRun);
    if (run) {
      const n = runReport(state.run).length;
      $("view-ctx").textContent = `${runDate(state.run)} · ${km(runLen[state.run])} · ${plural(n, "cafe")} considered`;
    } else if (state.sel >= 0) {
      const r = state.res[state.sel];
      $("view-ctx").textContent = `${cafeName[state.sel]} · ${r.runs} runs · ${STATUS[r.status].label}`;
    }
    setTab(run ? "run" : "cafes");
    $("tab-run-btn").setAttribute("aria-disabled", !run && state.lastRun < 0);
    const want = run ? "run" : "cafe";
    if ($("legend").dataset.view !== want) { $("legend").dataset.view = want; $("legend").innerHTML = run ? RUN_LEGEND() : CAFE_LEGEND(); decorateTips($("legend")); }
  }
  function fitRun(run) {
    const flat = runLines(run).flat();
    if (flat.length) map.fitBounds(L.latLngBounds(flat), { padding: [44, 44], maxZoom: 17, animate: false });
  }
  function fitSelection() { if (state.mode === "run") fitRun(state.run); else if (state.sel >= 0) map.setView(lls[state.sel], 17, { animate: false }); }
  const FitControl = L.Control.extend({
    options: { position: "topleft" },
    onAdd() {
      const box = L.DomUtil.create("div", "leaflet-bar leaflet-control leaflet-control-fit");
      const a = L.DomUtil.create("a", "", box);
      a.href = "#"; a.title = "Fit the selection"; a.setAttribute("role", "button"); a.setAttribute("aria-label", "Fit the selection"); a.textContent = "⤢";
      L.DomEvent.disableClickPropagation(box);
      L.DomEvent.on(a, "click", e => { L.DomEvent.preventDefault(e); fitSelection(); });
      return box;
    },
  });
  new FitControl().addTo(map);

  // ---------- one run: picking, drawing, report (the engine answers; the page draws and words) ----------
  function runReport(run) {
    const k = M._ct_run_report(run);
    return records(M._ct_run_report_data(), k, ["cafe", "d", "prof", "pos", "pro", "along", "street", "qlat", "qlon"])
      .map(w => ({ ...w, pos: !!w.pos, pro: !!w.pro, street: streetName(w.street), q: [w.qlat, w.qlon] }));
  }
  const runLines = run => {
    const out = [];
    for (let k = 0; k < nParts; k++) {
      if (partRun[k] !== run) continue;
      const ll = []; for (let i = partStart[k]; i < partStart[k + 1]; i++) ll.push([pts[2 * i], pts[2 * i + 1]]);
      out.push(ll);
    }
    return out;
  };
  // arrows along the run, oriented on screen (Mercator keeps angles, so they need no redraw on zoom)
  function runArrows(lines) {
    const flat = lines.flat(), seg = [];
    let total = 0;
    for (let i = 1; i < flat.length; i++) {
      const a = flat[i - 1], b = flat[i];
      const dx = (b[1] - a[1]) * kx(a[0]), dy = (b[0] - a[0]) * KY, l = Math.hypot(dx, dy);
      seg.push({ a, dx, dy, l, at: total }); total += l;
    }
    return [0.15, 0.38, 0.62, 0.85].map(f => {
      const s = seg.find(q => q.at + q.l >= f * total && q.l > 0.5);
      return s ? { ll: s.a, deg: Math.atan2(-s.dy, s.dx) * 180 / Math.PI } : null;
    }).filter(Boolean);
  }
  function drawRun() {
    runLayer.clearLayers();
    if (state.hover >= 0 && state.hover !== state.run)  // a run previewed from the picker or the runs table, in either view
      L.polyline(runLines(state.hover), { renderer: runRenderer, color: WHITE, weight: 1.6, dashArray: "5 4", opacity: .9, interactive: false }).addTo(runLayer);
    if (state.mode !== "run") return;
    const lines = runLines(state.run);
    const runCol = css("--run");  // the selected run is orange; a hover preview stays white dashed
    L.polyline(lines, { renderer: runRenderer, color: runCol, weight: 9, opacity: .2, interactive: false }).addTo(runLayer);
    L.polyline(lines, { renderer: runRenderer, color: runCol, weight: 2.8, opacity: 1, interactive: false }).addTo(runLayer);
    runArrows(lines).forEach(a => L.marker(a.ll, { pane: "runs", interactive: false, keyboard: false,
      icon: L.divIcon({ className: "", html: `<div class="run-arrow" style="transform: translate(-4px, -5px) rotate(${a.deg.toFixed(0)}deg)"></div>`, iconSize: [0, 0] }) }).addTo(runLayer));
    // start and finish (a public build has trimmed them, so these are where the shown track begins and ends)
    const flat = lines.flat();
    if (flat.length) {
      const a = flat[0], b = flat[flat.length - 1];
      const same = Math.hypot((b[1] - a[1]) * kx(a[0]), (b[0] - a[0]) * KY) < 30;
      (same ? [[a, "S/F"]] : [[a, "S"], [b, "F"]]).forEach(([ll, t]) => L.marker(ll, { pane: "runs", interactive: false, keyboard: false,
        icon: L.divIcon({ className: "", iconSize: [0, 0], html: `<span class="run-end">${t}</span>` }) }).addTo(runLayer));
    }
    runReport(state.run).forEach(w => L.circleMarker(w.q, { renderer: runRenderer, radius: 3, stroke: false, fillColor: WHITE,
      fillOpacity: 1, interactive: false }).addTo(runLayer));  // each closest approach
  }
  function setTab(t) {
    $("tab-cafes").hidden = t !== "cafes"; $("tab-run").hidden = t !== "run";
    $("tab-cafes-btn").setAttribute("aria-selected", t === "cafes"); $("tab-run-btn").setAttribute("aria-selected", t === "run");
  }
  // Selecting a run always shows the run view, fitted to the whole run.
  function selectRun(run, fit = true) {
    state.mode = "run"; state.run = run; state.lastRun = run; state.hover = -1;
    map.closePopup(); writeHash();
    glow._draw(); drawDetail(); drawPins(); drawRun(); renderRunReport(); viewChrome();
    if (fit) fitRun(run);
  }
  // ✕ and the Cafes tab: back to the cafe view of the cafe you came from
  function clearRun() { if (state.mode === "run") select(state.sel, true); }
  const hoverRun = run => { if (state.hover !== run) { state.hover = run; drawRun(); } };
  // ◀ ▶ step through the runs at the selected cafe when the run is one of them, else through every shown run
  function stepList() {
    const byDate = (a, b) => runDates[a] - runDates[b] || a - b;
    const here = lastDetail && lastDetail.i === state.sel ? lastDetail.d.parts.map(p => p.run) : [];
    if (here.includes(state.run)) return { runs: here.sort(byDate), atCafe: true };
    const all = []; for (let r = 0; r < nRuns; r++) if (state.years.has(runYears[r])) all.push(r);
    return { runs: all.sort(byDate), atCafe: false };
  }

  function renderRunReport() {
    const el = $("run-report");
    if (state.run < 0) {
      el.innerHTML = `<div class="stack-s"><h3>No run selected</h3><p class="muted">Click any run on the map. Where several runs overlap, a list lets you pick one by date.</p>
        <p class="muted">Or select a cafe and click a row under "Runs at this cafe".</p></div>`;
      return;
    }
    const run = state.run, rows = runReport(run), okN = rows.filter(w => w.pos && w.pro).length;
    const best = rows.reduce((m, w) => (!m || w.d < m.d ? w : m), null), worst = rows.reduce((m, w) => (!m || w.prof > m.prof ? w : m), null);
    const steps = stepList();
    const LANES = [24, 2, 46], lastIn = [-99, -99, -99];  // on the line, above, below (px from the top)
    const strip = rows.map((w, k) => { const ok = w.pos && w.pro, left = 100 * w.along / Math.max(1, runLen[run]);
      let lane = LANES.findIndex((_, j) => left - lastIn[j] >= 6);
      if (lane < 0) lane = lastIn.indexOf(Math.min(...lastIn));
      lastIn[lane] = left;
      return `<button type="button"${ok ? ' class="ok"' : ""} data-cafe="${w.cafe}" aria-label="${k + 1}: ${esc(cafeName[w.cafe])}, ${ok ? "accepted" : "rejected"}. Select this cafe"
        style="left:${left.toFixed(1)}%;top:${LANES[lane]}px">${k + 1}</button>`; }).join("");
    let html = `<div class="rr-head"><div class="stack-s"><span class="eyebrow">Part report · run</span><h3>${runDate(run)}</h3>
        <span class="actual">${km(runLen[run])} · run ${run + 1} of ${nRuns}</span></div>
        <div class="rr-nav"><button type="button" data-act="prev" aria-label="Previous run">◀</button><button type="button" data-act="next" aria-label="Next run">▶</button>
        <button type="button" data-act="clear" aria-label="Clear the run">✕</button></div></div>
      <span class="hint">◀ ▶ step through ${steps.atCafe ? `the ${steps.runs.length} runs at ${esc(cafeName[state.sel])}` : "every shown run"}, by date</span>
      <div class="rr-stats"><div><span class="eyebrow">Cafes passed</span><b>${rows.length}</b></div>
        <div><span class="eyebrow">Accepted at</span><b class="yes">${okN}</b></div><div><span class="eyebrow">Rejected at</span><b class="no">${rows.length - okN}</b></div></div>`;
    if (!rows.length) html += `<p class="muted">This run passes no cafe within R = ${state.R} m.</p>`;
    else {
      html += `<div class="actual">best Ø${(2 * best.d).toFixed(0)} at ${esc(cafeName[best.cafe])}<br>worst ⌒ ${worst.prof.toFixed(1)} m at ${esc(cafeName[worst.cafe])}, on ${esc(worst.street)}</div>
        <div class="stack-s"><span class="eyebrow" data-tip="run-strip">Route strip</span>
          <div class="rr-strip"><div class="line"></div><div class="end l"></div><div class="end r"></div>${strip}
            <span class="km l">0 km</span><span class="km r">${km(runLen[run])}</span></div></div>
        <div class="tablewrap"><table>` + thead(["#", "Cafe", "Ø", "⌒", "⌖", "⌒"]) + "<tbody>" +
        rows.map((w, k) => `<tr class="clickable${w.cafe === state.sel ? " sel" : ""}" data-cafe="${w.cafe}"><td class="num ${w.pos && w.pro ? "yes" : "no"}">${k + 1}</td>
          <td><button type="button" class="linkbtn" data-cafe="${w.cafe}">${esc(cafeName[w.cafe])}</button><div class="hint">on ${esc(w.street)} · ${km(w.along)} in</div></td>
          <td class="num">Ø${(2 * w.d).toFixed(0)}</td><td class="num">${w.prof.toFixed(1)}</td><td class="num">${mark(w.pos)}</td><td class="num">${mark(w.pro)}</td></tr>`).join("") +
        `</tbody></table></div>`;
      html += runProfile(run);
    }
    el.innerHTML = html;
    decorateTips(el);
  }
  // ◀ ▶ ✕, and a cafe (a strip mark, a name or its row) selects that cafe
  $("run-report").addEventListener("click", e => {
    const act = e.target.closest("[data-act]"), cafe = e.target.closest("[data-cafe]");
    if (act && act.dataset.act === "clear") clearRun();
    else if (act) {
      const s = stepList().runs, k = s.indexOf(state.run);
      if (s.length) selectRun(s[(k + (act.dataset.act === "next" ? 1 : -1) + s.length) % s.length]);
    } else if (cafe) select(+cafe.dataset.cafe, true);
  });

  // one part's profile against its tolerance zone: the run's offsets along the street at the selected cafe
  function runProfile(run) {
    if (state.sel < 0) return "";
    const k = M._ct_run_profile(state.sel, run);
    if (!k) return "";
    const D = f64(M._ct_run_profile_data(), 2 * k), street = streetName(M._ct_run_profile_street());
    const p = []; for (let j = 0; j < k; j++) p.push([D[2 * j], D[2 * j + 1]]);
    const half = state.band / 2, maxOff = Math.max(...p.map(q => Math.abs(q[1]))), span = Math.max(half * 1.5, maxOff * 1.15, 5);
    const W = 360, H = 132, cy = 66, sy = 54 / span, sx = v => 180 + v / state.R * 160, yv = o => cy - o * sy;
    const line = "M" + p.map(q => `${sx(q[0]).toFixed(1)} ${yv(q[1]).toFixed(1)}`).join(" L");
    const top = p.reduce((m, q) => (Math.abs(q[1]) > Math.abs(m[1]) ? q : m), p[0]);
    const prof = 2 * maxOff, ok = prof <= state.band;
    const row = runReport(run).find(w => w.cafe === state.sel);
    return `<div class="stack-s rr-section">
      <span class="eyebrow" data-tip="run-profile">This run at ${esc(cafeName[state.sel])}</span>
      <svg class="rr-profile" viewBox="0 0 ${W} ${H}" role="img" aria-label="Offset of this run from the ${esc(street)} centreline inside R, against the ${state.band} m band">
        <rect class="bandfill" x="20" y="${yv(half).toFixed(1)}" width="320" height="${(2 * half * sy).toFixed(1)}"/>
        <path class="bandedge" d="M20 ${yv(half).toFixed(1)} H340 M20 ${yv(-half).toFixed(1)} H340"/>
        <path class="nom" d="M20 ${cy} H340"/>
        <path class="datum" d="M180 12 V120"/><text x="184" y="20">A</text>
        <path class="run${ok ? " ok" : ""}" d="${line}"/>
        <path class="lead" d="M${sx(top[0]).toFixed(1)} ${cy} V${yv(top[1]).toFixed(1)}"/>
        <circle cx="${sx(top[0]).toFixed(1)}" cy="${yv(top[1]).toFixed(1)}" r="3.5"/>
        <text class="bd" x="22" y="${(yv(half) - 4).toFixed(1)}">+${half} m</text><text class="bd" x="22" y="${(yv(-half) + 12).toFixed(1)}">−${half} m</text>
        <text x="22" y="128">−${state.R} m</text><text x="338" y="128" text-anchor="end">+${state.R} m along ${esc(street)}</text>
      </svg>
      <span class="actual">⌒ ${prof.toFixed(1)} m on this pass (band ${state.band}) ${ok ? "✓" : "✗"}` +
      (row ? ` · Ø${(2 * row.d).toFixed(0)} (zone Ø${state.tol}) ${row.pos ? "✓" : "✗"}` : "") + `</span>
      <span class="hint">The run's sideways offset along the street inside R, against the profile band. ⌒ actual = 2 × the largest offset.</span></div>`;
  }

  // a click on the map (not on a pin) asks the engine which runs pass within ~10 px of it
  function pickerHtml(ids) {
    const here = lastDetail && lastDetail.i === state.sel ? new Map(lastDetail.d.parts.map(p => [p.run, p])) : new Map();
    return `<div class="pk-title">${ids.length} runs pass here</div><div class="pk-list">` + ids.map(r => {
      const rows = runReport(r), ok = rows.filter(w => w.pos && w.pro).length, p = here.get(r);
      const verdict = p ? (p.pos && p.pro ? `<span class="yes">accepted here</span>` : `<span class="no">rejected here</span>`) : "";
      return `<button type="button" data-run="${r}"><b>${runDate(r)}</b><span class="r">${km(runLen[r])}</span>
        <span class="sub">${rows.length} cafes · accepted at ${ok}</span><span class="r">${verdict}</span></button>`;
    }).join("") + `</div><div class="pk-foot">Hover to highlight · click to inspect</div>`;
  }
  map.on("click", e => {
    const ll = [e.latlng.lat, e.latlng.lng], r = Math.min(80, Math.max(4, 10 / pxPerMetre(ll)));
    const k = M._ct_runs_at(ll[0], ll[1], r), H = f64(M._ct_runs_at_data(), 2 * k), ids = [];
    for (let j = 0; j < k; j++) ids.push(H[2 * j]);
    if (!ids.length) { map.closePopup(); return; }
    if (ids.length === 1) { if (!(state.mode === "run" && ids[0] === state.run)) selectRun(ids[0]); return; }
    ids.sort((a, b) => runDates[a] - runDates[b] || a - b);
    const pop = L.popup({ className: "run-picker", maxWidth: 280, minWidth: 262, autoPanPadding: [24, 24] }).setLatLng(e.latlng)
      .setContent(pickerHtml(ids.slice(0, 60))).openOn(map);
    pop.getElement().querySelectorAll("button[data-run]").forEach(b => {
      const run = +b.dataset.run;
      b.addEventListener("mouseenter", () => hoverRun(run)); b.addEventListener("focus", () => hoverRun(run));
      b.addEventListener("mouseleave", () => hoverRun(-1));
      b.addEventListener("click", ev => { L.DomEvent.stopPropagation(ev); selectRun(run); });
    });
  });
  map.on("popupclose", () => hoverRun(-1));

  // ---------- controls ----------
  SLIDERS.forEach(s => {
    const el = $(s.id), out = $(s.id + "-out");
    const upd = () => { state[s.key] = +el.value; out.textContent = s.fmt(state[s.key]); };
    upd();
    el.addEventListener("input", () => { upd(); if (s.recut) { M._ct_set_radius(state.R); capTol(); } refresh(); });
  });
  // the position zone can't be wider than the search circle: at Ø = 2R every run within R already conforms
  function capTol() {
    const el = $("tol"), max = Math.min(200, 2 * state.R);
    el.max = max;
    if (state.tol > max) { state.tol = max; el.value = max; $("tol-out").textContent = "Ø" + max + " m"; }
  }
  capTol();
  $("years").innerHTML = '<span class="eyebrow" data-tip="years">Years</span>' + years.map(y => `<button type="button" class="chipbtn" data-y="${y}" data-tip="years" aria-pressed="true">${y}</button>`).join("");
  decorateTips($("years"));
  $("years").addEventListener("click", e => {
    const b = e.target.closest("button[data-y]"); if (!b) return;
    const y = +b.dataset.y;
    if (state.years.has(y) && state.years.size > 1) state.years.delete(y); else state.years.add(y);
    $("years").querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", state.years.has(+x.dataset.y)));
    if (state.lastRun >= 0 && !state.years.has(runYears[state.lastRun])) state.lastRun = -1;  // its year is hidden now
    if (state.mode === "run" && !state.years.has(runYears[state.run])) { evaluate(); renderLot(); select(state.sel, true); return; }  // re-score, then back to the cafe view
    glow._draw(); refresh();
  });
  const syncShow = () => { $("show-all").setAttribute("aria-pressed", state.show === "all"); $("show-both").setAttribute("aria-pressed", state.show === "both"); };
  const setShow = mode => {
    state.show = mode; syncShow();
    if (state.mode === "run") { table(); writeHash(); return; }  // the filter is for the cafe view; the run view stays
    if (state.sel >= 0) select(state.sel, false); else { drawPins(); table(); }
  };
  $("show-all").addEventListener("click", () => setShow("all"));
  $("show-both").addEventListener("click", () => setShow("both"));
  $("search").addEventListener("input", e => { state.query = e.target.value; table(); });
  $("cafes").addEventListener("click", e => { const b = e.target.closest("button[data-i]"); if (b) select(+b.dataset.i, true); });
  // a row under "Runs at this cafe" previews its run on hover and opens it on click
  $("detail").addEventListener("mouseover", e => { const tr = e.target.closest("tr[data-run]"); if (tr) hoverRun(+tr.dataset.run); });
  $("detail").addEventListener("mouseout", e => { const tr = e.target.closest("tr[data-run]"); if (tr && !tr.contains(e.relatedTarget)) hoverRun(-1); });
  $("detail").addEventListener("click", e => { const tr = e.target.closest("tr[data-run]"); if (tr) selectRun(+tr.dataset.run); });
  $("annot").addEventListener("change", e => { state.annot = e.target.checked; drawAnnotations(); });
  // the panel tabs and the view bar switch views: Cafes = the cafe view, Run report = the last run's view
  const toCafeView = () => { if (state.mode === "run") clearRun(); };
  const toRunView = () => { if (state.mode !== "run" && state.lastRun >= 0) selectRun(state.lastRun); };
  $("tab-cafes-btn").addEventListener("click", toCafeView);
  $("tab-run-btn").addEventListener("click", toRunView);
  $("view-cafe").addEventListener("click", toCafeView);
  $("view-run").addEventListener("click", toRunView);
  $("back-run").addEventListener("click", toRunView);

  // ---------- start ----------
  M._ct_set_radius(state.R);
  evaluate();
  renderLot();
  syncShow();
  const want = hash.get("cafe");
  const fromHash = want ? cafeOsm.findIndex(o => String(o) === want) : -1;
  if (fromHash >= 0) select(fromHash, true);
  else {
    const best = state.res.filter(r => r.status === 0).sort((a, b) => b.runs - a.runs)[0] || state.res.slice().sort((a, b) => b.runs - a.runs)[0];
    if (best) select(best.i, true); else refresh();
  }
  // The page always opens in the cafe view. A #run=<n> link only offers "back to run" for that run.
  const wantRun = +(hash.get("run") || 0) - 1;
  if (wantRun >= 0 && wantRun < nRuns && state.years.has(runYears[wantRun])) state.lastRun = wantRun;
  renderRunReport(); viewChrome();
})();
