// The big screen: plays what the show says is on air, with the TV dressing.
const { Room, RoomEvent, Track } = LivekitClient;

const $ = (id) => document.getElementById(id);
const stage = $("stage");
const tiles = new Map(); // camera id -> { el, audio: RemoteAudioTrack | null }
let current = null; // last show state
let room = null;
let audioCtx = null;

function tile(id) {
  let t = tiles.get(id);
  if (!t) {
    const el = document.createElement("div");
    el.className = "tile";
    el.innerHTML = `<video autoplay playsinline muted></video><div class="label"><span class="icon">${teamIcon("camera")}</span><b></b></div>`;
    stage.append(el);
    t = { el, audio: null };
    tiles.set(id, t);
  }
  return t;
}

function render() {
  const s = current;
  if (!s) return;
  const cams = new Map(s.cameras.map((c) => [c.id, c]));
  for (const [id, t] of tiles) {
    const cam = cams.get(id);
    const onAir = s.on_air.includes(id);
    t.el.hidden = !cam;
    t.el.classList.toggle("on", onAir);
    t.audio?.setVolume(s.audio && onAir ? 1 : 0);
    if (!cam) continue;
    t.el.style.order = s.layout === "duel" ? s.duo.indexOf(id) : 0;
    t.el.querySelector("b").textContent = cam.name;
  }
  stage.dataset.layout = s.layout;
  stage.dataset.count = s.on_air.length;
  document.body.classList.toggle("idle", s.on_air.length === 0);
  for (const team of ["faucille", "marteau"]) $(`score-${team}`).textContent = s.scores[team];
  $("banner").hidden = !s.banner;
  $("banner").textContent = s.banner;
  $("corner-qr").hidden = !s.qr || s.on_air.length === 0;
}

let timerWasRunning = false;
setInterval(() => {
  const timer = current && formatTimer(current.timer);
  $("timer").hidden = !timer;
  if (!timer) { timerWasRunning = false; return; }
  $("timer").textContent = timer.text;
  $("timer").classList.toggle("urgent", timer.left <= 10);
  if (timer.left === 0 && timerWasRunning) drum();
  timerWasRunning = timer.left > 0;
}, 200);

// --- effects ---------------------------------------------------------------

function drum() {
  if (!audioCtx) return;
  const now = audioCtx.currentTime;
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.frequency.setValueAtTime(140, now);
  osc.frequency.exponentialRampToValueAtTime(40, now + 0.5);
  gain.gain.setValueAtTime(0.9, now);
  gain.gain.exponentialRampToValueAtTime(0.001, now + 1.2);
  osc.connect(gain).connect(audioCtx.destination);
  osc.start(now);
  osc.stop(now + 1.2);
}

function float(emoji) {
  const fx = $("fx");
  if (fx.childElementCount > 80) return;
  const el = document.createElement("span");
  if (emoji in TEAMS) { // a cheer: the team's icon on its colour
    el.className = `cheer team-${emoji}`;
    el.innerHTML = teamIcon(emoji);
  } else {
    el.textContent = emoji;
  }
  el.style.left = `${5 + Math.random() * 90}%`;
  el.style.fontSize = `${3 + Math.random() * 3}vw`;
  el.style.animationDuration = `${3 + Math.random() * 2}s`;
  el.addEventListener("animationend", () => el.remove());
  fx.append(el);
}

function comment(msg) {
  const el = document.createElement("div");
  el.innerHTML = `<b>${esc(msg.name)}</b> ${esc(msg.text)}`;
  $("ticker").append(el);
  while ($("ticker").childElementCount > 4) $("ticker").firstChild.remove();
  setTimeout(() => el.remove(), 12000);
}

let stingerTimer;
function stinger(text) {
  const el = $("stinger");
  el.querySelector("span").textContent = text;
  el.hidden = false;
  el.classList.remove("play");
  void el.offsetWidth; // restart the animation
  el.classList.add("play");
  drum();
  clearTimeout(stingerTimer);
  stingerTimer = setTimeout(() => { el.hidden = true; }, 4500);
}

// --- the show socket and the video room ------------------------------------

const show = connectShow("tv", () => ({}), {
  state(s) { current = s; render(); },
  event(msg) {
    if (msg.type === "react") float(msg.emoji);
    else if (msg.type === "comment") comment(msg);
    else if (msg.type === "stinger") stinger(msg.text);
  },
});

async function watch() {
  const { url, token } = await (await fetch("/api/viewer", { method: "POST" })).json();
  // Every camera keeps streaming at full quality, so cuts are instant.
  room = new Room({ adaptiveStream: false, dynacast: true });
  room.on(RoomEvent.TrackSubscribed, (track, _pub, participant) => {
    const t = tile(participant.identity);
    if (track.kind === Track.Kind.Video) {
      track.attach(t.el.querySelector("video"));
    } else {
      t.audio = track;
      track.attach();
    }
    render();
  });
  room.on(RoomEvent.TrackUnsubscribed, (track, _pub, participant) => {
    track.detach().forEach((el) => { if (el.tagName === "AUDIO") el.remove(); });
    const t = tiles.get(participant.identity);
    if (t && track.kind === Track.Kind.Audio) t.audio = null;
  });
  room.on(RoomEvent.ParticipantDisconnected, (participant) => {
    tiles.get(participant.identity)?.el.remove();
    tiles.delete(participant.identity);
  });
  // The automatic director cuts to whoever is loud.
  room.on(RoomEvent.ActiveSpeakersChanged, (speakers) => {
    show.send({ type: "speakers", ids: speakers.map((p) => p.identity) });
  });
  room.on(RoomEvent.Disconnected, () => setTimeout(() => location.reload(), 3000));
  await room.connect(url, token);
}

$("start").addEventListener("click", async () => {
  $("gate").remove();
  audioCtx = new AudioContext();
  document.documentElement.requestFullscreen?.().catch(() => {});
  await room?.startAudio();
});

watch().catch(() => setTimeout(() => location.reload(), 3000));
