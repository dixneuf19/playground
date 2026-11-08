// Phone page. The server renders everything (karaoke/templates) and keeps it up
// to date over the WebSocket (htmx ws extension, fragments morphed by id).
// What's left here is what only the browser can do: tabs, the name cookie,
// confirmation sheets, toasts, drag and drop, resizing photos before upload.
const $ = (id) => document.getElementById(id);
const CODE = document.body.dataset.code;
const NAME_COOKIE = "karaoke_name";

// A push never fights a finger on the volume slider.
Idiomorph.defaults.callbacks.beforeNodeMorphed = (old) =>
  !(old.id === "volume" && old === document.activeElement);

// --- name -------------------------------------------------------------------
function cookieName() {
  const m = document.cookie.match(new RegExp(`(?:^|; )${NAME_COOKIE}=([^;]*)`));
  return m ? decodeURIComponent(m[1]) : "";
}
function saveName(name) {
  document.cookie = `${NAME_COOKIE}=${encodeURIComponent(name)}; path=/; max-age=31536000; samesite=lax`;
  localStorage.setItem("karaoke.name", name);
}
// Phones that picked a name before it moved to a cookie keep it.
if (!cookieName() && localStorage.getItem("karaoke.name")) {
  saveName(localStorage.getItem("karaoke.name"));
  location.reload();
}
function askName() {
  $("name-sheet").classList.remove("hidden");
  setTimeout(() => $("name-input").focus(), 50);
}
$("name-form").onsubmit = (e) => {
  e.preventDefault();
  const name = $("name-input").value.trim().slice(0, 40);
  if (!name) return;
  saveName(name);
  location.reload(); // the server renders "moi", my photos… for this name
};
$("me-btn").onclick = askName;

// --- tabs -------------------------------------------------------------------
function show(view) {
  // Leaving the search tab brings the next search back to the catalogue.
  if (view !== "search" && $("everywhere").value === "1") {
    $("everywhere").value = "0";
    if ($("q").value.trim()) htmx.trigger("#search-form", "submit");
  }
  document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${view}`));
  window.scrollTo({ top: 0 });
}
document.addEventListener("click", (e) => {
  const tab = e.target.closest("[data-view]");
  if (tab) show(tab.dataset.view);
});

// --- toasts, confirmations, errors ------------------------------------------
let toastTimer;
function toast(text) {
  const t = $("toast");
  t.textContent = text;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}
// The server sends `HX-Trigger: {"toast": "…"}`.
document.body.addEventListener("toast", (e) => toast(e.detail.value));

function confirmSheet(title, text, yes = "Oui") {
  return new Promise((resolve) => {
    $("confirm-title").textContent = title;
    $("confirm-text").textContent = text;
    $("confirm-yes").textContent = yes;
    $("confirm-sheet").classList.remove("hidden");
    const done = (v) => { $("confirm-sheet").classList.add("hidden"); resolve(v); };
    $("confirm-yes").onclick = () => done(true);
    $("confirm-no").onclick = () => done(false);
  });
}
// hx-confirm opens our sheet instead of the browser's dialog.
document.addEventListener("htmx:confirm", (e) => {
  const el = e.detail.elt;
  if (!el.hasAttribute("hx-confirm")) return;
  e.preventDefault();
  confirmSheet(el.dataset.confirmTitle || "Sûr ?", e.detail.question, el.dataset.confirmYes).then((ok) => {
    if (ok) e.detail.issueRequest(true);
  });
});

document.addEventListener("htmx:beforeRequest", (e) => {
  const el = e.detail.elt;
  // Adding a song needs a name: it shows on the big screen.
  if (el.matches("[data-added]") && !cookieName()) { e.preventDefault(); askName(); return; }
  if (el.id === "search-form" || e.detail.target?.id === "results") {
    if (!$("q").value.trim()) return;
    $("results").innerHTML = '<div class="skeleton"></div>'.repeat(5);
  }
});
document.addEventListener("htmx:afterRequest", (e) => {
  const el = e.detail.elt;
  if (el.matches("[data-added]") && e.detail.successful) { el.classList.add("done"); el.textContent = "✓"; }
});
document.addEventListener("htmx:responseError", (e) => {
  let detail = "";
  try { detail = JSON.parse(e.detail.xhr.responseText).detail; } catch {}
  toast(typeof detail === "string" && detail ? detail : "Oups, ça n'a pas marché");
});
document.addEventListener("htmx:sendError", () => toast("Pas de réseau ?"));

// --- search -----------------------------------------------------------------
$("q").addEventListener("input", () => {
  if (!$("q").value.trim()) { $("everywhere").value = "0"; htmx.trigger("#search-form", "submit"); }
});
$("search-form").addEventListener("submit", () => $("q").blur());
document.addEventListener("click", async (e) => {
  if (!e.target.closest("[data-paste]")) return;
  let text = "";
  try { text = (await navigator.clipboard.readText()).trim(); } catch {}
  if (!text) { $("q").focus(); toast("Colle le lien dans la barre de recherche"); return; }
  $("q").value = text;
  htmx.trigger("#search-form", "submit");
});

// --- live connection ---------------------------------------------------------
document.body.addEventListener("htmx:wsOpen", () => document.body.classList.remove("offline"));
document.body.addEventListener("htmx:wsClose", (e) => {
  if (e.detail.event.code === 4404) { location.href = "/"; return; } // room gone
  document.body.classList.add("offline");
});

// --- queue: drag and drop ---------------------------------------------------
// A push while a finger drags would yank the list: hold it, then catch up.
let dragging = false;
let missedPush = false;
document.body.addEventListener("htmx:wsBeforeMessage", (e) => {
  if (dragging) { missedPush = true; e.preventDefault(); }
});
function catchUp() {
  if (!missedPush) return;
  missedPush = false;
  htmx.ajax("GET", `/r/${CODE}/state`, { target: "body", swap: "none" });
}
function sortable() {
  const list = $("queue");
  if (!list || list.sortable) return;
  list.sortable = Sortable.create(list, {
    handle: ".handle",
    animation: 180,
    onStart: () => { dragging = true; },
    onEnd: (e) => {
      dragging = false;
      if (e.oldIndex === e.newIndex) { catchUp(); return; }
      missedPush = false; // the move brings a fresh push anyway
      htmx.ajax("POST", `/r/${CODE}/do`, {
        swap: "none",
        values: { type: "move", item_id: e.item.dataset.id, index: e.newIndex },
      });
    },
  });
}
sortable();

// --- photos -----------------------------------------------------------------
const MAX_SIDE = 1920;

async function shrink(file) {
  // Big phone photos (5-10 MB) would crawl over 4G: resize to ~300 KB JPEG.
  try {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = Math.min(1, MAX_SIDE / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    bmp.close?.();
    return await new Promise((ok) => canvas.toBlob(ok, "image/jpeg", 0.85));
  } catch {
    return file; // let the server judge (JPEG/PNG/WebP accepted)
  }
}

$("photo-input").onchange = async (e) => {
  const files = [...e.target.files];
  e.target.value = "";
  if (!files.length) return;
  if (!cookieName()) { askName(); return; }
  let sent = 0, failed = 0, lastError = "";
  $("sending").classList.remove("hidden");
  for (const [i, file] of files.entries()) {
    $("sending-text").textContent = `Envoi ${i + 1}/${files.length}…`;
    const blob = await shrink(file);
    try {
      const res = await fetch(`/api/rooms/${CODE}/photos`, {
        method: "POST", headers: { "Content-Type": blob.type || "application/octet-stream" }, body: blob,
      });
      if (res.ok) sent++;
      else { failed++; lastError = (await res.json().catch(() => ({}))).detail || ""; }
    } catch { failed++; }
  }
  $("sending").classList.add("hidden");
  if (sent) toast(`${sent} photo${sent > 1 ? "s" : ""} envoyée${sent > 1 ? "s" : ""} 📸`);
  if (failed) toast(`${failed} échec${failed > 1 ? "s" : ""}${lastError ? " : " + lastError : ""}`);
};
