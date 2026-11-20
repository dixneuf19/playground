// Shared by every page: the show socket (auto-reconnect), escaping, team art.
/* exported connectShow, esc, TEAMS, teamIcon, formatTimer, stored, store */

const TEAMS = { faucille: "Rouge Faucille", marteau: "Rouge Marteau" };

const ICONS = {
  faucille:
    '<svg viewBox="0 0 100 100"><g transform="translate(0 -6)"><path d="M36 12A40 40 0 1 1 30 82L42 72A31 31 0 1 0 36 12Z"/><rect x="6" y="80.5" width="38" height="12" rx="6" transform="rotate(-40.4 25 86.5)"/></g></svg>',
  marteau:
    '<svg viewBox="0 0 100 100"><g transform="translate(50 50) rotate(-45)"><rect x="-6.5" y="-33" width="13" height="84" rx="4"/><rect x="-23" y="-50" width="46" height="23" rx="3"/></g></svg>',
  camera:
    '<svg viewBox="0 0 24 24"><rect x="2" y="6" width="14" height="12" rx="2"/><path d="M16 10.5l6-3v9l-6-3z"/></svg>',
};

function teamIcon(name) {
  return ICONS[name] || "";
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// localStorage may be blocked (private windows): the pages work without it.
function stored(key) {
  try { return localStorage.getItem(`coco-${key}`) || ""; } catch { return ""; }
}
function store(key, value) {
  try { localStorage.setItem(`coco-${key}`, value); } catch { /* not kept */ }
}

// The server's clock minus ours, so timers agree on every screen.
let clockOffset = 0;

function formatTimer(timer) {
  let left = null;
  if (timer.ends != null) left = timer.ends - (Date.now() / 1000 + clockOffset);
  else if (timer.left != null) left = timer.left;
  if (left == null) return null;
  left = Math.max(0, Math.ceil(left));
  return { text: `${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}`, left };
}

// handlers: state(show), event(msg), closed(code) -> false to stop reconnecting.
function connectShow(role, params, handlers) {
  let ws;
  let retry = 0;
  let stopped = false;

  function open() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const query = new URLSearchParams({ role, ...params() });
    ws = new WebSocket(`${proto}://${location.host}/ws?${query}`);
    ws.onopen = () => { retry = 0; document.body.classList.remove("offline"); };
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "state") {
        clockOffset = msg.now - Date.now() / 1000;
        handlers.state?.(msg.show);
      } else {
        handlers.event?.(msg);
      }
    };
    ws.onclose = (e) => {
      if (stopped || handlers.closed?.(e.code) === false) return;
      document.body.classList.add("offline");
      setTimeout(open, Math.min(500 * 2 ** retry++, 5000));
    };
  }
  open();

  return {
    send(msg) { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg)); },
    close() { stopped = true; ws.close(); },
    reopen() { stopped = false; open(); },
  };
}

document.querySelectorAll("[data-icon]").forEach((el) => { el.innerHTML = teamIcon(el.dataset.icon); });
