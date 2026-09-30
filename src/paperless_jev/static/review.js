// Review: Dokument per pdf.js mit markierten Jev-Werten, Auswahl-Blätter, Titelvorschlag, Tastatur.
const PDFJS = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.10.38";
const data = JSON.parse(document.getElementById("rv-data").textContent);
const T = data.t;

// --- Dokumentansicht --------------------------------------------------------------
const docEl = document.getElementById("rv-doc");
const pagesEl = document.getElementById("rv-pages");
const pageInfo = document.querySelector("#rv-pageinfo span");
let zoom = 1;
let pdf = null;
let imageUrl = null;
let renderToken = 0;

const norm = (s) => s.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/\s+/g, " ").trim();

// Suchbegriffe: ganzer Wert, sonst markante Wörter (z. B. "Bauhaus" aus "Bauhaus AG")
const needles = data.marks.flatMap((label, i) => {
  const level = data.levels[i] === "auto" ? "ok" : "warn";
  const full = norm(String(label));
  const words = full.split(" ").filter((w) => w.length >= 5 && !/^(gmbh|schweiz|suisse)$/.test(w));
  return [{ text: full, level, src: i, full: true }, ...words.map((w) => ({ text: w, level, src: i, full: false }))];
}).filter((n) => n.text.length >= 3);

async function loadDocument() {
  try {
    const resp = await fetch(docEl.dataset.src);
    if (!resp.ok) throw new Error(resp.status);
    const type = resp.headers.get("content-type") || "";
    const buf = await resp.arrayBuffer();
    if (type.startsWith("image/")) {
      imageUrl = URL.createObjectURL(new Blob([buf], { type }));
    } else {
      const pdfjs = await import(`${PDFJS}/pdf.min.mjs`);
      pdfjs.GlobalWorkerOptions.workerSrc = `${PDFJS}/pdf.worker.min.mjs`;
      pdf = await pdfjs.getDocument({ data: buf }).promise;
      window.pdfjsUtil = pdfjs.Util;
    }
    await render();
  } catch (e) {
    pagesEl.innerHTML = `<div class="rv-loading">${T.failed}<br><a href="${docEl.dataset.src.split("?")[0]}" target="_blank" rel="noopener">${T.open}</a></div>`;
  }
}

async function render() {
  const token = ++renderToken;
  const width = Math.max(pagesEl.clientWidth - 16, 200) * zoom;
  pagesEl.innerHTML = "";
  if (imageUrl) {
    const box = document.createElement("div");
    box.className = "rv-page";
    box.style.width = `${width}px`;
    box.innerHTML = `<img src="${imageUrl}" alt="">`;
    pagesEl.append(box);
    pageInfo.textContent = T.page.replace("{n}", 1).replace("{total}", 1);
    return;
  }
  const total = Math.min(pdf.numPages, 30);
  pageInfo.textContent = T.page.replace("{n}", 1).replace("{total}", pdf.numPages);
  for (let n = 1; n <= total; n++) {
    if (token !== renderToken) return;
    const page = await pdf.getPage(n);
    const base = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({ scale: width / base.width });
    const dpr = Math.min(window.devicePixelRatio || 1, 3);
    const box = document.createElement("div");
    box.className = "rv-page";
    box.dataset.page = n;
    box.style.width = `${viewport.width}px`;
    box.style.height = `${viewport.height}px`;
    const canvas = document.createElement("canvas");
    canvas.width = Math.floor(viewport.width * dpr);
    canvas.height = Math.floor(viewport.height * dpr);
    box.append(canvas);
    pagesEl.append(box);
    await page.render({ canvasContext: canvas.getContext("2d"), viewport, transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null }).promise;
    await highlight(page, viewport, box);
  }
  observePages();
}

// Markiert Textstellen, die zu Jevs Werten passen (ganzer Wert zuerst, sonst markante Wörter)
async function highlight(page, viewport, box) {
  const content = await page.getTextContent();
  let any = false;
  const done = new Set();
  for (const needle of needles) {
    if (!needle.full && done.has(needle.src)) continue;
    for (const item of content.items) {
      const str = item.str || "";
      // nur ganze Wörter: "Rechnung" nicht mitten in "Prämienrechnung"
      const hit = new RegExp(`(^|[^\\p{L}\\p{N}])${needle.text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?=$|[^\\p{L}\\p{N}])`, "u").exec(norm(str));
      if (!hit || !str.trim()) continue;
      const idx = hit.index + hit[1].length;
      const tx = window.pdfjsUtil.transform(viewport.transform, item.transform);
      const h = Math.hypot(tx[2], tx[3]);
      const w = item.width * viewport.scale;
      const frac = norm(str).length ? needle.text.length / norm(str).length : 1;
      const mark = document.createElement("span");
      mark.className = `rv-mark ${needle.level}`;
      mark.style.left = `${tx[4] + w * (idx / Math.max(norm(str).length, 1))}px`;
      mark.style.top = `${tx[5] - h}px`;
      mark.style.width = `${Math.max(w * frac, 8)}px`;
      mark.style.height = `${h * 1.15}px`;
      box.append(mark);
      any = true;
      if (needle.full) done.add(needle.src);
    }
  }
  return any;
}

let observer;
function observePages() {
  observer?.disconnect();
  observer = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (e.isIntersecting) pageInfo.textContent = T.page.replace("{n}", e.target.dataset.page).replace("{total}", pdf.numPages);
    }
  }, { root: pagesEl, threshold: 0.5 });
  pagesEl.querySelectorAll(".rv-page").forEach((p) => observer.observe(p));
}

document.querySelectorAll("[data-zoom]").forEach((b) => b.addEventListener("click", () => {
  zoom = Math.min(Math.max(zoom + Number(b.dataset.zoom) * 0.25, 0.5), 3);
  render();
}));
document.getElementById("rv-fs").addEventListener("click", () => {
  docEl.classList.toggle("rv-fullscreen");
  document.body.classList.toggle("rv-noscroll", docEl.classList.contains("rv-fullscreen"));
  document.querySelector(".rv-fs-label").textContent = docEl.classList.contains("rv-fullscreen") ? T.exitfs : document.querySelector("#rv-fs").getAttribute("aria-label");
  render();
});
let resizeTimer;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => (pdf || imageUrl) && render(), 250); });
loadDocument();

// --- Auswahl-Blätter ------------------------------------------------------------------
const sheet = document.getElementById("rv-sheet");
const list = document.getElementById("rv-sheet-list");
const search = document.getElementById("rv-sheet-search");
let sheetField = null;

function option(id, name, extra = "", pct = null) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "rv-opt" + (String(document.getElementById(`in-${sheetField}`).value) === String(id ?? "") ? " sel" : "");
  b.innerHTML = `<span class="rv-rad"></span><span class="rv-optname"></span>` +
    (pct !== null ? `<span class="rv-optbar"><span style="width:${Math.round(pct * 100)}%"></span></span><span class="rv-optpct">${Math.round(pct * 100)} %</span>` : "");
  b.querySelector(".rv-optname").textContent = name;
  if (extra) b.querySelector(".rv-optname").insertAdjacentHTML("beforeend", `<small>${extra}</small>`);
  b.addEventListener("click", () => {
    document.getElementById(`in-${sheetField}`).value = id ?? "";
    document.getElementById(`lbl-${sheetField}`).textContent = id == null ? T.empty : name;
    sheet.close();
  });
  return b;
}

function fillSheet() {
  const c = data.choices[sheetField];
  const q = norm(search.value);
  list.innerHTML = "";
  if (!q) {
    const shown = new Set();
    if (c.top.length) list.insertAdjacentHTML("beforeend", `<div class="rv-sheet-sec">${T.suggestions}</div>`);
    for (const t of c.top) {
      list.append(option(t.id, t.name, t.id === c.current ? T.now : "", t.p));
      shown.add(t.id);
    }
    if (c.current != null && !shown.has(c.current)) {
      const cur = c.options.find((o) => o[0] === c.current);
      if (cur) list.append(option(cur[0], cur[1], T.now));
    }
    list.append(option(null, T.empty));
    list.insertAdjacentHTML("beforeend", `<div class="rv-sheet-sec">${T.all}</div>`);
  }
  for (const [id, name] of c.options) {
    if (!q || norm(name).includes(q)) list.append(option(id, name, id === c.current ? T.now : ""));
  }
}

document.querySelectorAll("[data-sheet]").forEach((row) => row.addEventListener("click", () => {
  sheetField = row.dataset.sheet;
  document.getElementById("rv-sheet-title").textContent = data.labels[sheetField];
  search.value = "";
  fillSheet();
  sheet.showModal();
}));
search.addEventListener("input", fillSheet);
sheet.addEventListener("click", (e) => { if (e.target === sheet || e.target.closest("[data-close-sheet]")) sheet.close(); });

// --- weitere Tags, Titelvorschlag -------------------------------------------------------
document.getElementById("rv-moretags")?.addEventListener("click", (e) => {
  const more = document.getElementById("rv-moretags-list");
  more.hidden = !more.hidden;
  e.currentTarget.classList.toggle("open", !more.hidden);
});
const suggestBtn = document.getElementById("rv-suggest");
suggestBtn?.addEventListener("click", async () => {
  const note = document.getElementById("rv-titlenote");
  const before = note.textContent;
  suggestBtn.setAttribute("aria-busy", "true");
  note.textContent = T.loading;
  try {
    const r = await (await fetch(suggestBtn.dataset.url, { method: "POST" })).json();
    if (r.title) { document.getElementById("rv-title").value = r.title; note.textContent = before; }
    else note.textContent = r.error || before;
  } catch { note.textContent = before; }
  suggestBtn.removeAttribute("aria-busy");
});

// --- Tastatur (Desktop) --------------------------------------------------------------------
document.addEventListener("keydown", (e) => {
  if (sheet.open) return;
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
    document.querySelector('#rv-form button[value="apply"]')?.click();
    return;
  }
  if (e.target.closest("input, textarea, select")) return;
  const target = e.key === "ArrowRight" ? "next" : e.key === "ArrowLeft" ? "prev" : null;
  const link = target && document.querySelector(`[data-key="${target}"]`);
  if (link) location.href = link.href;
});
