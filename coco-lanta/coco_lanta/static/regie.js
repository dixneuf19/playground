// The control room: pick what is on air, scores, timer, banners.
const { Room, RoomEvent, Track, VideoQuality } = LivekitClient;

const $ = (id) => document.getElementById(id);
const cams = $("cams");
const videos = new Map(); // camera id -> RemoteVideoTrack
let current = null;

const show = connectShow("regie", () => ({ key: stored("regie-key") }), {
  state(s) { current = s; render(); },
  event(msg) { if (msg.type === "comment") comment(msg); },
  closed(code) {
    if (code !== 4401) return true;
    const key = prompt("Mot de passe de la régie");
    if (key == null) return false;
    store("regie-key", key);
    return true;
  },
});
const cmd = (name, args) => show.send({ type: "cmd", cmd: name, ...args });

function render() {
  const s = current;
  const ids = new Set(s.cameras.map((c) => c.id));
  cams.querySelectorAll(".cam").forEach((el) => { if (!ids.has(el.dataset.id)) el.remove(); });
  for (const cam of s.cameras) {
    let el = cams.querySelector(`.cam[data-id="${cam.id}"]`);
    if (!el) {
      el = document.createElement("div");
      el.className = "cam";
      el.dataset.id = cam.id;
      el.innerHTML = '<video autoplay playsinline muted></video><div class="label"><b></b></div><button class="sound" title="Son de cette caméra dans la mosaïque"></button><button class="kick" title="Couper cette caméra">✕</button>';
      cams.append(el);
      videos.get(cam.id)?.attach(el.querySelector("video"));
    }
    el.classList.toggle("on", s.on_air.includes(cam.id));
    const heard = s.heard.includes(cam.id);
    el.querySelector(".sound").textContent = heard ? "🔊" : "🔇";
    el.querySelector(".sound").classList.toggle("on", heard);
    el.querySelector("b").textContent = cam.name;
  }
  cams.dataset.layout = s.layout;
  $("empty").hidden = s.cameras.length > 0;
  $("auto").classList.toggle("on", s.auto && s.layout === "solo");
  document.querySelectorAll("#layouts button").forEach((b) => b.classList.toggle("on", b.dataset.layout === s.layout));
  for (const team of ["faucille", "marteau"]) $(`score-${team}`).textContent = s.scores[team];
  $("audio").classList.toggle("on", s.audio);
  $("qr").classList.toggle("on", s.qr);
  if (document.activeElement !== $("banner-text")) $("banner-text").value = s.banner;
}

setInterval(() => {
  const timer = current && formatTimer(current.timer);
  $("timer").textContent = timer ? timer.text : "";
}, 200);

function comment(msg) {
  const box = $("comments");
  if (box.classList.contains("hint")) { box.textContent = ""; box.classList.remove("hint"); }
  const el = document.createElement("div");
  el.innerHTML = `<b>${esc(msg.name)}</b> ${esc(msg.text)}`;
  box.prepend(el);
  while (box.childElementCount > 20) box.lastChild.remove();
}

cams.addEventListener("click", (e) => {
  const cam = e.target.closest(".cam");
  if (!cam) return;
  if (e.target.closest(".sound")) {
    cmd("hear", { id: cam.dataset.id, on: !current.heard.includes(cam.dataset.id) });
  } else if (e.target.closest(".kick")) {
    if (confirm("Couper cette caméra ?")) cmd("kick", { id: cam.dataset.id });
  } else {
    cmd("take", { id: cam.dataset.id });
  }
});
$("auto").addEventListener("click", () => { cmd("layout", { layout: "solo" }); cmd("auto"); });
$("layouts").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) cmd("layout", { layout: b.dataset.layout });
});
document.querySelectorAll("[data-score]").forEach((b) => b.addEventListener("click", () => {
  cmd("score", { team: b.dataset.score, delta: Number(b.dataset.delta) });
}));
$("score-reset").addEventListener("click", () => { if (confirm("Remettre les scores à zéro ?")) cmd("score_reset"); });
document.querySelectorAll("[data-timer]").forEach((b) => b.addEventListener("click", () => {
  cmd("timer_start", { seconds: Number(b.dataset.timer) });
}));
$("timer-toggle").addEventListener("click", () => cmd("timer_toggle"));
$("timer-reset").addEventListener("click", () => cmd("timer_reset"));
$("banner").addEventListener("submit", (e) => {
  e.preventDefault();
  cmd("banner", { text: $("banner-text").value });
  $("banner-text").blur();
});
$("banner-clear").addEventListener("click", () => cmd("banner", { text: "" }));
$("stingers").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (b) cmd("stinger", { text: b.textContent });
});
$("stinger").addEventListener("submit", (e) => {
  e.preventDefault();
  cmd("stinger", { text: $("stinger-text").value });
  $("stinger-text").value = "";
});
$("audio").addEventListener("click", () => cmd("audio", { on: !current.audio }));
$("qr").addEventListener("click", () => cmd("qr", { on: !current.qr }));

// Silent thumbnails of every camera.
async function watch() {
  const { url, token } = await (await fetch("/api/viewer", { method: "POST" })).json();
  const room = new Room({ dynacast: true });
  room.on(RoomEvent.TrackSubscribed, (track, pub, participant) => {
    if (track.kind !== Track.Kind.Video) { pub.setSubscribed(false); return; }
    pub.setVideoQuality(VideoQuality.LOW); // thumbnails: easy on a phone
    videos.set(participant.identity, track);
    const el = cams.querySelector(`.cam[data-id="${participant.identity}"] video`);
    if (el) track.attach(el);
  });
  room.on(RoomEvent.TrackUnsubscribed, (track, _pub, participant) => {
    if (track.kind !== Track.Kind.Video) return;
    track.detach();
    videos.delete(participant.identity);
  });
  room.on(RoomEvent.Disconnected, () => setTimeout(watch, 3000));
  await room.connect(url, token);
}
watch().catch(() => setTimeout(watch, 3000));
