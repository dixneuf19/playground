// Phone page. The server renders everything (karaoke/templates) and keeps it up
// to date over the WebSocket (htmx 4 + hx-ws, fragments morphed in by id).
// What's left here is what only the browser can do: tabs, the name sheet,
// confirmation sheets, toasts, drag and drop, resizing photos before upload.
const $ = (id) => document.getElementById(id);
const CODE = document.body.dataset.code;

// --- name -------------------------------------------------------------------
// The server keeps who holds which name in the room (one phone per name) and
// sets the cookies; the header button carries the name this phone holds.
const myName = () => $("me-btn").dataset.me;
// Phones that picked a name before cookies: suggest it, still to confirm.
const legacyName = localStorage.getItem("karaoke.name");
if (legacyName && !$("name-input").value) $("name-input").value = legacyName;
function askName() {
  $("name-error").classList.add("hidden");
  $("name-sheet").classList.remove("hidden");
  setTimeout(() => $("name-input").focus(), 50);
}
$("name-form").onsubmit = async (e) => {
  e.preventDefault();
  // By code point, like the server: never splits an emoji.
  const name = [...$("name-input").value.trim()].slice(0, 40).join("");
  if (!name) return;
  let res;
  try {
    res = await fetch(`/r/${CODE}/name`, { method: "POST", body: new URLSearchParams({ name }) });
  } catch { toast("Pas de réseau ?"); return; }
  if (res.ok) {
    localStorage.removeItem("karaoke.name"); // the pre-cookie name, now superseded
    location.reload(); // the server renders "moi", my photos… for this name
    return;
  }
  const body = await res.json().catch(() => ({}));
  $("name-error").textContent = (body.detail || "Oups, ça n'a pas marché") +
    // French spacing, and the suggested name kept on one line
    (body.suggestion ? `. Et «\u00a0${body.suggestion.replaceAll(" ", "\u00a0")}\u00a0»\u00a0?` : "");
  $("name-error").classList.remove("hidden");
  // Keep what they typed, selected to retype: a prefilled suggestion got
  // submitted by mistake with a quick second tap.
  $("name-input").focus();
  $("name-input").select();
};
// Delegated: the button is replaced by pushes.
document.addEventListener("click", (e) => { if (e.target.closest("#me-btn")) askName(); });

// --- tabs -------------------------------------------------------------------
function show(view) {
  // Leaving the search tab brings the next search back to the catalogue.
  if (view !== "search" && $("everywhere").value === "1") {
    $("everywhere").value = "0";
    if ($("q").value.trim()) $("search-form").requestSubmit();
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
// Adding a song (shown on the big screen) or cancelling an artist (signed)
// needs a name. Checked before the confirmation sheet, pointless without one.
document.addEventListener("htmx:config:request", (e) => {
  if (e.detail.ctx.sourceElement.matches("[data-added], #cancel-form") && !myName()) {
    e.preventDefault();
    askName();
  }
});
// hx-confirm opens our sheet instead of the browser's dialog.
document.addEventListener("htmx:confirm", (e) => {
  const el = e.detail.ctx.sourceElement;
  e.preventDefault();
  confirmSheet(el.dataset.confirmTitle || "Sûr ?", e.detail.ctx.confirm, el.dataset.confirmYes).then((ok) => {
    if (ok) e.detail.issueRequest();
    else e.detail.dropRequest();
  });
});

document.addEventListener("htmx:before:request", (e) => {
  // A search (the form, or the catalogue/YouTube buttons in the results)
  if (e.detail.ctx.sourceElement.matches("#search-form, #results [hx-get]") && $("q").value.trim()) {
    $("results").innerHTML = `<div class="list">${'<div class="skeleton"></div>'.repeat(5)}</div>`;
  }
});
document.addEventListener("htmx:after:request", (e) => {
  const { sourceElement: el, response } = e.detail.ctx;
  if (el.matches("[data-added]") && response.status < 400) { el.classList.add("done"); el.textContent = "✓"; }
});
// Errors are not swapped (noSwap in the htmx-config meta): show the message.
document.addEventListener("htmx:response:error", (e) => {
  let detail = "";
  try { detail = JSON.parse(e.detail.ctx.text).detail; } catch { /* not JSON */ }
  toast(typeof detail === "string" && detail ? detail : "Oups, ça n'a pas marché");
});
document.addEventListener("htmx:error", () => toast("Pas de réseau ?"));

// --- search -----------------------------------------------------------------
$("q").addEventListener("input", () => {
  if (!$("q").value.trim()) { $("everywhere").value = "0"; $("search-form").requestSubmit(); }
});
$("search-form").addEventListener("submit", () => $("q").blur());

// --- cancelled artists ------------------------------------------------------
// A song's 🚫 button opens the artist sheet: cancel the artist with a reason,
// or see why it is cancelled and uncancel it. The server sends the details.
function closeArtist() { $("artist-sheet").classList.add("hidden"); }
document.addEventListener("click", (e) => {
  if (e.target.closest("[data-close]")) { closeArtist(); return; }
  const el = e.target.closest("button[data-artist]");
  if (!el) return;
  const { artist, cancel } = JSON.parse(el.dataset.artist);
  $("cancel-form").classList.toggle("hidden", !!cancel);
  $("uncancel-form").classList.toggle("hidden", !cancel);
  if (cancel) {
    $("uncancel-artist").value = cancel.artist;
    $("uncancel-title").textContent = `🚫 ${cancel.artist} est cancel`;
    const by = cancel.by ? `Cancel par ${cancel.by}` : "Cancel";
    const when = `${by} le ${cancel.date}`; // "8 nov." already ends a sentence
    $("uncancel-text").textContent = `« ${cancel.reason} » ${when}${when.endsWith(".") ? "" : "."}`;
  } else {
    $("cancel-artist").value = artist;
    $("cancel-reason").value = "";
  }
  $("artist-sheet").classList.remove("hidden");
  if (!cancel) setTimeout(() => $(artist ? "cancel-reason" : "cancel-artist").focus(), 50);
});
// Done (`HX-Trigger: {"cancelled": true}`): the queue comes back by push, the
// results (or, before a search, the popular list) are fetched again for
// their flags.
document.body.addEventListener("cancelled", () => {
  closeArtist();
  $("search-form").requestSubmit();
});

// --- live connection ---------------------------------------------------------
// hx-ws reconnects by itself (closes 1001/1005/1006/1011-1014), and pauses
// the socket while the phone's screen is off.
document.addEventListener("htmx:ws:after:connection", () => document.body.classList.remove("offline"));
document.addEventListener("htmx:ws:close", (e) => {
  if (e.detail.code === 4404) { location.href = "/"; return; } // room gone
  if (!document.hidden) document.body.classList.add("offline");
});

// --- queue: drag and drop ---------------------------------------------------
// A push while a finger drags would yank the list: hold it, then catch up.
let dragging = false;
let missedPush = false;
document.addEventListener("htmx:ws:before:message:incoming", (e) => {
  if (dragging) { missedPush = true; e.preventDefault(); }
});
function refresh() {
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
      if (e.oldIndex === e.newIndex) { if (missedPush) refresh(); return; }
      // Then reload the list: a move that changed nothing (the song was
      // removed meanwhile) brings no push, and the dragged order would stay.
      htmx.ajax("POST", `/r/${CODE}/do`, {
        swap: "none",
        values: { type: "move", item_id: e.item.dataset.id, index: e.newIndex },
      }).finally(refresh);
    },
  });
}
sortable();

// --- reactions on the big screen ----------------------------------------------
// Quick taps are grouped into one message per kind (the server takes up to 10).
const REACT_BATCH = 10;
const REACT_EVERY = 250; // ms
const reacting = new Map();
let reactTimer = null;
function flushReactions() {
  reactTimer = null;
  for (const [kind, count] of reacting) {
    const body = new URLSearchParams({ type: "react", kind, count: Math.min(count, REACT_BATCH) });
    fetch(`/r/${CODE}/do`, { method: "POST", body }).catch(() => {});
  }
  reacting.clear();
}
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-react]");
  if (!btn) return;
  const kind = btn.dataset.react;
  reacting.set(kind, (reacting.get(kind) || 0) + 1);
  reactTimer ??= setTimeout(flushReactions, REACT_EVERY);
  navigator.vibrate?.(8);
  const puff = document.createElement("span");
  puff.className = "puff";
  puff.textContent = btn.dataset.emoji;
  puff.addEventListener("animationend", () => puff.remove());
  btn.append(puff);
});

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
  if (!myName()) { askName(); return; }
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
