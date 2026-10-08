/* ═══════════════════════════════════════════════════════════════════
   VISHWAS dashboard client.

   It draws two things and computes neither of them:

     * the live mission, streamed frame by frame over a websocket from
       a real simulated swarm - one frame is one tick;
     * the evaluation bundles written by `scripts/run_evaluation.py`,
       read straight from the same JSON the written report quotes.

   Nothing here is smoothed, interpolated or re-derived.  If a number
   on this screen is wrong, the number in the paper is wrong too.
   ═══════════════════════════════════════════════════════════════════ */

const API = "/api/v1";

const el = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (v === null || v === undefined ? "—" : Number(v).toFixed(d));
const pct = (v, d = 0) => (v === null || v === undefined ? "—" : (Number(v) * 100).toFixed(d) + "%");

/* ── state ──────────────────────────────────────────────────────── */

const S = {
  scenarios: [],
  mission: null,     // snapshot
  meta: null,        // fixed geometry + ground truth
  frame: null,       // most recent tick
  trails: new Map(), // drone id -> [[x, y], ...]
  confirmed: new Set(), // target ids the swarm has corroborated
  events: [],
  ws: null,
  retried: false,
};

/* ── colour ─────────────────────────────────────────────────────── */

const CSSVAR = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const PALETTE = {};

function readPalette() {
  for (const k of ["plot", "felt", "rule", "chalk", "dim", "brass", "flare", "verdigris"]) {
    PALETTE[k] = CSSVAR("--" + k);
  }
}

const hex2rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
const rgb2hex = (c) => "#" + c.map((v) => Math.round(v).toString(16).padStart(2, "0")).join("");

function mix(a, b, t) {
  const [x, y] = [hex2rgb(a), hex2rgb(b)];
  return rgb2hex(x.map((v, i) => v + (y[i] - v) * Math.max(0, Math.min(1, t))));
}

/* Suspicion ramp: calm until the swarm starts to doubt you, brass at
   the accusation threshold, vermilion when the doubt is near total. */
function suspicionColour(s, accuse) {
  const knee = accuse || 0.55;
  if (s <= knee) return mix(PALETTE.verdigris, PALETTE.brass, s / knee);
  return mix(PALETTE.brass, PALETTE.flare, (s - knee) / Math.max(1e-6, 1 - knee));
}

/* ── boot ───────────────────────────────────────────────────────── */

async function boot() {
  readPalette();
  wireViews();
  wireOrder();
  wireTransport();
  await loadScenarios();
}

function wireViews() {
  document.querySelectorAll(".views__tab").forEach((tab) => {
    tab.addEventListener("click", () => switchView(tab.dataset.view));
  });
  document.querySelectorAll("[data-view-link]").forEach((btn) => {
    btn.addEventListener("click", () => switchView(btn.dataset.viewLink));
  });
}

function switchView(view) {
  document.querySelectorAll(".views__tab").forEach((t) => t.classList.toggle("is-on", t.dataset.view === view));
  el("view-mission").hidden = view !== "mission";
  el("view-logs").hidden = view !== "logs";
  el("view-results").hidden = view !== "results";
  if (view === "results") loadResults();
  if (view === "logs") renderLogsPage();
}

async function api(path, opts) {
  const res = await fetch(API + path, opts);
  const body = res.status === 204 ? null : await res.json().catch(() => null);
  if (!res.ok) {
    const err = (body && body.error) || { code: "http_" + res.status, message: res.statusText };
    throw Object.assign(new Error(err.message), { code: err.code, details: err.details });
  }
  return body;
}

/* ── scenarios ──────────────────────────────────────────────────── */

async function loadScenarios() {
  try {
    const body = await api("/scenarios");
    S.scenarios = body.data;
    const sel = el("f-scenario");
    sel.innerHTML = S.scenarios.map((s) => `<option value="${s.key}">${s.title}</option>`).join("");
    sel.value = "position_teleport";
    sel.addEventListener("change", noteScenario);
    noteScenario();
    noteMode();
  } catch (exc) {
    showError("Cannot reach the control API — is `uvicorn vishwas.api:app` running? (" + exc.message + ")");
  }
}

function noteScenario() {
  const s = S.scenarios.find((x) => x.key === el("f-scenario").value);
  if (!s) return;
  // The spoofed-anchor scenario is the one place where scenario and assurance
  // mode are coupled: the drone being spoofed *is* the attested one, so
  // without an anchor configured there is nothing to spoof and the run
  // degrades into an ordinary position lie. Saying so beats letting someone
  // demo it in the wrong mode and conclude the mechanism does nothing.
  const needsAnchor = s.key === "spoofed_anchor" && modeOf() !== "anchored";
  el("f-scenario-note").textContent = needsAnchor
    ? s.tests + "  \u2014 needs the hardware-anchor mode: the spoofed drone IS the " +
      "attested one, so without an anchor there is nothing to spoof."
    : s.tests;
  el("f-scenario-note").classList.toggle("field__note--warn", needsAnchor);
  el("f-bad").value = s.n_compromised;
  noteTolerance();
}

/* Every vote this system takes - the trimmed mean and the 2f+1 quorum
   alike - is a majority-vote mechanism at heart, and no majority-vote
   mechanism can tell a compromised minority from an honest one once the
   compromised side stops being a minority.  This mirrors the engine's own
   ``ConsensusEngine._majority_cap``: measured directly (see
   experiments/diag24_few_honest.py), recall and false-exclusion rate both stay
   clean right up to a bare compromised *minority*, then collapse the
   instant it becomes a bare majority - 0 liars caught, the honest drones
   wrongly convicted instead. Surfacing the same bound here, before launch,
   is cheaper than a confusing mission the swarm was mathematically never
   going to get right. */
function modeOf() {
  const sel = el("f-mode");
  return sel ? sel.value : "verified";
}

/* What each assurance mode actually changes, stated in terms of the decision
   rather than the mechanism - somebody watching a demo wants to know what the
   swarm will *do*, not which module is switched on. Numbers here are measured
   over the 216-mission sweep in docs/ENGINEERING_LOG.md §18, not illustrative. */
const MODE_NOTES = {
  vote: "Accusations are counted, not checked. This is the pre-ClaimCheck behaviour every " +
    "study in data/results/ was generated under. Once the compromised drones are the majority " +
    "they out-vote the honest ones: 55 honest drones were wrongly expelled across the sweep.",
  verified: "Every accusation is re-derived from the range measurements the swarm already " +
    "broadcast, with the accuser excluded from its own alibi. A claim the physics refutes stays " +
    "refuted however many drones signed it: 0 honest drones wrongly expelled, at every " +
    "compromise level tested. A gossip-only fabricator is disarmed rather than expelled - its " +
    "claims all fail, but it keeps flying.",
  anchored: "Adds one hardware-attested drone: never drawn as compromised, never voted off, and " +
    "its ranging grounds E2 for everyone else. It is a reference from outside the vote, so it " +
    "settles which account of the geometry is real when the swarm alone cannot - which is what " +
    "lets the fabricator be caught outright instead of merely disarmed (0/9 -> 7/9 at 3 of 9 " +
    "compromised), still with nobody honest convicted.",
};

function noteMode() {
  const note = el("f-mode-note");
  if (note) note.textContent = MODE_NOTES[modeOf()] || "";
}

function noteTolerance() {
  const note = el("f-tolerance-note");
  const n = +el("f-drones").value || 0;
  const bad = +el("f-bad").value || 0;
  const anchored = modeOf() === "anchored";
  if (!n) { note.textContent = "—"; note.classList.remove("field__note--warn"); return; }
  const fTol = Math.floor((n - 1) / 3);
  const majority = Math.floor((n - 1) / 2);
  const anchorNote = anchored
    ? " The certified anchor does not raise this bound - it is not a vote - but it is a reference from " +
      "outside the vote, which is what lets verified evidence exist again past the bound: measured, " +
      "fabricator detection goes from 0/9 caught to 7/9 at 3 of 9 compromised, still with nobody honest " +
      "convicted (docs/ENGINEERING_LOG.md §18)."
    : "";
  if (bad > majority) {
    note.textContent =
      `${bad} of ${n} compromised is a live majority - no vote-based swarm can reliably tell liars ` +
      `from honest drones past this point (expect wrongly-convicted honest drones, not just missed liars).` +
      anchorNote;
    note.classList.add("field__note--warn");
  } else if (bad > fTol) {
    note.textContent =
      `Byzantine bound tolerates f = ${fTol} of ${n} (n ≥ 3f+1) - ${bad} exceeds it, so expect reduced ` +
      `recall on some liars, though the swarm should still not convict anyone honest.` + anchorNote;
    note.classList.add("field__note--warn");
  } else {
    note.textContent = `Byzantine bound: tolerates up to f = ${fTol} of ${n} compromised (n ≥ 3f+1).` + anchorNote;
    note.classList.remove("field__note--warn");
  }
}

/* ── launching ──────────────────────────────────────────────────── */

function wireOrder() {
  el("f-drones").addEventListener("input", noteTolerance);
  el("f-bad").addEventListener("input", noteTolerance);
  el("f-mode").addEventListener("change", () => {
    noteTolerance();
    noteMode();
    noteScenario();   // the spoofed-anchor warning depends on the mode
  });
  el("order").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    showError(null);
    el("f-launch").disabled = true;
    try {
      if (S.mission) await api("/missions/" + S.mission.id, { method: "DELETE" }).catch(() => {});
      const req = {
        scenario: el("f-scenario").value,
        n_drones: +el("f-drones").value,
        n_compromised: +el("f-bad").value,
        max_ticks: +el("f-ticks").value,
        seed: +el("f-seed").value,
        enable_consensus: el("f-consensus").checked,
        enable_trust_priors: el("f-priors").checked,
        speed: +el("t-speed").value,
        enable_claim_verifier: modeOf() !== "vote",
        certified_anchors: modeOf() === "anchored" ? [0] : [],
      };
      const body = await api("/missions", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(req),
      });
      openMission(body.data, body.meta.mission);
    } catch (exc) {
      showError(exc.message + (exc.details ? " — " + exc.details.map((d) => d.field + ": " + d.message).join("; ") : ""));
    } finally {
      el("f-launch").disabled = false;
    }
  });
}

function showError(msg) {
  const box = el("f-error");
  box.hidden = !msg;
  box.textContent = msg || "";
}

function openMission(snapshot, meta) {
  S.mission = snapshot;
  S.meta = meta;
  S.frame = null;
  S.events = [];
  S.trails.clear();
  S.confirmed.clear();
  S.retried = false;
  el("transport").hidden = false;
  el("plot-empty").hidden = true;
  el("tote-rule").textContent = "accuse at " + fmt(meta.thresholds.accuse, 2);
  el("plot-scale").textContent =
    `${meta.area_size} m across · ${meta.sectors.length} sectors · ${meta.targets.length} targets · ` +
    `quorum ${meta.quorum_size} of ${meta.n_drones} · tolerates f = ${meta.f_tolerated}`;
  el("log").innerHTML = '<li class="log__idle">Nothing decided yet.</li>';
  drawStatic();
  drawTote();
  paintSnapshot();
  if (!el("view-logs").hidden) renderLogsPage();
  connect();
}

/* ── stream ─────────────────────────────────────────────────────── */

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}${API}/missions/${S.mission.id}/stream`);
  S.ws = ws;

  ws.onmessage = (msg) => {
    const m = JSON.parse(msg.data);
    if (m.kind === "error") return showError(m.error.message);
    if (m.mission) S.mission = m.mission;

    if (m.kind === "frames") {
      for (const f of m.data) ingest(f);
      if (m.events && m.events.length) {
        logEvents(m.events);
        if (!el("view-logs").hidden) renderLogsPage();
      }
      if (S.frame) { drawFrame(); drawTote(); }
    }
    paintSnapshot();
  };

  ws.onclose = () => {
    if (S.mission && ["running", "paused", "starting"].includes(S.mission.state) && !S.retried) {
      S.retried = true;
      setTimeout(connect, 1000);
    }
  };
}

function ingest(f) {
  S.frame = f;
  for (const d of f.drones) {
    if (!d.alive || d.excluded || !d.pos) continue;
    let t = S.trails.get(d.id);
    if (!t) S.trails.set(d.id, (t = []));
    t.push(d.pos);
    if (t.length > 140) t.shift();
  }
}

/* ── readout ────────────────────────────────────────────────────── */

function paintSnapshot() {
  const m = S.mission, f = S.frame;
  const state = m ? m.state : "idle";
  const pill = el("r-state");
  pill.textContent = m && m.error ? "failed" : state;
  pill.className = "state state--" + (["running", "paused", "finished", "failed"].includes(state) ? state : "idle");

  el("r-tick").textContent = f ? f.tick : "—";
  el("r-clock").textContent = f ? fmt(f.t_s, 1) : "—";
  el("r-phase").textContent = f ? f.phase : "—";
  el("r-cov").textContent = f ? pct(f.coverage) : "—";
  el("r-conf").textContent = f ? `${f.targets_confirmed}/${f.targets_total}` : "—";
  el("r-live").textContent = f ? `${f.drones.filter((d) => d.alive && !d.excluded).length}/${f.drones.length}` : "—";

  // ClaimCheck. Null when the verifier is switched off (configuration D),
  // and the cells say so rather than showing a zero that would read as
  // "nothing was refuted" instead of "nothing was checked".
  const cc = f && f.claimcheck;
  const refuted = cc ? (cc.verdicts.refuted || 0) : null;
  el("r-refuted").textContent = cc ? refuted : f ? "off" : "—";
  el("r-held").textContent = f ? (cc ? (f.quarantined || []).length : "off") : "—";
  el("r-refuted").parentElement.classList.toggle("is-live", !!refuted);
  el("r-held").parentElement.classList.toggle("is-live", !!(f && f.quarantined && f.quarantined.length));

  el("t-pause").textContent = state === "paused" ? "Resume" : "Pause";
  el("t-pause").disabled = !["running", "paused"].includes(state);
  el("t-lag").hidden = !(m && m.lagging && state === "running");
}

function wireTransport() {
  el("t-pause").addEventListener("click", async () => {
    if (!S.mission) return;
    const body = await api("/missions/" + S.mission.id, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ paused: S.mission.state !== "paused" }),
    });
    S.mission = body.data;
    paintSnapshot();
  });

  el("t-abort").addEventListener("click", async () => {
    if (!S.mission) return;
    await api("/missions/" + S.mission.id, { method: "DELETE" }).catch(() => {});
    if (S.ws) S.ws.close();
    S.mission = null; S.meta = null; S.frame = null;
    el("transport").hidden = true;
    el("plot-empty").hidden = false;
    el("plot").innerHTML = "";
    el("tote").innerHTML = "";
    paintSnapshot();
  });

  const speed = el("t-speed");
  speed.addEventListener("input", () => { el("t-speed-v").textContent = speed.value + "×"; });
  speed.addEventListener("change", async () => {
    if (!S.mission) return;
    await api("/missions/" + S.mission.id, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ speed: +speed.value }),
    }).catch(() => {});
  });
}

/* ── plot ───────────────────────────────────────────────────────── */

/* World coordinates are ENU metres with y pointing north; SVG y points
   down, so every point is flipped once, here, and nowhere else. */
const X = (p) => p[0];
const Y = (p) => -p[1];

function drawStatic() {
  const A = S.meta.area_size, h = A / 2;
  const pad = A * 0.06;
  const svg = el("plot");
  svg.setAttribute("viewBox", `${-h - pad} ${-h - pad} ${A + 2 * pad} ${A + 2 * pad}`);

  const sectors = S.meta.sectors
    .map((s) => `<rect class="sector" x="${s.x0}" y="${-s.y1}" width="${s.x1 - s.x0}" height="${s.y1 - s.y0}"/>`)
    .join("");

  const targets = S.meta.targets
    .map((t) => {
      const [x, y] = [X(t.position), Y(t.position)];
      return `<g class="target-g" data-id="${t.id}">
        <circle class="target" cx="${x}" cy="${y}" r="16"/>
        <text class="tag tag--dim" x="${x + 24}" y="${y + 7}">${t.id}</text></g>`;
    })
    .join("");

  svg.innerHTML =
    `<g id="static">
       <rect class="box" x="${-h}" y="${-h}" width="${A}" height="${A}"/>
       ${sectors}
       <g id="targets">${targets}</g>
       <path class="home" d="M -22 0 H 22 M 0 -22 V 22"/>
     </g>
     <g id="live"></g>`;
}

function drawFrame() {
  const f = S.frame;
  const accuse = S.meta.thresholds.accuse;
  const bad = new Set(S.meta.compromised);
  const parts = [];

  // trails first, so live markers sit on top of their own history
  for (const [id, pts] of S.trails) {
    if (pts.length < 2) continue;
    const d = f.drones.find((x) => x.id === id);
    const c = d && !d.excluded ? suspicionColour(d.suspicion, accuse) : PALETTE.dim;
    parts.push(`<polyline class="trail" stroke="${c}" points="${pts.map((p) => `${X(p)},${Y(p)}`).join(" ")}"/>`);
  }

  for (const d of f.drones) {
    // A drone out of battery stops being reported at all; it has no
    // position to draw and the recorder is right not to invent one.
    if (!d.pos) continue;
    const [x, y] = [X(d.pos), Y(d.pos)];

    if (d.excluded) {
      parts.push(
        `<g><circle class="drone--out" cx="${x}" cy="${y}" r="13"/>
          <path class="strike" d="M ${x - 10} ${y - 10} L ${x + 10} ${y + 10} M ${x + 10} ${y - 10} L ${x - 10} ${y + 10}"/>
          <text class="tag tag--dim" x="${x + 20}" y="${y - 14}">${d.id}</text></g>`
      );
      continue;
    }
    if (!d.alive) continue;

    // Held, not expelled: the weighted quorum wanted this drone gone and
    // ClaimCheck would not certify the accusations. Deliberately drawn
    // unlike an exclusion - the drone is still flying and still counted,
    // and rendering a refusal to convict the same way as a conviction
    // would undo the distinction the whole mechanism exists to make.
    if (d.quarantined) {
      parts.push(
        `<circle cx="${x}" cy="${y}" r="22" fill="none" stroke="${PALETTE.flare}"
          stroke-width="2" stroke-dasharray="2 6" opacity="0.85"/>`
      );
    }

    // the lie, drawn from the answer key: claimed position vs the real one
    if (d.claimed) {
      const cx = X(d.claimed), cy = Y(d.claimed);
      if (Math.hypot(cx - x, cy - y) > 6) {
        parts.push(`<line class="tether" x1="${x}" y1="${y}" x2="${cx}" y2="${cy}"/>`);
        parts.push(`<circle class="ghost" cx="${cx}" cy="${cy}" r="11"/>`);
      }
    }

    const fill = suspicionColour(d.suspicion, accuse);
    const ring = bad.has(d.id)
      ? `<circle cx="${x}" cy="${y}" r="19" fill="none" stroke="${PALETTE.flare}" stroke-width="2" stroke-dasharray="4 5" opacity="0.9"/>`
      : "";
    const accusers = d.accusers
      ? `<text class="tag" x="${x + 18}" y="${y + 24}" font-size="15" fill="${PALETTE.flare}">${d.accusers}✦</text>`
      : "";

    parts.push(
      `<g>${ring}<circle class="drone" cx="${x}" cy="${y}" r="12" fill="${fill}"/>
        <text class="tag" x="${x + 18}" y="${y - 12}">${d.id}</text>${accusers}</g>`
    );
  }

  document.getElementById("live").innerHTML = parts.join("");

  // Targets light up by identity, from the confirmation events - not by
  // counting off the first N, which would put the mark in the wrong place.
  document.querySelectorAll("#targets .target-g").forEach((g) => {
    g.querySelector(".target").classList.toggle("target--found", S.confirmed.has(g.dataset.id));
  });
}

/* ── tote ───────────────────────────────────────────────────────── */

function drawTote() {
  const tote = el("tote");
  const f = S.frame;
  const accuse = S.meta.thresholds.accuse;
  const bad = new Set(S.meta.compromised);

  if (!f) {
    tote.innerHTML = Array.from({ length: S.meta.n_drones }, (_, i) =>
      `<div class="col ${bad.has(i) ? "is-bad" : ""}">
         <div class="col__tube"></div><div class="col__id">${i}</div><div class="col__chan">—</div></div>`
    ).join("");
    return;
  }

  tote.innerHTML = f.drones
    .map((d) => {
      const s = Math.max(0, Math.min(1, d.suspicion));
      const colour = suspicionColour(s, accuse);
      const cls = ["col", bad.has(d.id) ? "is-bad" : "", d.excluded ? "is-out" : "", d.quarantined ? "is-held" : ""].join(" ");
      return `<div class="${cls}" title="drone ${d.id} · suspicion ${fmt(s, 3)} · credibility ${fmt(d.weight, 2)}">
        <div class="col__tube">
          <div class="col__rule" style="top:${(1 - accuse) * 100}%"></div>
          <div class="col__fill" style="height:${s * 100}%;background:${colour}"></div>
        </div>
        <div class="col__id">${d.id}</div>
        <div class="col__chan">${d.dominant === "none" ? "·" : d.dominant}</div>
      </div>`;
    })
    .join("");
}

/* ── decision log ───────────────────────────────────────────────── */

function logEvents(events) {
  const log = el("log");
  if (!S.events.length) log.innerHTML = "";
  for (const e of events) {
    S.events.push(e);
    if (e.kind === "target_confirmed") S.confirmed.add(e.target_id);
    log.prepend(renderEvent(e));
  }
  while (log.children.length > 60) log.removeChild(log.lastChild);
}

function renderEvent(e) {
  const li = document.createElement("li");
  const t = fmt(e.tick * S.meta.dt, 1) + "s";

  if (e.kind === "exclusion") {
    const wrong = e.honest === true;
    const byFabrication = e.reason === "fabrication_breadth";
    const sub = byFabrication
      ? `caught fabricating accusations · breadth ${fmt(e.weight, 1)}`
      : `${e.accusers.length} accusers · weight ${fmt(e.weight, 2)} of ${e.quorum_required} required`;
    li.className = "entry " + (wrong ? "entry--false" : "entry--caught");
    li.innerHTML =
      `<span class="entry__t">${t}</span><span>
         <span class="entry__head">Drone <b>${e.target}</b> expelled —
           <b class="${wrong ? "wrong" : "caught"}">${wrong ? "honest, wrongly convicted" : "the liar"}</b></span>
         <span class="entry__sub">${sub}</span>
       </span>`;
  } else if (e.kind === "claimcheck_block") {
    // The headline event: a majority agreed and the physics said no.
    const saved = e.honest === true;
    li.className = "entry " + (saved ? "entry--blocked" : "entry--held");
    const sub = e.refuted && e.refuted.length
      ? `${e.refuted.length} accusations refuted by independent ranging · ${e.vote_accusers.length} had voted to expel`
      : `${e.supported.length} of ${e.vote_accusers.length} accusations verified · ${e.quorum_required} required`;
    li.innerHTML =
      `<span class="entry__t">${t}</span><span>
         <span class="entry__head">Drone <b>${e.target}</b> held, not expelled —
           <b class="${saved ? "caught" : "wrong"}">${saved ? "honest, protected" : "compromised, unverifiable"}</b></span>
         <span class="entry__sub">${sub}</span>
       </span>`;
  } else if (e.kind === "target_confirmed") {
    li.className = "entry entry--target";
    li.innerHTML =
      `<span class="entry__t">${t}</span><span>
         <span class="entry__head">Target <b>${e.target_id}</b> confirmed</span>
         <span class="entry__sub">seen independently by enough trusted drones</span></span>`;
  } else {
    li.className = "entry";
    li.innerHTML = `<span class="entry__t">${t}</span><span>${e.kind}</span>`;
  }
  return li;
}

/* ── full logs page ─────────────────────────────────────────────────
   Same event stream as the Plot tab's small "Decisions" panel (S.events
   is shared, never re-fetched or re-derived), just uncapped and with the
   full vote-math/evidence trail unfolded for each exclusion.  This is the
   page built to answer "how did the swarm know" - every number on it
   comes straight off the ExclusionEvent the consensus engine produced,
   via vishwas/api/explain.py's narrative and the per-accuser evidence
   snapshot Swarm._handle_exclusion records before forgetting the target.
   ═══════════════════════════════════════════════════════════════════ */

function renderLogsPage() {
  const body = el("logs-body");
  if (!S.mission || !S.meta) {
    body.innerHTML =
      '<p class="sheet__idle">No mission open yet. Launch one from the Plot tab, then come back here.</p>';
    return;
  }
  const meta = S.meta, mission = S.mission;
  const compromised = meta.compromised || [];
  const exclusions = S.events.filter((e) => e.kind === "exclusion");
  const caught = exclusions.filter((e) => e.honest === false);
  const wrong = exclusions.filter((e) => e.honest === true);
  const groundTruth = compromised.length
    ? compromised.map((id) => "drone " + id).join(", ")
    : "none — honest control run";
  const caughtClass = !compromised.length ? "" : caught.length === compromised.length ? "fig__v--good" : "fig__v--warn";

  const figs = `
    <div class="head-figs">
      <div class="fig">
        <span class="fig__v ${caughtClass}">${caught.length}/${compromised.length}</span>
        <span class="fig__k">Liars caught</span>
        <span class="fig__note">ground truth: ${groundTruth}</span>
      </div>
      <div class="fig">
        <span class="fig__v ${wrong.length ? "fig__v--warn" : "fig__v--good"}">${wrong.length}</span>
        <span class="fig__k">Wrong exclusions</span>
        <span class="fig__note">honest drones mistakenly expelled</span>
      </div>
      <div class="fig">
        <span class="fig__v">${exclusions.length}</span>
        <span class="fig__k">Total exclusions</span>
        <span class="fig__note">${S.events.length} events logged this mission</span>
      </div>
      <div class="fig">
        <span class="fig__v">${mission.tick || 0}</span>
        <span class="fig__k">Ticks elapsed</span>
        <span class="fig__note">of ${meta.max_ticks} · scenario: ${meta.scenario} · state: ${mission.state}</span>
      </div>
    </div>`;

  const rows = S.events.length
    ? S.events.slice().reverse().map(renderLogEntry).join("")
    : '<li class="log__idle">Nothing decided yet.</li>';

  body.innerHTML = `
    <div class="study">
      <h2 class="panel__title">Mission verdict</h2>
      <p class="study__note">The swarm's decisions, scored against the simulator's own answer key. No part of
        VISHWAS reads the ground-truth field above while flying - it is used only here, after the fact, to
        grade the outcome.</p>
      ${figs}
    </div>
    <div class="study">
      <h2 class="panel__title">Full event log</h2>
      <p class="study__note">Every decision the swarm made, in order. Expand an exclusion for the full vote
        math and evidence trail behind it - the same explanation you can quote directly.</p>
      <ol class="log log--full">${rows}</ol>
    </div>`;
}

function renderLogEntry(e) {
  const t = fmt(e.tick * S.meta.dt, 1) + "s (tick " + e.tick + ")";
  if (e.kind === "exclusion") {
    const wrong = e.honest === true;
    const byFabrication = e.reason === "fabrication_breadth";
    const reasonLabel = byFabrication ? "caught by the fabrication detector" : "caught by trust-weighted quorum vote";
    const sub = byFabrication
      ? `${reasonLabel} · breadth score ${fmt(e.weight, 1)}`
      : `${reasonLabel} · ${e.accusers.length} accusers · weight ${fmt(e.weight, 2)} of ${e.quorum_required} required`;
    const evidenceRows = (e.evidence || [])
      .map(
        (ev) => `
        <tr>
          <td>drone ${ev.accuser}</td>
          <td>${fmt(ev.credibility, 2)}</td>
          <td>${ev.dominant}</td>
          <td>${fmt(ev.z[0], 2)} / ${fmt(ev.z[1], 2)} / ${fmt(ev.z[2], 2)}</td>
          <td>${ev.offset_m != null ? fmt(ev.offset_m, 1) + " m" : "—"}</td>
        </tr>`
      )
      .join("");
    return `
      <li class="entry entry--wide ${wrong ? "entry--false" : "entry--caught"}">
        <details>
          <summary>
            <span class="entry__t">${t}</span>
            <span>
              <span class="entry__head">Drone <b>${e.target}</b> expelled —
                <b class="${wrong ? "wrong" : "caught"}">${wrong ? "honest, wrongly convicted" : "the liar"}</b></span>
              <span class="entry__sub">${sub}</span>
            </span>
            <span class="entry__caret">▾</span>
          </summary>
          <div class="explain">
            <p class="explain__text">${e.explanation || "No explanation recorded."}</p>
            ${
              evidenceRows
                ? `<table class="explain__table">
                <thead><tr><th>Accuser</th><th>Credibility</th><th>Dominant</th><th>z (E1 / E2 / E3)</th><th>Offset</th></tr></thead>
                <tbody>${evidenceRows}</tbody>
              </table>`
                : ""
            }
          </div>
        </details>
      </li>`;
  }
  if (e.kind === "target_confirmed") {
    return `<li class="entry entry--target"><span class="entry__t">${t}</span><span>
        <span class="entry__head">Target <b>${e.target_id}</b> confirmed</span>
        <span class="entry__sub">seen independently by enough trusted drones</span></span></li>`;
  }
  return `<li class="entry"><span class="entry__t">${t}</span><span>${e.kind}</span></li>`;
}

/* ── evaluation sheet ───────────────────────────────────────────────
   Status colour is reserved for the four columns that answer "did the
   swarm do its job": recall, clearance_rate and mission_success_rate
   (higher is better) and false_exclusion_rate (the one column where
   *any* nonzero value is a fail - see the scenarios bundle's own notes:
   "the honest control row ... must show a false-exclusion rate of
   zero"). Everything else (counts, latency, coverage) stays plain -
   colour marks a verdict, not just "this is a number". The three
   colours reuse the exact vocabulary the Plot view's own legend
   already teaches (.key__dot--calm/warm/hot == good/warn/bad here). */

const COLUMNS = {
  scenarios: [
    ["scenario", "Scenario"], ["n_runs", "Runs"], ["attackers_total", "Attackers"],
    ["attackers_caught", "Caught"], ["recall", "Recall"], ["detection_latency_s_mean", "Latency (s)"],
    ["detection_latency_s_p90", "p90 (s)"], ["false_exclusion_rate", "False excl."],
    ["coverage_mean", "Coverage"], ["mission_success_rate", "Mission success"],
  ],
  degradation: [
    ["n_compromised", "Compromised"], ["recall", "Recall"], ["clearance_rate", "Fully cleared"],
    ["false_exclusion_rate", "False excl."], ["coverage_mean", "Coverage"], ["mission_success_rate", "Mission success"],
  ],
  cusum: [
    ["overrides.cusum_threshold_h", "Threshold h"], ["scenario", "Scenario"], ["recall", "Recall"],
    ["detection_latency_s_mean", "Latency (s)"], ["false_exclusion_rate", "False excl."],
  ],
};

const RATE_KEYS = new Set([
  "recall", "false_exclusion_rate", "coverage_mean", "mission_success_rate", "clearance_rate",
]);
// Direction each rate column needs to move to be "good" - only these get a
// status colour. false_exclusion_rate is inverted: 0 is the pass bar.
const GOOD_HIGH = new Set(["recall", "clearance_rate", "mission_success_rate"]);
const GOOD_LOW = new Set(["false_exclusion_rate"]);
// Columns that are counts of things, not rates or measurements - always
// plain integers, never the "24" vs "25.60" jitter a generic formatter
// produces on a mixed int/float column.
const INT_KEYS = new Set(["n_runs", "attackers_total", "attackers_caught", "n_compromised"]);
// Measurement columns (not rates, not counts) - always a fixed decimal
// count, so a column never mixes "24" with "25.60".
const FIXED_KEYS = { detection_latency_s_mean: 1, detection_latency_s_p90: 1, "overrides.cusum_threshold_h": 2 };

let resultsLoaded = false;

async function loadResults() {
  if (resultsLoaded) return;
  const body = el("results-body");
  try {
    const index = (await api("/results")).data;
    const bundles = await Promise.all(index.bundles.map((b) => api("/results/" + b.name).then((r) => r.data)));
    const byName = Object.fromEntries(bundles.map((b) => [b.name, b]));
    const renderers = { ablation: renderAblation, degradation: renderDegradation };
    body.innerHTML =
      renderHeadline(index.headline) +
      bundles.map((b) => (renderers[b.name] || renderBundle)(b)).join("");
    resultsLoaded = true;
  } catch (exc) {
    body.innerHTML =
      `<p class="sheet__idle">No evaluation bundles on disk yet.</p>
       <p class="sheet__idle">Run <b>python scripts/run_evaluation.py --quick</b> for a two-minute smoke
       sweep, or <b>--full</b> for the figures that go in the paper. This page reads exactly the
       JSON those runs write — it never recomputes anything.</p>
       <p class="sheet__idle">(${exc.message})</p>`;
  }
}

function statusClass(key, v) {
  if (typeof v !== "number") return "";
  if (GOOD_LOW.has(key)) return v <= 0 ? "stat--good" : "stat--bad";
  if (GOOD_HIGH.has(key)) return v >= 0.999 ? "stat--good" : v <= 0.001 ? "stat--bad" : "stat--warn";
  return "";
}

function renderHeadline(h) {
  if (!h) return "";
  const figs = [
    ["fig__v--good", pct(h.recall, 0), "Attackers expelled", `${h.attackers_caught} of ${h.attackers_total} across ${h.scenarios_evaluated} scenarios`],
    ["", fmt(h.mean_detection_latency_s, 1) + "s", "Mean detection latency", `worst scenario mean ${fmt(h.worst_detection_latency_s, 1)}s`],
    [h.false_exclusion_rate > 0 ? "fig__v--warn" : "fig__v--good", pct(h.false_exclusion_rate, 1), "False-exclusion rate", `over ${h.honest_drones_flown} honest drone-missions`],
    ["", pct(h.mission_success_under_attack, 0), "Mission success under attack", "search completed despite the liar"],
    ["", fmt(h.ms_per_drone_tick, 2), "ms / drone / tick", "single-core Python, no GPU"],
  ];
  return `<section class="study">
    <h2 class="panel__title">Headline</h2>
    <div class="head-figs">${figs
      .map(([cls, v, k, note]) => `<div class="fig"><span class="fig__v ${cls}">${v}</span>
        <span class="fig__k">${k}</span><span class="fig__note">${note}</span></div>`)
      .join("")}</div>
    <p class="study__note">Every figure is pulled from the result bundles, so the slide cannot drift from the experiment.</p>
  </section>`;
}

// A run with no attacker in it (the "none" honest-control row, or
// n_compromised === 0) is structurally different from an attack result -
// mostly dashes, nothing to catch. Dim it rather than let it read as a
// broken/empty data row sitting among real ones.
function isBaseline(row) {
  return row.scenario === "none" || row.n_compromised === 0;
}

function dataTable(b, cols) {
  return `<table><thead><tr>${cols.map(([, h]) => `<th>${h}</th>`).join("")}</tr></thead>
      <tbody>${b.table
        .map(
          (r) =>
            `<tr class="${isBaseline(r) ? "row--baseline" : ""}">${cols
              .map(([k]) => `<td class="${statusClass(k, r[k])}">${cell(r, k)}</td>`)
              .join("")}</tr>`
        )
        .join("")}</tbody>
    </table>
    <p class="fig__note">${b.n_runs} runs${b.n_failed ? ` · ${b.n_failed} failed` : ""}</p>`;
}

function renderBundle(b) {
  const cols = COLUMNS[b.name] || Object.keys(b.table[0] || {}).map((k) => [k, k]);
  return `<section class="study">
    <h2 class="panel__title">${b.title}</h2>
    <p class="study__note">${(b.notes || "").trim()}</p>
    ${dataTable(b, cols)}
  </section>`;
}

// Three-configuration ablation, pivoted: one row per scenario with A/B/C
// side by side for the two metrics the study's own notes call out as the
// headline comparison (recall = "did voting help catch it", false-excl =
// "did voting cost an innocent drone" - the deployability bar). The raw
// bundle is config-major (7 scenario rows × 3 configs = 21 near-identical
// rows) which is the right shape for the JSON but the wrong shape to
// *read*; grouping by scenario is what the data is actually for.
function renderAblation(b) {
  const byScenario = new Map();
  for (const r of b.table) {
    if (!byScenario.has(r.scenario)) byScenario.set(r.scenario, {});
    byScenario.get(r.scenario)[r.config_label] = r;
  }
  const configs = ["A", "B", "C"];
  const rows = [...byScenario.entries()]
    .map(([scenario, byConfig]) => {
      const base = isBaseline({ scenario }) ? "row--baseline" : "";
      const recall = configs
        .map((c) => {
          const v = byConfig[c]?.recall;
          return `<td class="${statusClass("recall", v)}">${cell(byConfig[c] || {}, "recall")}</td>`;
        })
        .join("");
      const excl = configs
        .map((c) => {
          const v = byConfig[c]?.false_exclusion_rate;
          return `<td class="${statusClass("false_exclusion_rate", v)}">${cell(byConfig[c] || {}, "false_exclusion_rate")}</td>`;
        })
        .join("");
      return `<tr class="${base}"><td>${scenario}</td>${recall}${excl}</tr>`;
    })
    .join("");
  return `<section class="study">
    <h2 class="panel__title">${b.title}</h2>
    <p class="study__note">${(b.notes || "").trim()}</p>
    <table>
      <thead>
        <tr><th rowspan="2">Scenario</th><th colspan="3" class="th-group">Recall</th><th colspan="3" class="th-group">False excl.</th></tr>
        <tr>${configs.map((c) => `<th class="th-sub">${c}</th>`).join("")}${configs.map((c) => `<th class="th-sub">${c}</th>`).join("")}</tr>
      </thead>
      <tbody>${rows}</tbody>
    </table>
    <p class="fig__note">A: no voting · B: consensus, uniform trust · C: full VISHWAS · ${b.n_runs} runs${b.n_failed ? ` · ${b.n_failed} failed` : ""}</p>
  </section>`;
}

// Graceful-degradation curve: this is trend data (recall/false-excl as a
// function of how many drones are compromised), so it gets a chart above
// the table instead of asking the reader to reconstruct the shape from a
// column of numbers - the table stays underneath for exact values.
function renderDegradation(b) {
  const cols = COLUMNS[b.name];
  const rows = b.table.filter((r) => r.n_compromised !== null && r.n_compromised !== undefined);
  const chart = lineChart(rows, "n_compromised", [
    ["recall", "Recall", "var(--verdigris)"],
    ["false_exclusion_rate", "False excl.", "var(--flare)"],
  ]);
  return `<section class="study">
    <h2 class="panel__title">${b.title}</h2>
    <p class="study__note">${(b.notes || "").trim()}</p>
    ${chart}
    ${dataTable(b, cols)}
  </section>`;
}

// Minimal inline line chart, drawn the same way the mission plot is (raw
// SVG string, no library). Not interactive - point values are already in
// the table right below it, so this only needs to show the shape.
function lineChart(rows, xKey, series) {
  const W = 760, H = 170, padL = 34, padR = 12, padT = 10, padB = 22;
  const xs = rows.map((r) => r[xKey]);
  const xMin = Math.min(...xs), xMax = Math.max(...xs) || 1;
  const X = (x) => padL + ((x - xMin) / (xMax - xMin || 1)) * (W - padL - padR);
  const Y = (v) => padT + (1 - (v ?? 0)) * (H - padT - padB);

  const grid = [0, 0.25, 0.5, 0.75, 1]
    .map(
      (t) =>
        `<line x1="${padL}" x2="${W - padR}" y1="${Y(t)}" y2="${Y(t)}" class="chart__grid"/>
         <text x="${padL - 6}" y="${Y(t) + 3}" class="chart__axis" text-anchor="end">${Math.round(t * 100)}%</text>`
    )
    .join("");
  const xTicks = rows
    .map((r) => `<text x="${X(r[xKey])}" y="${H - 4}" class="chart__axis" text-anchor="middle">${r[xKey]}</text>`)
    .join("");

  const lines = series
    .map(([key, label, color]) => {
      const pts = rows.filter((r) => typeof r[key] === "number").map((r) => `${X(r[xKey])},${Y(r[key])}`);
      if (pts.length === 0) return "";
      const dots = rows
        .filter((r) => typeof r[key] === "number")
        .map((r) => `<circle cx="${X(r[xKey])}" cy="${Y(r[key])}" r="3" fill="${color}"/>`)
        .join("");
      return `<polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="2"/>${dots}`;
    })
    .join("");

  const legend = series
    .map(([, label, color]) => `<span class="chart__legend-item"><i style="background:${color}"></i>${label}</span>`)
    .join("");

  return `<div class="chart-block">
    <div class="chart__legend">${legend}</div>
    <svg viewBox="0 0 ${W} ${H}" class="chart" role="img" aria-label="Recall and false-exclusion rate vs. number of compromised drones">
      ${grid}${xTicks}${lines}
    </svg>
    <p class="chart__caption">Compromised drones swept from zero past the Byzantine bound f = (n-1)/3 (x-axis) against recall and false-exclusion rate (y-axis).</p>
  </div>`;
}

function cell(row, key) {
  let v = key in row ? row[key] : row.overrides && row.overrides[key.split(".").pop()];
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (typeof v !== "number") return String(v);
  if (RATE_KEYS.has(key)) return pct(v, 1);
  if (INT_KEYS.has(key)) return String(Math.round(v));
  if (key in FIXED_KEYS) return fmt(v, FIXED_KEYS[key]);
  return Number.isInteger(v) ? String(v) : fmt(v, 2);
}

boot();
