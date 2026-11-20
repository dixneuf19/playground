// Shared by every page: the show socket (auto-reconnect), escaping, team art.
/* exported connectShow, esc, TEAMS, teamIcon, formatTimer, stored, store */

const TEAMS = {
  faucille: "Rouge Faucille",
  marteau: "Rouge Marteau",
  reporter: "Reporter",
};

const ICONS = {
  faucille:
    '<svg viewBox="0 0 100 100"><path d="M34 6c26 2 46 22 46 46 0 20-15 36-34 36-8 0-15-2-21-7 5 2 9 3 14 3 15 0 27-12 27-28 0-22-14-40-32-50z"/><rect x="12" y="68" width="12" height="30" rx="5" transform="rotate(45 18 83)"/></svg>',
  marteau:
    '<svg viewBox="0 0 100 100"><g transform="rotate(-45 50 50)"><rect x="18" y="10" width="64" height="26" rx="4"/><rect x="43" y="32" width="14" height="62" rx="5"/></g></svg>',
  reporter:
    '<svg viewBox="0 0 100 100"><rect x="38" y="8" width="24" height="50" rx="12"/><path d="M26 44v6a24 24 0 0 0 48 0v-6h-7v6a17 17 0 0 1-34 0v-6z"/><rect x="46" y="72" width="8" height="16"/><rect x="32" y="86" width="36" height="7" rx="3"/></svg>',
};

function teamIcon(team) {
  return ICONS[team] || "";
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
