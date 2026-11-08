// Big screen: the player. Follows the room state pushed as JSON (common.js).
const TURN_SECONDS = 6;
const video = document.getElementById("video");
const $ = (id) => document.getElementById(id);
let room = null;
let loadedId = null;
let turnTimer = null;
let unlocked = false;
let roomAt = 0; // when the last state arrived, to extrapolate `position`
const REWIND = 3; // seconds replayed on resume so the singer finds their place

document.querySelectorAll(".qr").forEach((img) => (img.src = `/api/rooms/${ROOM_CODE}/qr.svg`));
document.querySelectorAll(".room-code").forEach((el) => (el.textContent = ROOM_CODE));
fetch(`/api/rooms/${ROOM_CODE}`).then((r) => r.json()).then((r) => {
  $("idle").querySelector(".join-url").textContent = r.join_url.replace(/^https?:\/\//, "");
});

const sock = connectRoom("display", onState, onCommand);

$("start").onclick = () => {
  unlocked = true;
  $("gate").classList.add("hidden");
  document.documentElement.requestFullscreen?.().catch(() => {});
  if (room) render();
};

function onState(r) {
  room = r;
  roomAt = Date.now();
  render();
  renderPhoto();
}

// Never show who sent a photo: guessing is part of the fun.
let shownPhotoId = null;
const CORNERS = 3;
let cornerIndex = -1;
function renderPhoto() {
  const el = $("photo");
  const playing = !!room.current && !$("stage").classList.contains("hidden");
  el.classList.toggle("corner", playing);
  el.classList.toggle("center", !playing);
  const p = room.photo;
  if (!p) { el.classList.remove("show"); shownPhotoId = null; return; }
  if (p.id === shownPhotoId) return;
  shownPhotoId = p.id;
  cornerIndex = (cornerIndex + 1) % CORNERS;
  el.classList.remove("pos-0", "pos-1", "pos-2");
  el.classList.add(`pos-${cornerIndex}`);
  // a different small tilt for each photo, alternating sides
  const tilt = (1.5 + Math.random() * 2.5) * (cornerIndex % 2 ? 1 : -1);
  el.style.setProperty("--tilt", `${tilt.toFixed(1)}deg`);
  el.classList.remove("show");
  const img = el.querySelector("img");
  img.onload = () => {
    if (shownPhotoId !== p.id) return;
    el.classList.add("show");
  };
  img.src = `/photos/${ROOM_CODE}/${p.id}`;
}

function render() {
  const cur = room.current;
  if (!cur) {
    clearTimeout(turnTimer);
    loadedId = null;
    video.pause();
    video.removeAttribute("src");
    video.load();
    $("turn").classList.add("hidden");
    $("stage").classList.add("hidden");
    $("idle").classList.remove("hidden");
    document.body.classList.remove("playing");
    renderIdleQueue();
    return;
  }
  if (cur.id !== loadedId && unlocked) startSong(cur, resumePoint());
  $("singer-name").textContent = cur.singer;
  $("stage").classList.toggle("paused", room.state === "paused");
  $("pausebadge").classList.toggle("hidden", room.state !== "paused" || !video.src);
  video.volume = room.volume;
  syncPlayback();
  updateUpNext();
}

function renderIdleQueue() {
  const q = room.queue;
  $("idle-queue").innerHTML = q.length
    ? q.slice(0, 6).map((i) => `
      <div class="card qitem">
        <img src="${esc(i.thumbnail)}" alt="">
        <div class="meta"><div class="t">${esc(i.title)}</div><div class="a">${esc(i.artist)}</div></div>
        <div class="s">${i.cancelled ? "🚫 " : ""}${esc(i.singer)}</div>
      </div>`).join("")
    : `<div class="card empty">Personne n'a encore choisi de chanson.<br>À vous de jouer !</div>`;
  const loading = q.find((i) => i.status === "downloading" || i.status === "pending");
  $("prep").classList.toggle("hidden", !loading || q.some((i) => i.status === "ready"));
  if (loading) {
    const pct = loading.status === "downloading" ? ` — ${Math.round(loading.progress * 100)}%` : "";
    $("prep-text").textContent = `Préparation de « ${loading.title} »${pct}`;
  }
}

// Where the current song stands, from the server: 0 for a fresh song,
// further along when this screen was reloaded mid-song.
function resumePoint() {
  let t = room.position || 0;
  if (room.state === "playing" && t > 0) t += (Date.now() - roomAt) / 1000;
  return t > REWIND ? t - REWIND : 0;
}

function startSong(item, resumeAt = 0) {
  loadedId = item.id;
  clearTimeout(turnTimer);
  video.pause();
  video.src = `/media/${item.video_id}.mp4`;
  video.load();
  $("idle").classList.add("hidden");
  $("stage").classList.remove("hidden");
  document.body.classList.add("playing");

  if (resumeAt > 0) {
    // Reloaded mid-song: no announcement, straight back to where it was.
    $("turn").classList.add("hidden");
    video.addEventListener("loadedmetadata", () => {
      if (loadedId !== item.id) return;
      video.currentTime = Math.min(resumeAt, Math.max(0, video.duration - 1));
      if (room.state !== "paused") video.play().catch(() => {});
    }, { once: true });
    return;
  }

  $("turn-name").textContent = item.singer;
  $("turn-song").textContent = item.title;
  $("turn-artist").textContent = item.artist;
  $("turn-cancel").textContent = item.cancelled ? `🚫 ${item.cancelled}` : "";
  $("turn-cancel").classList.toggle("hidden", !item.cancelled);
  const name = $("turn-name");
  name.classList.remove("pop"); void name.offsetWidth; name.classList.add("pop");
  $("turn").classList.remove("hidden");

  let left = TURN_SECONDS;
  const tick = () => {
    if (loadedId !== item.id) return;
    if (left <= 0) {
      $("turn").classList.add("hidden");
      if (room.state !== "paused") video.play().catch(() => {});
      return;
    }
    $("turn-count").textContent = `Ça commence dans ${left}…`;
    left -= 1;
    turnTimer = setTimeout(tick, 1000);
  };
  tick();
}

// Play/pause also follow the state, not only the commands: a command lost
// while this screen was reconnecting is corrected on the next state push.
function syncPlayback() {
  const cur = room.current;
  if (!cur || cur.id !== loadedId || !video.src || video.readyState < 1) return;
  if (!$("turn").classList.contains("hidden") || video.ended) return;
  if (room.state === "paused" && !video.paused) video.pause();
  else if (room.state === "playing" && video.paused) video.play().catch(() => {});
}

function updateUpNext() {
  const next = room?.queue[0];
  const el = $("upnext");
  const t = video.currentTime, d = video.duration;
  const show = next && d && (t < 12 || d - t < 25);
  if (next) el.innerHTML = `À suivre : <b>${esc(next.title)}</b> — ${esc(next.singer)}`;
  el.classList.toggle("off", !show);
}

video.addEventListener("timeupdate", updateUpNext);
video.addEventListener("ended", () => sock.send({ type: "ended", item_id: loadedId }));
video.addEventListener("error", () => {
  // Keep the id of the song that failed: by the time the timer fires,
  // another song may be loaded and must not be skipped instead.
  const failed = loadedId;
  if (failed) setTimeout(() => sock.send({ type: "ended", item_id: failed }), 3000);
});
video.addEventListener("play", () => sock.send({ type: "player", state: "playing", t: video.currentTime }));
video.addEventListener("pause", () => {
  if (!video.ended && $("turn").classList.contains("hidden")) sock.send({ type: "player", state: "paused", t: video.currentTime });
});
// Tell the server where the song is, so a reloaded screen picks it up.
function reportPosition() {
  if (loadedId && video.src && !video.paused && !video.ended) {
    sock.send({ type: "position", item_id: loadedId, t: video.currentTime });
  }
}
setInterval(reportPosition, 5000);
video.addEventListener("seeked", reportPosition);
window.addEventListener("pagehide", reportPosition);

function onCommand(cmd) {
  if (cmd.action === "pause") video.pause();
  else if (cmd.action === "play" && $("turn").classList.contains("hidden")) video.play().catch(() => {});
  else if (cmd.action === "restart") { video.currentTime = 0; video.play().catch(() => {}); }
  else if (cmd.action === "volume") video.volume = cmd.value;
  else if (cmd.action === "react") react(cmd);
}

// --- reactions: emoji float up, effects rain down --------------------------
// Never shows who sent them. Live particles are capped so that a whole room
// spamming fills the screen without freezing the TV; effects get some extra
// room, so a confetti burst still shows when the screen is full of emoji.
const MAX_EMOJI = 80;
const MAX_PARTICLES = 200;
const CONFETTI = 70;
const HEARTS = 35;
const CONFETTI_COLORS = ["#ff3d8b", "#7b5cff", "#ffd23f", "#3ddcff", "#5dff8f", "#ffffff"];
const HEART_EMOJI = ["💖", "💗", "💕", "❤️", "💘"];
let particles = 0;
const rand = (a, b) => a + Math.random() * (b - a);
const pick = (list) => list[Math.floor(Math.random() * list.length)];

function spawn(cls, style, keyframes, timing, text = "") {
  const el = document.createElement("span");
  el.className = `particle ${cls}`;
  el.textContent = text;
  Object.assign(el.style, style);
  $("fx").append(el);
  particles++;
  const anim = el.animate(keyframes, { fill: "backwards", ...timing });
  anim.finished.catch(() => {}).finally(() => { el.remove(); particles--; });
}

function floatEmoji(emoji, delay) {
  const drift = rand(-6, 6);
  spawn("emoji", { left: `${rand(4, 84)}vw`, fontSize: `${rand(3, 5.5)}vw` }, [
    { transform: "translate(0, 15vh) scale(0.5)", opacity: 0 },
    { transform: `translate(${drift / 2}vw, -10vh) scale(1)`, opacity: 1, offset: 0.15 },
    { transform: `translate(${drift}vw, -55vh) scale(1.05)`, opacity: 1, offset: 0.7 },
    { transform: `translate(${-drift / 2}vw, -85vh) scale(1.15)`, opacity: 0 },
  ], { duration: rand(3200, 4600), delay, easing: "ease-out" }, emoji);
}

function fall(cls, style, text, spin) {
  const sway = rand(-10, 10);
  spawn(`fall ${cls}`, { left: `${rand(-2, 100)}vw`, ...style }, [
    { transform: "translate(0, -8vh) rotate(0deg)", opacity: 1 },
    { transform: `translate(${sway}vw, 50vh) rotate(${spin / 2}deg)`, opacity: 1, offset: 0.5 },
    { transform: `translate(${-sway / 2}vw, 108vh) rotate(${spin}deg)`, opacity: 0.9 },
  ], { duration: rand(2600, 4200), delay: rand(0, 900), easing: "cubic-bezier(.35,.55,.65,1)" }, text);
}

function react(cmd) {
  if (cmd.effect === "confetti") {
    for (let i = 0; i < CONFETTI && particles < MAX_PARTICLES; i++) {
      fall("confetti", { background: pick(CONFETTI_COLORS) }, "", rand(-900, 900));
    }
  } else if (cmd.effect === "hearts") {
    for (let i = 0; i < HEARTS && particles < MAX_PARTICLES; i++) {
      fall("heart", { fontSize: `${rand(2, 4)}vw` }, pick(HEART_EMOJI), rand(-40, 40));
    }
  } else if (cmd.emoji) {
    // a batch of quick taps from one phone, spread out a little
    for (let i = 0; i < (cmd.count || 1) && particles < MAX_EMOJI; i++) floatEmoji(cmd.emoji, i * 110);
  }
}

document.addEventListener("keydown", (e) => {
  if (e.key === "f" || e.key === "F") {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen?.();
  } else if (e.key === " " && room?.current) {
    e.preventDefault();
    sock.send({ type: video.paused ? "play" : "pause" });
  } else if ((e.key === "p" || e.key === "P") && room?.photo) {
    sock.send({ type: "photo_skip" });
  } else if ((e.key === "n" || e.key === "ArrowRight") && room?.current) {
    sock.send({ type: "skip" });
  }
});
