// Phone page: publish the camera and microphone to LiveKit, show the tally.
const { Room, RoomEvent, Track, VideoPresets } = LivekitClient;

const $ = (id) => document.getElementById(id);
const nameInput = $("name");
let team = stored("team");
let me = null; // { id, url, token } once on air
let room = null;
let show = null;
let wakeLock = null;
let facing = "environment";

nameInput.value = stored("name");

function refreshForm() {
  document.querySelectorAll("#teams button").forEach((b) => {
    b.classList.toggle("on", b.dataset.team === team);
  });
  $("go").disabled = !nameInput.value.trim() || !team;
}
refreshForm();
nameInput.addEventListener("input", refreshForm);
$("teams").addEventListener("click", (e) => {
  const button = e.target.closest("button");
  if (!button) return;
  team = button.dataset.team;
  refreshForm();
});

async function keepAwake() {
  try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { /* the phone may sleep */ }
}
document.addEventListener("visibilitychange", () => {
  if (me && document.visibilityState === "visible") keepAwake();
});

function setStatus(text) { $("status").textContent = text; }

function cameraTrack() {
  return room.localParticipant.getTrackPublication(Track.Source.Camera)?.track;
}

async function start() {
  $("error").textContent = "";
  $("go").disabled = true;
  const name = nameInput.value.trim();
  store("name", name);
  store("team", team);
  try {
    const response = await fetch("/api/cam", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, team }),
    });
    if (!response.ok) throw new Error("Le serveur a refusé");
    me = await response.json();

    room = new Room({
      videoCaptureDefaults: { facingMode: facing, resolution: VideoPresets.h720.resolution },
      publishDefaults: { simulcast: true, videoEncoding: VideoPresets.h720.encoding },
    });
    room.on(RoomEvent.Reconnecting, () => setStatus("Reconnexion…"));
    room.on(RoomEvent.Reconnected, () => setStatus(""));
    room.on(RoomEvent.Disconnected, () => { if (me) stopCamera("Connexion vidéo perdue. Relance la caméra."); });
    await room.connect(me.url, me.token);
    await room.localParticipant.setCameraEnabled(true);
    // No microphone (refused, or none) still makes a fine camera.
    try { await room.localParticipant.setMicrophoneEnabled(true); } catch { $("mic").classList.add("off"); }
    cameraTrack().attach($("preview"));
  } catch (err) {
    await stopCamera(err.name === "NotAllowedError" ? "Autorise la caméra pour ce site, puis réessaie." : `Impossible de démarrer : ${err.message}`);
    return;
  }
  $("setup").hidden = true;
  $("live").hidden = false;
  keepAwake();
  // Only now does the show know this camera: it is really publishing.
  show = connectShow("cam", () => ({ id: me.id, name, team }), {
    state(s) {
      const onAir = s.on_air.includes(me.id);
      document.body.classList.toggle("on-air", onAir);
      $("tally").textContent = onAir ? "● À L'ANTENNE" : "EN ATTENTE";
    },
    closed(code) {
      if (code !== 4403) return true;
      stopCamera("La régie a coupé ta caméra.");
      return false;
    },
  });
}

async function stopCamera(message) {
  me = null;
  show?.close();
  show = null;
  const old = room;
  room = null;
  await old?.disconnect();
  wakeLock?.release().catch(() => {});
  document.body.classList.remove("on-air", "offline");
  $("live").hidden = true;
  $("setup").hidden = false;
  $("error").textContent = message || "";
  refreshForm();
}

$("go").addEventListener("click", start);
$("stop").addEventListener("click", () => stopCamera(""));
$("flip").addEventListener("click", async () => {
  facing = facing === "environment" ? "user" : "environment";
  await cameraTrack()?.restartTrack({ facingMode: facing });
});
$("mic").addEventListener("click", async () => {
  const on = !room.localParticipant.isMicrophoneEnabled;
  await room.localParticipant.setMicrophoneEnabled(on);
  $("mic").classList.toggle("off", !on);
});
