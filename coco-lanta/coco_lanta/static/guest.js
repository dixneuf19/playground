// The guests' phone page: emoji and comments for the TV and, for those who
// want to film, the camera and microphone published to LiveKit.

const $ = (id) => document.getElementById(id);
const nameInput = $("name");
let me = null; // { id, url, token } once on air
let room = null;
let camSocket = null;
let wakeLock = null;
let facing = "environment";

nameInput.value = stored("name");
nameInput.addEventListener("change", () => store("name", nameInput.value.trim()));

// --- reactions --------------------------------------------------------------

const show = connectShow("react", () => ({}), {});

function react(e) {
  const button = e.target.closest("button");
  if (!button) return;
  show.send({ type: "react", emoji: button.dataset.emoji });
  button.classList.remove("pop");
  void button.offsetWidth; // restart the animation
  button.classList.add("pop");
  navigator.vibrate?.(10);
}
$("emojis").addEventListener("click", react);
$("cheers").addEventListener("click", react);
$("sides").addEventListener("click", react);

$("comment").addEventListener("submit", (e) => {
  e.preventDefault();
  show.send({ type: "comment", name: nameInput.value, text: $("text").value });
  $("text").value = "";
});

// --- camera -----------------------------------------------------------------

// Filming needs a name: until there is one the button is greyed out, and a
// tap on it points at the name field instead of opening the camera panel.
function refreshForm() {
  const named = Boolean(nameInput.value.trim());
  $("film").classList.toggle("off", !named);
  $("film").setAttribute("aria-disabled", String(!named));
  $("go").disabled = !named;
  if (named) {
    $("name-hint").hidden = true;
    nameInput.classList.remove("missing");
  } else {
    $("camera").hidden = true;
  }
}
refreshForm();
nameInput.addEventListener("input", refreshForm);
$("film").addEventListener("click", () => {
  if (!nameInput.value.trim()) {
    $("name-hint").hidden = false;
    nameInput.classList.add("missing");
    nameInput.scrollIntoView({ behavior: "smooth", block: "center" });
    nameInput.focus({ preventScroll: true });
    return;
  }
  $("camera").hidden = !$("camera").hidden;
  if (!$("camera").hidden) $("camera").scrollIntoView({ behavior: "smooth" });
});

async function keepAwake() {
  try { wakeLock = await navigator.wakeLock?.request("screen"); } catch { /* the phone may sleep */ }
}
document.addEventListener("visibilitychange", () => {
  if (me && document.visibilityState === "visible") keepAwake();
});

function setStatus(text) { $("status").textContent = text; }

function cameraTrack() {
  return room.localParticipant.getTrackPublication(LivekitClient.Track.Source.Camera)?.track;
}

const NO_WIFI = "Impossible de joindre le plateau. Es-tu bien sur le Wi-Fi de la maison ? En 4G ou 5G, la caméra ne marche pas.";
const GO_LABEL = $("go").textContent;

function startError(err) {
  if (err.name === "NotAllowedError") return "Autorise la caméra pour ce site, puis réessaie.";
  if (err.name === "NotFoundError" || err.name === "NotReadableError") return "Impossible d'ouvrir la caméra de ce téléphone.";
  return NO_WIFI; // everything else is the network: the video only travels on the house Wi-Fi
}

async function start() {
  $("error").textContent = "";
  $("go").disabled = true;
  $("go").textContent = "Connexion au plateau…";
  const name = nameInput.value.trim();
  store("name", name);
  try {
    // Looked up here, not at load: reactions work even if the CDN did not answer.
    const { Room, RoomEvent, VideoPresets } = LivekitClient;
    const response = await fetch("/api/cam", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    if (!response.ok) throw new Error("Le serveur a refusé");
    me = await response.json();

    room = new Room({
      videoCaptureDefaults: { facingMode: facing, resolution: VideoPresets.h720.resolution },
      publishDefaults: { simulcast: true, videoEncoding: VideoPresets.h720.encoding },
    });
    room.on(RoomEvent.Reconnecting, () => setStatus("Reconnexion…"));
    room.on(RoomEvent.Reconnected, () => setStatus(""));
    room.on(RoomEvent.Disconnected, () => { if (me) stopCamera("Connexion vidéo perdue. Vérifie que tu es sur le Wi-Fi de la maison, puis relance la caméra."); });
    await room.connect(me.url, me.token);
    await room.localParticipant.setCameraEnabled(true);
    // The microphone starts off: whoever films turns it on when it is worth hearing.
    showMic(false);
    cameraTrack().attach($("preview"));
  } catch (err) {
    await stopCamera(startError(err));
    return;
  }
  $("setup").hidden = true;
  $("live").hidden = false;
  keepAwake();
  // Only now does the show know this camera: it is really publishing.
  camSocket = connectShow("cam", () => ({ id: me.id, name }), {
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
  camSocket?.close();
  camSocket = null;
  const old = room;
  room = null;
  await old?.disconnect();
  wakeLock?.release().catch(() => {});
  document.body.classList.remove("on-air");
  $("live").hidden = true;
  $("setup").hidden = false;
  $("error").textContent = message || "";
  $("go").textContent = GO_LABEL;
  if (message) $("camera").hidden = false; // keep the way back to the camera in sight
  refreshForm();
}

$("go").addEventListener("click", start);
$("stop").addEventListener("click", () => stopCamera(""));
$("flip").addEventListener("click", async () => {
  facing = facing === "environment" ? "user" : "environment";
  await cameraTrack()?.restartTrack({ facingMode: facing });
});
function showMic(on) {
  $("mic").classList.toggle("off", !on);
  $("mic").textContent = on ? "🎙️" : "🔇";
  $("mic").title = on ? "Couper le micro" : "Activer le micro";
}

$("mic").addEventListener("click", async () => {
  const on = !room.localParticipant.isMicrophoneEnabled;
  try {
    await room.localParticipant.setMicrophoneEnabled(on);
    setStatus("");
  } catch {
    setStatus("Micro refusé : autorise-le pour ce site.");
  }
  showMic(room.localParticipant.isMicrophoneEnabled);
});
