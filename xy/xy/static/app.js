/* Deux axes: place candidates on a two-axis chart, then see where everyone
   put them. Coordinates are x, y in [-1, 1], y pointing up. */

const PALETTE = ['#e8734a', '#3a6ea5', '#2a9d8f', '#c9a227', '#7b4fa0', '#d1495b',
                 '#4f9d3a', '#8c5a3c', '#1b998b', '#b5446e', '#5c6bc0', '#6d6875'];
const MARGIN = 0.07;   // share of the field kept free around the [-1, 1] square
const DRAG_START = 6;  // px before a press becomes a drag

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
};

const store = {
    get(key, fallback) {
        try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
    },
    set(key, value) {
        try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ }
    },
};

function playerId() {
    let id = store.get('xy.player', null);
    if (!id) {
        id = crypto.randomUUID ? crypto.randomUUID()
            : '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, (c) =>
                (c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16));
        store.set('xy.player', id);
    }
    return id;
}

async function api(path, opts) {
    const res = await fetch(path, opts);
    if (!res.ok) throw new Error(`${res.status}`);
    return res.status === 204 ? null : res.json();
}

const state = {
    round: null,
    colors: {},
    positions: {},     // candidate id -> {x, y}: this player's answers
    mode: 'play',      // 'play' | 'results'
    selected: null,    // play: candidate armed for tap-to-place; results: candidate focused
    results: null,
};

const field = $('field');

/* ---------------- geometry ---------------- */

function toPx(x, y) {
    const r = field.getBoundingClientRect();
    const span = 1 - 2 * MARGIN;
    return {
        left: (MARGIN + (x + 1) / 2 * span) * r.width,
        top: (MARGIN + (1 - y) / 2 * span) * r.height,
    };
}

function fromClient(cx, cy) {
    const r = field.getBoundingClientRect();
    const span = 1 - 2 * MARGIN;
    const clamp = (v) => Math.max(-1, Math.min(1, v));
    const fx = ((cx - r.left) / r.width - MARGIN) / span;
    const fy = ((cy - r.top) / r.height - MARGIN) / span;
    return { x: clamp(fx * 2 - 1), y: clamp(1 - fy * 2) };
}

function insideField(cx, cy) {
    const r = field.getBoundingClientRect();
    return cx >= r.left && cx <= r.right && cy >= r.top && cy <= r.bottom;
}

/* ---------------- tokens ---------------- */

function token(c) {
    const t = el('button', 'tok');
    t.type = 'button';
    t.dataset.id = c.id;
    t.style.setProperty('--c', state.colors[c.id]);
    t.append(el('span', 'dot', c.emoji || c.name.slice(0, 1)), el('span', 'name', c.name));
    t.setAttribute('aria-label', c.name);
    return t;
}

function render() {
    const { round, mode } = state;
    $('tray').replaceChildren();
    $('tokens').replaceChildren();
    $('lines').replaceChildren();
    field.classList.toggle('armed', mode === 'play' && !!state.selected);

    if (mode === 'play') renderPlay(round); else renderResults(round);
    renderBar();
}

function renderPlay(round) {
    const tray = $('tray');
    for (const c of round.candidates) {
        const placed = state.positions[c.id];
        const t = token(c);
        if (placed) {
            const p = token(c);
            const { left, top } = toPx(placed.x, placed.y);
            Object.assign(p.style, { left: `${left}px`, top: `${top}px` });
            $('tokens').append(p);
            bindDrag(p, c);
        } else {
            if (state.selected === c.id) t.classList.add('selected');
            tray.append(t);
            bindDrag(t, c);
        }
    }
    const left = round.candidates.filter((c) => !state.positions[c.id]).length;
    $('hint').textContent = left
        ? 'Glisse chaque candidat sur le graphique (ou touche-le, puis touche le graphique).'
        : 'Tout le monde est placé. Tu peux encore les déplacer, puis valider.';
}

function svg(tag, attrs) {
    const e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    return e;
}

function renderResults(round) {
    const lines = $('lines');
    const byId = Object.fromEntries((state.results?.candidates ?? []).map((r) => [r.candidate, r]));
    const focus = state.selected;

    if (focus && byId[focus]) {
        for (const [x, y] of byId[focus].points) {
            const p = toPx(x, y);
            lines.append(svg('circle', { cx: p.left, cy: p.top, r: 4, fill: state.colors[focus], class: 'cloud' }));
        }
    }

    for (const c of round.candidates) {
        const r = byId[c.id];
        const mine = state.positions[c.id];
        if (!r) continue;
        const med = toPx(r.median_x, r.median_y);
        if (mine && (!focus || focus === c.id)) {
            const m = toPx(mine.x, mine.y);
            lines.append(svg('line', {
                x1: m.left, y1: m.top, x2: med.left, y2: med.top,
                stroke: state.colors[c.id], 'stroke-width': 1.5, 'stroke-dasharray': '4 3', opacity: 0.8,
            }));
            lines.append(svg('circle', { cx: m.left, cy: m.top, r: 5, stroke: state.colors[c.id], class: 'mine' }));
        }
        const t = token(c);
        if (focus === c.id) t.querySelector('.name').textContent = `${c.name} (${r.count} réponse${r.count > 1 ? 's' : ''})`;
        Object.assign(t.style, { left: `${med.left}px`, top: `${med.top}px` });
        if (focus) t.classList.add(focus === c.id ? 'focus' : 'faded');
        t.addEventListener('click', () => {
            state.selected = state.selected === c.id ? null : c.id;
            render();
        });
        $('tokens').append(t);
    }

    const n = state.results?.players ?? 0;
    $('hint').textContent = `Position médiane de chaque candidat, sur ${n} joueur${n > 1 ? 's' : ''}. `
        + 'Les cercles en pointillé sont tes réponses. Touche un candidat pour voir toutes les réponses.';
    $('tray').append(el('p', 'legend', focus
        ? 'Touche à nouveau le candidat pour revenir à la vue d\'ensemble.'
        : 'Touche un candidat pour voir où chacun l\'a placé.'));
}

function renderBar() {
    const btn = $('mainBtn');
    const placed = Object.keys(state.positions).length;
    if (state.mode === 'play') {
        btn.textContent = 'Valider';
        btn.disabled = placed === 0;
        $('shareBtn').hidden = true;
        $('status').textContent = `${placed} / ${state.round.candidates.length} placés`;
    } else {
        btn.textContent = 'Modifier mes réponses';
        btn.disabled = false;
        $('shareBtn').hidden = false;
        $('status').textContent = '';
    }
}

/* ---------------- drag and tap ---------------- */

function bindDrag(t, c) {
    t.addEventListener('pointerdown', (ev) => {
        if (state.mode !== 'play' || ev.button > 0) return;
        // Touch in the tray must stay free to scroll it sideways
        if (ev.pointerType === 'mouse' || state.positions[c.id]) ev.preventDefault();
        const startX = ev.clientX, startY = ev.clientY;
        let ghost = null;

        const move = (e) => {
            if (!ghost && Math.hypot(e.clientX - startX, e.clientY - startY) < DRAG_START) return;
            if (!ghost) {
                ghost = token(c);
                ghost.classList.add('ghost-tok');
                document.body.append(ghost);
                t.style.visibility = 'hidden';
            }
            ghost.style.left = `${e.clientX}px`;
            ghost.style.top = `${e.clientY}px`;
        };
        const up = (e) => {
            window.removeEventListener('pointermove', move);
            window.removeEventListener('pointerup', up);
            window.removeEventListener('pointercancel', up);
            if (!ghost) {   // a tap, or the browser took over to scroll the tray
                if (e.type === 'pointercancel') return;
                if (!state.positions[c.id]) {
                    state.selected = state.selected === c.id ? null : c.id;
                    render();
                }
                return;
            }
            ghost.remove();
            if (e.type !== 'pointercancel' && insideField(e.clientX, e.clientY)) {
                state.positions[c.id] = fromClient(e.clientX, e.clientY);
            } else {
                delete state.positions[c.id];
            }
            if (state.selected === c.id) state.selected = null;
            saveDraft();
            render();
        };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
        window.addEventListener('pointercancel', up);
    });
}

field.addEventListener('click', (ev) => {
    if (state.mode !== 'play' || !state.selected || ev.target.closest('.tok')) return;
    state.positions[state.selected] = fromClient(ev.clientX, ev.clientY);
    const next = state.round.candidates.find((c) => !state.positions[c.id]);
    state.selected = next ? next.id : null;
    saveDraft();
    render();
});

/* ---------------- submit, results ---------------- */

function saveDraft() {
    store.set(`xy.draft.${state.round.id}`, state.positions);
}

async function submit() {
    $('confirmPop').hidden = true;
    const btn = $('mainBtn');
    btn.disabled = true;
    $('status').textContent = 'Envoi…';
    try {
        await api(`/api/rounds/${state.round.id}/answers/${playerId()}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ positions: state.positions }),
        });
        store.set(`xy.answers.${state.round.id}`, state.positions);
        await showResults();
    } catch {
        $('status').textContent = 'Envoi impossible, réessaie.';
        btn.disabled = false;
    }
}

async function showResults() {
    state.results = await api(`/api/rounds/${state.round.id}/results`);
    state.mode = 'results';
    state.selected = null;
    render();
}

$('mainBtn').addEventListener('click', () => {
    if (state.mode === 'results') {
        state.mode = 'play';
        state.selected = null;
        render();
        return;
    }
    const missing = state.round.candidates.length - Object.keys(state.positions).length;
    if (missing > 0) {
        $('popText').textContent = missing === 1
            ? 'Il reste un candidat à placer.'
            : `Il reste ${missing} candidats à placer.`;
        $('confirmPop').hidden = false;
        return;
    }
    submit();
});
$('popOk').addEventListener('click', submit);
$('popCancel').addEventListener('click', () => { $('confirmPop').hidden = true; });

$('shareBtn').addEventListener('click', async () => {
    const url = `${location.origin}/?r=${state.round.id}`;
    const text = `${state.round.title} : place les candidats sur les deux axes`;
    if (navigator.share) {
        try { await navigator.share({ title: 'Deux axes', text, url }); } catch { /* cancelled */ }
        return;
    }
    try {
        await navigator.clipboard.writeText(url);
        $('status').textContent = 'Lien copié';
    } catch {
        $('status').textContent = url;
    }
});

let resizeFrame = 0;
window.addEventListener('resize', () => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => state.round && render());
});

/* ---------------- boot ---------------- */

async function boot() {
    const rounds = await api('/api/rounds');
    if (!rounds.length) {
        $('title').textContent = 'Aucune manche pour l\'instant';
        return;
    }
    const wanted = new URLSearchParams(location.search).get('r');
    const current = rounds.find((r) => r.id === wanted) ?? rounds[0];

    if (rounds.length > 1) {
        $('roundsBox').hidden = false;
        $('rounds').replaceChildren(...rounds.map((r) => {
            const a = el('a', '', r.title);
            a.href = `/?r=${r.id}`;
            if (r.id === current.id) a.setAttribute('aria-current', 'page');
            a.append(el('span', '', new Date(r.date).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })));
            return a;
        }));
    }

    const round = await api(`/api/rounds/${current.id}`);
    state.round = round;
    round.candidates.forEach((c, i) => { state.colors[c.id] = PALETTE[i % PALETTE.length]; });
    document.title = `${round.title} · Deux axes`;
    $('title').textContent = round.title;
    $('labXn').textContent = round.axes.x.neg;
    $('labXp').textContent = round.axes.x.pos;
    $('labYn').textContent = round.axes.y.neg;
    $('labYp').textContent = round.axes.y.pos;

    const answered = store.get(`xy.answers.${round.id}`, null);
    const known = (p) => Object.fromEntries(Object.entries(p ?? {})
        .filter(([id]) => round.candidates.some((c) => c.id === id)));
    if (answered) {
        state.positions = known(answered);
        await showResults();
    } else {
        state.positions = known(store.get(`xy.draft.${round.id}`, {}));
        render();
    }
}

boot().catch(() => {
    $('title').textContent = 'Le jeu ne répond pas, réessaie plus tard.';
});
