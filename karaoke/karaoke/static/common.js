// Screen page: room socket with auto-reconnect (the phone page uses htmx).
const ROOM_CODE = location.pathname.split("/").filter(Boolean).pop().toUpperCase();

function connectRoom(role, onState, onCommand) {
  let ws;
  let retry = 0;
  const status = { connected: false };

  function open() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(`${proto}://${location.host}/ws/${ROOM_CODE}?role=${role}`);
    ws.onopen = () => { retry = 0; status.connected = true; document.body.classList.remove("offline"); };
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "state") onState(msg.room);
      else if (msg.type === "command" && onCommand) onCommand(msg);
    };
    ws.onclose = (e) => {
      status.connected = false;
      document.body.classList.add("offline");
      if (e.code === 4404) { location.href = "/"; return; }
      setTimeout(open, Math.min(1000 * 2 ** retry++, 10000));
    };
  }
  open();

  return {
    send(msg) { if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg)); },
    status,
  };
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
