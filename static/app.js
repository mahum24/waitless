const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const SAMPLES = {
  knee: {
    referral: "Dear Orthopaedic Team, I would be grateful if you could see this 67-year-old lady with severe right knee osteoarthritis. X-ray shows bone-on-bone medial compartment. Pain is limiting her walking to 100 yards despite analgesia and physiotherapy. She would like to discuss total knee replacement. Routine referral.",
    postcode: "LE1 5WW",
    context: "I don't drive, my daughter can take me on Fridays",
  },
  cataract: {
    referral: "My optician says I have a cataract in my left eye and my GP is referring me to have it removed. My vision is cloudy and driving at night is getting difficult.",
    postcode: "TR1 3LJ",
    context: "",
  },
  gynae: {
    referral: "Referral to gynaecology: 29-year-old with suspected endometriosis, cyclical pelvic pain for 3 years, not controlled with hormonal treatment. Requesting assessment and consideration of diagnostic laparoscopy. Routine.",
    postcode: "M13 9WL",
    context: "I work full time so I'd prefer somewhere near a train station",
  },
};

let map, layer, started;

fetch("/api/meta").then((r) => r.json()).then((m) => {
  $("#meta").textContent = `${m.providers} providers · NHS England ${m.period.replace("RTT-", "").replace("-", " ")}`;
  $("#period").textContent = m.period.replace("RTT-", "").replace("-", " ");
});

document.querySelectorAll("[data-sample]").forEach((b) => b.addEventListener("click", () => {
  const s = SAMPLES[b.dataset.sample];
  $("#referral").value = s.referral; $("#postcode").value = s.postcode; $("#context").value = s.context;
}));
let visionUsage = null;
async function readLetter(file) {
  const btn = $(".upload-btn"), li = document.querySelector('#steps li[data-step="vision"]');
  btn.classList.add("busy"); btn.querySelector("b").textContent = "Reading your letter…";
  li.className = "optional running"; li.querySelector(".t")?.remove();
  const t0 = performance.now();
  try {
    const fd = new FormData(); fd.append("file", file);
    const res = await fetch("/api/read-letter", { method: "POST", body: fd });
    if (!res.ok) throw new Error((await res.json()).detail || "Couldn't read that file");
    const out = await res.json();
    $("#referral").value = out.text;
    visionUsage = out.usage;
    li.className = "optional done";
    const t = document.createElement("span"); t.className = "t";
    t.textContent = `${((performance.now() - t0) / 1000).toFixed(1)}s`; li.appendChild(t);
    if (!$("#postcode").value) $("#postcode").focus();
  } catch (err) { li.className = "optional error"; showError(err.message); }
  btn.classList.remove("busy"); btn.querySelector("b").textContent = "Upload your referral letter";
}
$("#file").addEventListener("change", (e) => e.target.files[0] && readLetter(e.target.files[0]));
$("#sampleLetter").addEventListener("click", async () => {
  const blob = await (await fetch("/static/samples/referral-knee.jpg")).blob();
  $("#postcode").value = "LE1 5WW"; $("#context").value = "My daughter can drive me on Fridays";
  readLetter(new File([blob], "referral-knee.jpg", { type: "image/jpeg" }));
});

$("#miles").addEventListener("input", (e) => ($("#milesOut").textContent = `${e.target.value} miles`));

$("#form").addEventListener("submit", async (e) => {
  e.preventDefault();
  reset();
  $("#go").disabled = true; $("#go").textContent = "Working…";
  started = performance.now();
  try {
    const res = await fetch("/api/analyze", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ referral: $("#referral").value, postcode: $("#postcode").value,
        max_miles: +$("#miles").value, context: $("#context").value }),
    });
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).replace(/^data: /, ""); buf = buf.slice(i + 2);
        if (line.trim()) handle(JSON.parse(line));
      }
    }
  } catch (err) { showError(err.message); }
  $("#go").disabled = false; $("#go").textContent = "Find shorter waits";
});

const state = {};
function reset() {
  Object.keys(state).forEach((k) => delete state[k]);
  document.querySelectorAll("#steps li:not(.optional)").forEach((li) => { li.className = ""; li.querySelector(".t")?.remove(); });
  ["#error", "#notice", "#results", "#routing"].forEach((s) => $(s).classList.add("hidden"));
}

function handle(ev) {
  const li = document.querySelector(`#steps li[data-step="${ev.step}"]:not(.optional)`);
  if (li) {
    li.className = ev.status;
    if (ev.status === "running") li.dataset.t0 = performance.now();
    if (ev.status === "done") {
      const t = document.createElement("span"); t.className = "t";
      t.textContent = `${((performance.now() - li.dataset.t0) / 1000).toFixed(1)}s`; li.appendChild(t);
    }
  }
  if (ev.status === "error") return showError(ev.message);
  if (ev.status !== "done") return;
  state[ev.step] = ev.result;
  if (ev.step === "locate" && !ev.result.eligibility.eligible) {
    $("#notice").innerHTML = "<b>Heads up:</b> " + ev.result.eligibility.reasons.map(esc).join(" ");
    $("#notice").classList.remove("hidden");
  }
  if (ev.step === "rank") renderMap();
  if (ev.step === "complete") render(ev.result);
}

function showError(msg) { $("#error").textContent = msg; $("#error").classList.remove("hidden"); }

function renderMap() {
  $("#results").classList.remove("hidden");
  const loc = state.locate.location, { options, baseline } = state.rank;
  if (!map) {
    map = L.map("map", { scrollWheelZoom: false });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap" }).addTo(map);
  }
  if (layer) layer.remove();
  layer = L.featureGroup().addTo(map);
  L.circleMarker([loc.lat, loc.lon], { radius: 9, color: "#14201c", fillColor: "#14201c", fillOpacity: 1 })
    .bindTooltip("You").addTo(layer);
  const maxW = Math.max(...options.map((o) => o.median_weeks), baseline?.median_weeks || 0);
  options.forEach((o) => {
    const t = o.median_weeks / maxW, color = t < .4 ? "#0b9b6f" : t < .7 ? "#e0a100" : "#d1495b";
    L.circleMarker([o.lat, o.lon], { radius: 7, color, fillColor: color, fillOpacity: .85, weight: 1 })
      .bindTooltip(`${esc(o.name)}<br>${o.median_weeks} wks · ${o.miles} mi`).addTo(layer);
  });
  map.fitBounds(layer.getBounds().pad(0.15));
  setTimeout(() => map.invalidateSize(), 50);
  $("#allCount").textContent = options.length;
  $("#all tbody").innerHTML = options.map((o) => `<tr><td>${esc(o.name)}<br><small>${esc(o.town)}</small></td><td>${o.miles}</td><td>${o.median_weeks} wks</td><td>${o.p92_weeks} wks</td><td>${o.pct_within_18}%</td></tr>`).join("");
}

function render(done) {
  const d = state.decide, base = state.rank.baseline, rec = done.recommended;
  const saved = base ? Math.max(0, base.median_weeks - rec.median_weeks) : null;
  $("#saved").innerHTML = saved != null ? `${Math.floor(saved)}<small>weeks sooner (median)</small>` : "";
  $("#headline").textContent = d.headline;
  $("#compare").textContent = base
    ? `${rec.name}, ${rec.miles} miles away: median wait ${rec.median_weeks} weeks. Your nearest NHS trust, ${base.name} (${base.miles} miles): ${base.median_weeks} weeks.`
    : `${rec.name}: median wait ${rec.median_weeks} weeks.`;
  $("#reasons").innerHTML = (d.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("");
  $("#tradeoffs").innerHTML = (d.tradeoffs || []).map((r) => `<li>${esc(r)}</li>`).join("");
  $("#askgp").innerHTML = (d.ask_gp || []).map((r) => `<li>${esc(r)}</li>`).join("");

  const maxW = Math.max(...done.shortlist.map((s) => s.p92_weeks));
  $("#cards").innerHTML = done.shortlist.map((s) => {
    const ev = s.evidence || {}, isRec = s.code === d.recommended_code, isBase = base && s.code === base.code;
    const tag = isRec ? "Recommended" : s.code === d.backup_code ? "Backup" : isBase ? "Your nearest NHS trust" : "Option";
    const badge = ev.verified
      ? `<a class="badge" href="${esc(ev.cqc_url)}" target="_blank" rel="noopener" title="${esc(ev.cqc_quote)}">CQC: ${esc(ev.cqc_rating)} ✓ source</a>`
      : `<span class="badge unv">CQC rating not verified</span>`;
    const news = ev.news ? `<div class="news">${esc(ev.news)} ${ev.news_url ? `<a href="${esc(ev.news_url)}" target="_blank" rel="noopener">source</a>` : ""}</div>` : "";
    return `<div class="pcard ${isRec ? "rec" : ""}">
      <div class="tag ${isBase && !isRec ? "base" : ""}">${tag}</div>
      <h5>${esc(s.name)}</h5>
      <div class="where">${esc(s.town)} · ${s.miles} miles${s.independent ? " · independent, NHS-funded" : ""}</div>
      <div class="stat"><span>Median wait</span><b>${s.median_weeks} wks</b></div>
      <div class="bar"><i style="width:${(100 * s.median_weeks / maxW).toFixed(0)}%"></i></div>
      <div class="stat"><span>9 in 10 seen within</span><b>${s.p92_weeks} wks</b></div>
      <div class="stat"><span>Seen within 18 weeks</span><b>${s.pct_within_18}%</b></div>
      ${badge}${news}
    </div>`;
  }).join("");

  $("#letter").textContent = state.letter.text;
  renderRouting(done.usage);
  $("#results").scrollIntoView({ behavior: "smooth" });
}

function renderRouting(usage) {
  if (visionUsage && $("#referral").value) usage = [visionUsage, ...usage];
  const total = ((performance.now() - started) / 1000).toFixed(1);
  const tok = usage.reduce((a, u) => a + u.prompt_tokens + u.completion_tokens, 0);
  $("#routing").innerHTML = `<table><tr><th>Step</th><th>Model</th><th>Time</th><th>Tokens</th></tr>${usage.map((u) =>
    `<tr><td>${esc(u.step)}<div class="model">${esc(u.model)}</div></td><td><span class="chip ${u.tier}">${u.tier}</span></td><td>${u.latency_s}s</td><td>${u.prompt_tokens + u.completion_tokens}${u.reasoning_tokens ? `<div class="rt">${u.reasoning_tokens} reasoning</div>` : ""}</td></tr>`).join("")}
    <tr><th>Analysis</th><th></th><th>${total}s</th><th>${tok}</th></tr></table>`;
  $("#routing").classList.remove("hidden");
}

$("#copy").addEventListener("click", () => navigator.clipboard.writeText($("#letter").textContent));
$("#download").addEventListener("click", () => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([$("#letter").textContent], { type: "text/plain" }));
  a.download = "right-to-choose-letter.txt"; a.click();
});
