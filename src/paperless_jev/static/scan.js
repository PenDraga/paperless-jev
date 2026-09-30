// Belege scannen: Kamera, Randerkennung (OpenCV.js), Entzerren, Filter, mehrseitiges PDF, Upload nach Paperless.
const CV_URL = "https://cdn.jsdelivr.net/npm/@techstark/opencv-js@4.10.0-release.1/dist/opencv.js";
const MAX_SIDE = 3000; // Originale so gross behalten (Speicher auf dem Handy)
const OUT_SIDE = 2400; // entzerrte Seite
const data = JSON.parse(document.getElementById("sc-data").textContent);
const T = data.t;
const $ = (id) => document.getElementById(id);
const fmt = (s, v) => Object.entries(v).reduce((a, [k, x]) => a.replace(`{${k}}`, x), s);

const pages = []; // {img, quad, rotation, filter, out, thumb}
let files = []; // direkt gewählte PDFs
let editIndex = -1;
let editQuad = null;
let retakeIndex = -1;

// --- OpenCV laden (einmalig, danach aus dem Cache) ------------------------------------------
let cvPromise = null;
function loadCv() {
  if (!cvPromise) {
    cvPromise = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = CV_URL;
      s.async = true;
      s.onload = () => {
        const c = window.cv;
        // Das Emscripten-Modul ist "thenable" und löst mit sich selbst auf: nie direkt awaiten
        const ready = (m) => { if (typeof m.then === "function") delete m.then; window.cv = m; resolve(true); };
        if (c && c.Mat) return ready(c);
        if (c && typeof c.then === "function") return c.then((m) => ready(m));
        if (c) c.onRuntimeInitialized = () => ready(c);
        else reject(new Error("OpenCV"));
      };
      s.onerror = reject;
      document.head.append(s);
    }).catch(() => null);
  }
  return cvPromise;
}
const cvReady = () => (window.cv && window.cv.Mat ? window.cv : null);

// --- Bildverarbeitung ----------------------------------------------------------------------------
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
function orderQuad(pts) {
  const sum = pts.map((p) => p[0] + p[1]);
  const diff = pts.map((p) => p[1] - p[0]);
  return [pts[sum.indexOf(Math.min(...sum))], pts[diff.indexOf(Math.min(...diff))],
    pts[sum.indexOf(Math.max(...sum))], pts[diff.indexOf(Math.max(...diff))]];
}
const fullQuad = (w, h) => [[0, 0], [w, 0], [w, h], [0, h]];

// Grösstes Viereck im Bild (Beleg); null, wenn nichts Plausibles gefunden
function detectQuad(source, w, h) {
  const cv = cvReady();
  if (!cv) return null;
  const scale = 480 / Math.max(w, h);
  const small = document.createElement("canvas");
  small.width = Math.round(w * scale);
  small.height = Math.round(h * scale);
  small.getContext("2d").drawImage(source, 0, 0, small.width, small.height);
  const mats = [];
  const keep = (m) => (mats.push(m), m);
  try {
    const src = keep(cv.imread(small));
    const gray = keep(new cv.Mat());
    cv.cvtColor(src, gray, cv.COLOR_RGBA2GRAY);
    cv.GaussianBlur(gray, gray, new cv.Size(5, 5), 0);
    const edges = keep(new cv.Mat());
    cv.Canny(gray, edges, 40, 120);
    const kernel = keep(cv.getStructuringElement(cv.MORPH_RECT, new cv.Size(5, 5)));
    cv.dilate(edges, edges, kernel);
    const contours = keep(new cv.MatVector());
    const hier = keep(new cv.Mat());
    cv.findContours(edges, contours, hier, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE);
    let best = null;
    let bestArea = small.width * small.height * 0.12;
    for (let i = 0; i < contours.size(); i++) {
      const c = keep(contours.get(i));
      const area = cv.contourArea(c);
      if (area < bestArea) continue;
      const approx = keep(new cv.Mat());
      cv.approxPolyDP(c, approx, 0.02 * cv.arcLength(c, true), true);
      if (approx.rows === 4 && cv.isContourConvex(approx)) {
        const d = approx.data32S;
        best = [[d[0], d[1]], [d[2], d[3]], [d[4], d[5]], [d[6], d[7]]];
        bestArea = area;
      }
    }
    return best ? orderQuad(best.map(([x, y]) => [x / scale, y / scale])) : null;
  } finally {
    mats.forEach((m) => m.delete());
  }
}

// Entzerren, Filter, Drehen -> Canvas der fertigen Seite
function processPage(page) {
  const cv = cvReady();
  const out = document.createElement("canvas");
  const [tl, tr, br, bl] = page.quad;
  let W = Math.max(dist(tl, tr), dist(bl, br));
  let H = Math.max(dist(tl, bl), dist(tr, br));
  const k = Math.min(1, OUT_SIDE / Math.max(W, H));
  W = Math.round(W * k); H = Math.round(H * k);
  if (!cv) {
    // ohne OpenCV: nur zuschneiden auf das umschliessende Rechteck
    const xs = page.quad.map((p) => p[0]); const ys = page.quad.map((p) => p[1]);
    const x0 = Math.min(...xs); const y0 = Math.min(...ys);
    const cw = Math.max(...xs) - x0; const ch = Math.max(...ys) - y0;
    const rot = page.rotation % 180 !== 0;
    out.width = rot ? ch : cw; out.height = rot ? cw : ch;
    const ctx = out.getContext("2d");
    ctx.filter = page.filter === "scan" ? "grayscale(1) contrast(1.6) brightness(1.1)" : page.filter === "gray" ? "grayscale(1)" : "none";
    ctx.translate(out.width / 2, out.height / 2);
    ctx.rotate((page.rotation * Math.PI) / 180);
    ctx.drawImage(page.img, x0, y0, cw, ch, -cw / 2, -ch / 2, cw, ch);
    return out;
  }
  const mats = [];
  const keep = (m) => (mats.push(m), m);
  try {
    const src = keep(cv.imread(page.img));
    const from = keep(cv.matFromArray(4, 1, cv.CV_32FC2, page.quad.flat()));
    const to = keep(cv.matFromArray(4, 1, cv.CV_32FC2, [0, 0, W, 0, W, H, 0, H]));
    const M = keep(cv.getPerspectiveTransform(from, to));
    let dst = keep(new cv.Mat());
    cv.warpPerspective(src, dst, M, new cv.Size(W, H), cv.INTER_LINEAR, cv.BORDER_REPLICATE);
    if (page.filter !== "color") {
      const gray = keep(new cv.Mat());
      cv.cvtColor(dst, gray, cv.COLOR_RGBA2GRAY);
      dst = gray;
      if (page.filter === "scan") {
        // Beleuchtung ausgleichen (Schatten weg), dann Kontrast strecken
        const bg = keep(new cv.Mat());
        const kernel = keep(cv.getStructuringElement(cv.MORPH_RECT, new cv.Size(9, 9)));
        cv.dilate(gray, bg, kernel);
        cv.medianBlur(bg, bg, 31);
        const diff = keep(new cv.Mat());
        cv.absdiff(gray, bg, diff);
        cv.bitwise_not(diff, diff);
        cv.normalize(diff, diff, 0, 255, cv.NORM_MINMAX);
        diff.convertTo(diff, -1, 1.25, -40);
        dst = diff;
      }
    }
    const codes = { 90: cv.ROTATE_90_CLOCKWISE, 180: cv.ROTATE_180, 270: cv.ROTATE_90_COUNTERCLOCKWISE };
    if (codes[page.rotation] !== undefined) {
      const r = keep(new cv.Mat());
      cv.rotate(dst, r, codes[page.rotation]);
      dst = r;
    }
    cv.imshow(out, dst);
    return out;
  } finally {
    mats.forEach((m) => m.delete());
  }
}

function toCanvas(source, w, h) {
  const k = Math.min(1, MAX_SIDE / Math.max(w, h));
  const c = document.createElement("canvas");
  c.width = Math.round(w * k);
  c.height = Math.round(h * k);
  c.getContext("2d").drawImage(source, 0, 0, c.width, c.height);
  return c;
}

function addPage(img) {
  const found = detectQuad(img, img.width, img.height);
  const page = { img, quad: found || fullQuad(img.width, img.height), rotation: 0, filter: "scan", detected: !!found };
  page.out = processPage(page);
  page.thumb = page.out.toDataURL("image/jpeg", 0.6);
  if (retakeIndex >= 0) { pages[retakeIndex] = page; retakeIndex = -1; } else pages.push(page);
  renderPages();
}

// --- Ansichten ----------------------------------------------------------------------------------
const views = ["overview", "capture", "edit", "status"];
function show(view) {
  views.forEach((v) => ($(`sc-${v}`).hidden = v !== view));
  document.body.dataset.scanView = view;
  if (view === "capture") startCamera(); else stopCamera();
  if (view === "overview") renderPages();
  window.scrollTo(0, 0);
}
document.addEventListener("click", (e) => {
  const go = e.target.closest("[data-go]");
  if (go) { e.preventDefault(); show(go.dataset.go); }
});

// --- Kamera --------------------------------------------------------------------------------------
const video = $("sc-video");
const overlay = $("sc-overlay");
let stream = null;
let loopTimer = null;
let liveQuad = null;

async function startCamera() {
  renderThumbs();
  const hint = $("sc-hint");
  if (!navigator.mediaDevices?.getUserMedia) return noCamera();
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 3840 }, height: { ideal: 2160 } }, audio: false,
    });
  } catch {
    return noCamera();
  }
  video.srcObject = stream;
  await video.play().catch(() => {});
  setupTorch();
  hint.textContent = cvReady() ? T.search : T.loadingCv;
  loadCv().then(() => { if (stream) hint.textContent = T.search; });
  loopTimer = setInterval(liveDetect, 280);
}
// Lampe: nur wenn Browser und Gerät sie anbieten (z. B. Chrome auf Android)
let torchOn = false;
function setupTorch() {
  const btn = $("sc-torch");
  const track = stream?.getVideoTracks()[0];
  const caps = track?.getCapabilities ? track.getCapabilities() : {};
  btn.hidden = !caps.torch;
  torchOn = false;
  btn.setAttribute("aria-pressed", "false");
}
$("sc-torch").addEventListener("click", async () => {
  const track = stream?.getVideoTracks()[0];
  if (!track) return;
  try {
    await track.applyConstraints({ advanced: [{ torch: !torchOn }] });
    torchOn = !torchOn;
    $("sc-torch").setAttribute("aria-pressed", String(torchOn));
  } catch {
    $("sc-torch").hidden = true;
  }
});

function stopCamera() {
  torchOn = false;
  clearInterval(loopTimer);
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
}
function noCamera() {
  loadCv(); // für Fotos aus der Kamera-App trotzdem Rand erkennen
  $("sc-hint").textContent = T.nocam;
  $("sc-nocam").classList.add("prominent");
  $("sc-shutter").disabled = true;
}

// Position des Videobildes im Element (object-fit: contain)
function videoRect() {
  const vw = video.videoWidth; const vh = video.videoHeight;
  const ew = video.clientWidth; const eh = video.clientHeight;
  const k = Math.min(ew / vw, eh / vh);
  return { k, x: (ew - vw * k) / 2, y: (eh - vh * k) / 2 };
}
function liveDetect() {
  if (!stream || !video.videoWidth) return;
  overlay.width = video.clientWidth;
  overlay.height = video.clientHeight;
  const ctx = overlay.getContext("2d");
  ctx.clearRect(0, 0, overlay.width, overlay.height);
  if (!cvReady()) return;
  liveQuad = detectQuad(video, video.videoWidth, video.videoHeight);
  const hint = $("sc-hint");
  hint.textContent = liveQuad ? T.detected : T.search;
  hint.classList.toggle("ok", !!liveQuad);
  if (!liveQuad) return;
  const r = videoRect();
  ctx.beginPath();
  liveQuad.forEach(([x, y], i) => ctx[i ? "lineTo" : "moveTo"](r.x + x * r.k, r.y + y * r.k));
  ctx.closePath();
  ctx.fillStyle = "rgba(34,197,94,.14)";
  ctx.strokeStyle = "#22c55e";
  ctx.lineWidth = 3;
  ctx.fill();
  ctx.stroke();
}

$("sc-shutter").addEventListener("click", () => {
  if (!video.videoWidth) return;
  const flash = $("sc-flash");
  flash.classList.remove("on"); void flash.offsetWidth; flash.classList.add("on");
  addPage(toCanvas(video, video.videoWidth, video.videoHeight));
  renderThumbs();
  if (navigator.vibrate) navigator.vibrate(30);
});

async function imageFromFile(file) {
  const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" }).catch(() => null);
  if (bitmap) return toCanvas(bitmap, bitmap.width, bitmap.height);
  const img = new Image();
  img.src = URL.createObjectURL(file);
  await img.decode();
  return toCanvas(img, img.naturalWidth, img.naturalHeight);
}
$("sc-native").addEventListener("change", async (e) => {
  await loadCv();
  for (const f of e.target.files) addPage(await imageFromFile(f));
  e.target.value = "";
  renderThumbs();
});

function renderThumbs() {
  $("sc-capcount").textContent = pages.length ? fmt(T.pages, { n: pages.length }) : T.none;
  $("sc-thumbs").innerHTML = pages.slice(-2).map((p, i, a) =>
    `<div style="background-image:url(${p.thumb})">${i === a.length - 1 ? `<span>${pages.length}</span>` : ""}</div>`).join("");
}

// --- Dateien wählen (PDF direkt, Bilder als Seiten) ---------------------------------------------------
async function onFiles(e) {
  const chosen = [...e.target.files];
  e.target.value = "";
  const images = chosen.filter((f) => f.type.startsWith("image/"));
  files = files.concat(chosen.filter((f) => f.type === "application/pdf"));
  if (images.length) {
    await loadCv();
    for (const f of images) addPage(await imageFromFile(f));
  }
  renderPages();
}
$("sc-files").addEventListener("change", onFiles);
$("sc-files2").addEventListener("change", onFiles);

// --- Übersicht ------------------------------------------------------------------------------------------
function renderPages() {
  const grid = $("sc-grid");
  grid.innerHTML = "";
  pages.forEach((p, i) => {
    const el = document.createElement("div");
    el.className = "sc-page";
    el.innerHTML = `<img src="${p.thumb}" alt=""><b>${i + 1}</b>
      <button type="button" class="sc-x" aria-label="✕">✕</button>
      <span class="sc-move">${i > 0 ? '<button type="button" data-move="-1">‹</button>' : ""}${i < pages.length - 1 ? '<button type="button" data-move="1">›</button>' : ""}</span>`;
    el.querySelector("img").addEventListener("click", () => openEdit(i));
    el.querySelector(".sc-x").addEventListener("click", () => { pages.splice(i, 1); renderPages(); });
    el.querySelectorAll("[data-move]").forEach((b) => b.addEventListener("click", () => {
      const j = i + Number(b.dataset.move);
      [pages[i], pages[j]] = [pages[j], pages[i]];
      renderPages();
    }));
    grid.append(el);
  });
  files.forEach((f, i) => {
    const el = document.createElement("div");
    el.className = "sc-page sc-file";
    el.innerHTML = `<span class="sc-fname"></span><b>PDF</b><button type="button" class="sc-x">✕</button>`;
    el.querySelector(".sc-fname").textContent = f.name;
    el.querySelector(".sc-x").addEventListener("click", () => { files.splice(i, 1); renderPages(); });
    grid.append(el);
  });
  const any = pages.length + files.length > 0;
  const add = document.createElement("button");
  add.type = "button";
  add.className = "sc-page sc-add";
  add.dataset.go = "capture";
  add.textContent = "+";
  if (any) grid.append(add);
  $("sc-pages-wrap").hidden = !any;
  $("sc-empty").hidden = any;
  $("sc-count").textContent = pages.length ? fmt(T.pages, { n: pages.length }) : files.length ? fmt(T.uploadFiles, { n: files.length }) : T.none;
  $("sc-upload").disabled = !any;
  $("sc-upload-label").textContent = pages.length ? fmt(T.upload, { n: pages.length }) : fmt(T.uploadFiles, { n: files.length });
}

// Tags der gewählten Instanz
function renderTags() {
  const box = $("sc-tags");
  const tags = data.tags[$("sc-instance").value] || [];
  box.innerHTML = "";
  tags.forEach(([id, name], i) => {
    const l = document.createElement("label");
    l.className = "tchip new" + (i >= 8 ? " sc-hidden" : "");
    l.innerHTML = `<input type="checkbox" value="${id}"><span class="tchip-mark"></span>`;
    l.append(name);
    box.append(l);
  });
  if (tags.length > 8) {
    const more = document.createElement("button");
    more.type = "button";
    more.className = "tchip more";
    more.textContent = T.more;
    more.addEventListener("click", () => { box.querySelectorAll(".sc-hidden").forEach((x) => x.classList.remove("sc-hidden")); more.remove(); });
    box.append(more);
  }
}
$("sc-instance").addEventListener("change", renderTags);
renderTags();

// --- Seite bearbeiten ---------------------------------------------------------------------------------
const editCanvas = $("sc-editcanvas");
const quadSvg = $("sc-quad");
let editScale = 1;

function openEdit(i) {
  editIndex = i;
  const p = pages[i];
  editQuad = p.quad.map((q) => [...q]);
  $("sc-edit-title").textContent = fmt(T.page, { n: i + 1 });
  document.querySelectorAll("#sc-filters [data-filter]").forEach((b) => b.classList.toggle("on", b.dataset.filter === p.filter));
  show("edit");
  editRotation = p.rotation;
  requestAnimationFrame(drawEdit);
}
let editRotation = 0;
function drawEdit() {
  const p = pages[editIndex];
  const area = $("sc-editarea");
  editScale = Math.min((area.clientWidth - 40) / p.img.width, (area.clientHeight - 40) / p.img.height);
  editCanvas.width = Math.round(p.img.width * editScale);
  editCanvas.height = Math.round(p.img.height * editScale);
  editCanvas.getContext("2d").drawImage(p.img, 0, 0, editCanvas.width, editCanvas.height);
  quadSvg.setAttribute("width", editCanvas.width);
  quadSvg.setAttribute("height", editCanvas.height);
  drawQuad();
}
function drawQuad() {
  const pts = editQuad.map(([x, y]) => [x * editScale, y * editScale]);
  quadSvg.innerHTML = `<polygon points="${pts.map((p) => p.join(",")).join(" ")}" />` +
    pts.map(([x, y], i) => `<circle class="hit" data-i="${i}" cx="${x}" cy="${y}" r="26"/><circle class="knob" cx="${x}" cy="${y}" r="12"/>`).join("");
}
let dragging = -1;
quadSvg.addEventListener("pointerdown", (e) => {
  const hit = e.target.closest(".hit");
  if (!hit) return;
  dragging = Number(hit.dataset.i);
  quadSvg.setPointerCapture(e.pointerId);
});
quadSvg.addEventListener("pointermove", (e) => {
  if (dragging < 0) return;
  const r = quadSvg.getBoundingClientRect();
  const p = pages[editIndex];
  editQuad[dragging] = [
    Math.min(Math.max((e.clientX - r.left) / editScale, 0), p.img.width),
    Math.min(Math.max((e.clientY - r.top) / editScale, 0), p.img.height),
  ];
  drawQuad();
});
quadSvg.addEventListener("pointerup", () => (dragging = -1));
quadSvg.addEventListener("pointercancel", () => (dragging = -1));

document.querySelectorAll("#sc-filters [data-filter]").forEach((b) => b.addEventListener("click", () => {
  document.querySelectorAll("#sc-filters [data-filter]").forEach((x) => x.classList.toggle("on", x === b));
}));
document.querySelectorAll("[data-act]").forEach((b) => b.addEventListener("click", () => {
  const p = pages[editIndex];
  if (b.dataset.act === "rotate") { editRotation = (editRotation + 90) % 360; b.classList.add("pulse"); setTimeout(() => b.classList.remove("pulse"), 300); $("sc-edit-sub").textContent = `${editRotation}°`; }
  if (b.dataset.act === "reset") { editQuad = fullQuad(p.img.width, p.img.height); drawQuad(); }
  if (b.dataset.act === "retake") { retakeIndex = editIndex; show("capture"); }
  if (b.dataset.act === "delete") { pages.splice(editIndex, 1); show("overview"); }
}));
$("sc-apply").addEventListener("click", () => {
  const p = pages[editIndex];
  p.quad = orderQuad(editQuad);
  p.rotation = editRotation;
  p.filter = document.querySelector("#sc-filters .on")?.dataset.filter || "scan";
  p.out = processPage(p);
  p.thumb = p.out.toDataURL("image/jpeg", 0.6);
  show("overview");
});
window.addEventListener("resize", () => { if (!$("sc-edit").hidden) drawEdit(); });

// --- Hochladen -----------------------------------------------------------------------------------------
function buildPdf() {
  const { jsPDF } = window.jspdf;
  let doc = null;
  for (const p of pages) {
    const w = 595; // A4-Breite in Punkt, Höhe nach Seitenverhältnis
    const h = Math.round((p.out.height / p.out.width) * w);
    if (!doc) doc = new jsPDF({ unit: "pt", format: [w, h], orientation: h >= w ? "p" : "l", compress: true });
    else doc.addPage([w, h], h >= w ? "p" : "l");
    doc.addImage(p.out.toDataURL("image/jpeg", 0.82), "JPEG", 0, 0, w, h, undefined, "FAST");
  }
  return doc.output("blob");
}

async function upload(blob, name) {
  const fd = new FormData();
  fd.append("file", blob, name);
  fd.append("instance_id", $("sc-instance").value);
  const title = $("sc-title").value.trim();
  if (title) fd.append("title", title);
  document.querySelectorAll("#sc-tags input:checked").forEach((c) => fd.append("tags", c.value));
  const r = await fetch("/scan/upload", { method: "POST", body: fd });
  const j = await r.json().catch(() => ({ error: r.statusText }));
  if (!r.ok || j.error) throw new Error(j.error || r.statusText);
  return j;
}

$("sc-upload").addEventListener("click", async () => {
  const btn = $("sc-upload");
  btn.disabled = true;
  btn.setAttribute("aria-busy", "true");
  const jobs = [];
  try {
    if (pages.length) {
      const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
      const blob = buildPdf();
      jobs.push({ ...(await upload(blob, `scan-${stamp}.pdf`)), label: fmt(T.pages, { n: pages.length }) });
    }
    for (const f of files) jobs.push({ ...(await upload(f, f.name)), label: f.name });
  } catch (err) {
    alert(fmt(T.failed, { error: err.message }));
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
    return;
  }
  btn.removeAttribute("aria-busy");
  pages.length = 0;
  files = [];
  $("sc-title").value = "";
  showStatus(jobs);
});

// --- Status ---------------------------------------------------------------------------------------------
const kb = (n) => (n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} kB`);
function setStep(step, state, text) {
  const li = document.querySelector(`#sc-steps [data-step="${step}"]`);
  li.className = state;
  if (text !== undefined) li.querySelector("small").textContent = text;
}
function showStatus(jobs) {
  show("status");
  const job = jobs[0];
  $("sc-status-sub").textContent = jobs.map((j) => j.label).join(" · ");
  $("sc-result").hidden = true;
  $("sc-links").innerHTML = "";
  setStep("upload", "ok", fmt(T.sent, { size: kb(jobs.reduce((a, j) => a + j.size, 0)) }));
  setStep("ocr", "run", T.ocr);
  setStep("jev", "wait", "");
  setStep("done", "wait", "");
  poll(job);
}
async function poll(job) {
  let s;
  try {
    s = await (await fetch(`/scan/status/${job.instance_id}/${encodeURIComponent(job.task_id)}`)).json();
  } catch {
    return setTimeout(() => poll(job), 3000);
  }
  if (s.state === "error") { setStep("ocr", "err", fmt(T.failed, { error: s.error })); return; }
  if (s.state === "queued" || s.state === "processing") return setTimeout(() => poll(job), 2000);
  setStep("ocr", "ok", fmt(T.docid, { id: s.doc_id }));
  if (s.state === "classifying") { setStep("jev", "run", T.jev); return setTimeout(() => poll(job), 2000); }
  const failed = s.status === "error";
  setStep("jev", failed ? "err" : "ok", s.status_label);
  setStep("done", failed ? "wait" : "ok", failed ? "" : s.title || "");
  const res = $("sc-result");
  res.innerHTML = s.fields.map((f) =>
    `<div class="sc-row"><span>${esc(f.name)}</span><span>${esc(f.label)} <span class="rv-c ${f.level}">${Math.round(f.confidence * 100)} %</span></span></div>`).join("");
  res.hidden = !s.fields.length;
  $("sc-links").innerHTML = `<a href="${s.browse_url}" target="_blank" rel="noopener">${T.view}</a> · <a href="/jobs/${s.job_id}">${T.job}</a> · <a href="/">${T.overview}</a>`;
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
$("sc-again").addEventListener("click", () => show("capture"));

// OpenCV erst bei Bedarf laden (Kamera oder Bilder): das Übersetzen blockiert den Browser kurz
renderPages();
