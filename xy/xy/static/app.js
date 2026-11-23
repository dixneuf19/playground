/* Deux axes: place candidates on a two-axis chart, then compare with where
   everyone else put them. Coordinates are x, y in [-1, 1], y pointing up. */

const PALETTE = ['#e8734a', '#3a6ea5', '#2a9d8f', '#c9a227', '#7b4fa0', '#d1495b',
                 '#4f9d3a', '#8c5a3c', '#1b998b', '#b5446e', '#5c6bc0', '#6d6875'];
const MARGIN = 0.07;   // share of the field kept free around the [-1, 1] square
const DRAG_START = 6;  // px before a press becomes a drag
const HEAT_GRID = 64;  // heatmap resolution, cells per side
const HEAT_SIGMA = 0.13;
// Mean distance between two uniform random points in the [-1, 1] square:
// consensus is 0 % at that spread, 100 % when everyone agrees.
const RANDOM_SPREAD = 1.0428;

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
    mode: 'play',      // 'play' until the first submit, then 'results'
    selected: null,    // play: candidate armed for tap-to-place
    focus: null,       // results: candidate whose heatmap is shown
    medians: store.get('xy.medians', true),
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

function placeAt(t, x, y) {
    const { left, top } = toPx(x, y);
    Object.assign(t.style, { left: `${left}px`, top: `${top}px` });
}

function svg(tag, attrs, text) {
    const e = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text !== undefined) e.textContent = text;
    return e;
}

function resultsById() {
    return Object.fromEntries((state.results?.candidates ?? []).map((r) => [r.candidate, r]));
}

function render() {
    $('tray').replaceChildren();
    $('tokens').replaceChildren();
    $('lines').replaceChildren();
    clearHeat();
    $('consensus').hidden = true;
    field.classList.toggle('armed', state.mode === 'play' && !!state.selected);
    if (state.mode === 'play') renderPlay(); else renderResults();
    renderBar();
}

function renderPlay() {
    for (const c of state.round.candidates) {
        const placed = state.positions[c.id];
        if (placed) {
            const p = token(c);
            placeAt(p, placed.x, placed.y);
            $('tokens').append(p);
            bindGesture(p, c, true);
        } else {
            const t = token(c);
            if (state.selected === c.id) t.classList.add('selected');
            $('tray').append(t);
            bindGesture(t, c, true);
        }
    }
    const left = state.round.candidates.filter((c) => !state.positions[c.id]).length;
    $('hint').textContent = left
        ? 'Glisse chaque candidat sur le graphique (ou touche-le, puis touche le graphique). Tu peux les déplacer ensuite.'
        : 'Tout le monde est placé. Tu peux encore les déplacer, puis valider.';
}

function renderResults() {
    const byId = resultsById();
    const focus = state.focus;
    const lines = $('lines');

    // The tray lists every candidate: tap one for its heatmap, drag an
    // unplaced one onto the chart to answer for it too.
    for (const c of state.round.candidates) {
        const t = token(c);
        if (focus) t.classList.add(focus === c.id ? 'focus' : 'faded');
        if (state.positions[c.id]) t.classList.add('answered');
        $('tray').append(t);
        bindGesture(t, c, !state.positions[c.id]);
    }

    if (focus && byId[focus]) {
        const r = byId[focus];
        drawHeat(r.points, state.colors[focus]);
        const med = toPx(r.median_x, r.median_y);
        const { width, height } = field.getBoundingClientRect();
        const cross = { stroke: state.colors[focus], 'stroke-width': 1.5, 'stroke-dasharray': '3 4', opacity: 0.9 };
        lines.append(svg('line', { ...cross, x1: 0, x2: width, y1: med.top, y2: med.top }));
        lines.append(svg('line', { ...cross, y1: 0, y2: height, x1: med.left, x2: med.left }));
        const pct = Math.round(consensus(r.points) * 100);
        $('consensus').replaceChildren('Consensus : ', el('strong', '', `${pct} %`));
        $('consensus').hidden = false;
    }

    for (const c of state.round.candidates) {
        const r = byId[c.id];
        const mine = state.positions[c.id];
        const shown = !focus || focus === c.id;
        const color = state.colors[c.id];
        // Without your answer the token itself sits on the median: no marker
        if (r && mine && (state.medians || focus === c.id) && shown) {
            const med = toPx(r.median_x, r.median_y);
            const m = toPx(mine.x, mine.y);
            lines.append(svg('line', {
                x1: m.left, y1: m.top, x2: med.left, y2: med.top,
                stroke: color, 'stroke-width': 2.5, 'stroke-dasharray': '6 5', opacity: 0.85,
            }));
            lines.append(svg('circle', { cx: med.left, cy: med.top, r: 15, class: 'median', stroke: color }));
            lines.append(svg('text', { x: med.left, y: med.top, class: 'median-label' }, c.emoji || c.name.slice(0, 1)));
        }
        // Your answer is the token; with no answer, the token sits on the median
        const at = mine ?? (r ? { x: r.median_x, y: r.median_y } : null);
        if (!at) continue;
        const t = token(c);
        placeAt(t, at.x, at.y);
        if (!mine) t.classList.add('theirs');
        if (focus) t.classList.add(focus === c.id ? 'focus' : 'faded');
        $('tokens').append(t);
        bindGesture(t, c, !!mine);
    }

    $('hint').textContent = focus
        ? 'Toutes les réponses pour ce candidat. Touche-le à nouveau pour revenir.'
        : 'Les cercles en pointillé sont les points médians de tout le monde. '
          + 'Touche un candidat pour voir toutes les réponses, glisse les tiens pour changer d\'avis.';
}

function renderBar() {
    const btn = $('mainBtn');
    const placed = Object.keys(state.positions).length;
    const play = state.mode === 'play';
    btn.hidden = !play;
    $('shareBtn').hidden = play;
    $('medOpt').hidden = play;
    $('medCb').checked = state.medians;
    if (play) {
        btn.disabled = placed === 0;
        $('status').textContent = `${placed} / ${state.round.candidates.length} placés`;
    } else {
        const n = state.results?.players ?? 0;
        $('status').textContent = `${n.toLocaleString('fr-FR')} réponse${n > 1 ? 's' : ''}`;
    }
}

/* ---------------- heatmap, consensus ---------------- */

const heat = $('heat');

function clearHeat() {
    heat.getContext('2d').clearRect(0, 0, heat.width, heat.height);
}

function drawHeat(points, color) {
    const r = field.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    heat.width = Math.round(r.width * dpr);
    heat.height = Math.round(r.height * dpr);
    if (!points.length) return;

    // The grid covers the whole field, margins included, so the cloud fades
    // out instead of stopping at the edge of the [-1, 1] square
    const n = HEAT_GRID;
    const span = 1 - 2 * MARGIN;
    const coord = (k) => ((k + 0.5) / n - MARGIN) / span * 2 - 1;
    const grid = new Float32Array(n * n);
    let max = 0;
    for (let j = 0; j < n; j++) {
        const y = -coord(j);
        for (let i = 0; i < n; i++) {
            const x = coord(i);
            let d = 0;
            for (const [px, py] of points) {
                const dx = x - px, dy = y - py;
                d += Math.exp(-(dx * dx + dy * dy) / (2 * HEAT_SIGMA * HEAT_SIGMA));
            }
            grid[j * n + i] = d;
            if (d > max) max = d;
        }
    }

    const small = document.createElement('canvas');
    small.width = n;
    small.height = n;
    const sctx = small.getContext('2d');
    const img = sctx.createImageData(n, n);
    const [cr, cg, cb] = [1, 3, 5].map((k) => parseInt(color.slice(k, k + 2), 16));
    for (let k = 0; k < n * n; k++) {
        const v = grid[k] / max;
        img.data[k * 4] = cr;
        img.data[k * 4 + 1] = cg;
        img.data[k * 4 + 2] = cb;
        img.data[k * 4 + 3] = Math.round(215 * Math.pow(v, 0.8));
    }
    sctx.putImageData(img, 0, 0);

    const ctx = heat.getContext('2d');
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(small, 0, 0, heat.width, heat.height);
}

function consensus(points) {
    if (points.length < 2) return 1;
    let sum = 0, pairs = 0;
    for (let i = 0; i < points.length; i++) {
        for (let j = i + 1; j < points.length; j++) {
            sum += Math.hypot(points[i][0] - points[j][0], points[i][1] - points[j][1]);
            pairs++;
        }
    }
    return Math.max(0, 1 - sum / pairs / RANDOM_SPREAD);
}

/* ---------------- gestures: tap or drag ---------------- */

/* Mouse and pen go through pointer events. Touch goes through touch events,
   because mobile browsers (iOS Safari, Firefox) only let a page stop
   scrolling from a non-passive touchmove listener. */
function gesture(t, c, draggable, x0, y0) {
    let ghost = null;
    return {
        dragging: () => !!ghost,
        far: (x, y) => draggable && Math.hypot(x - x0, y - y0) >= DRAG_START,
        move(x, y) {
            if (!ghost) {
                ghost = token(c);
                ghost.classList.add('ghost-tok');
                document.body.append(ghost);
                t.style.visibility = 'hidden';
            }
            ghost.style.left = `${x}px`;
            ghost.style.top = `${y}px`;
        },
        end(x, y, cancelled) {
            if (!ghost) {
                if (!cancelled) tap(c);
                return;
            }
            ghost.remove();
            if (cancelled) { render(); return; }
            drop(c, insideField(x, y) ? fromClient(x, y) : null);
        },
    };
}

function tap(c) {
    if (state.mode === 'results') {
        state.focus = state.focus === c.id ? null : c.id;
    } else if (!state.positions[c.id]) {
        state.selected = state.selected === c.id ? null : c.id;
    }
    render();
}

function drop(c, pos) {
    if (state.mode === 'play') {
        if (pos) state.positions[c.id] = pos;
        else delete state.positions[c.id];   // dropped off the chart: back to the tray
        if (state.selected === c.id) state.selected = null;
        saveDraft();
        render();
        return;
    }
    if (!pos) { render(); return; }          // after submitting, answers only move
    state.positions[c.id] = pos;
    render();
    submit();
}

function bindGesture(t, c, draggable) {
    if (!draggable) t.classList.add('tap-only');
    t.addEventListener('pointerdown', (ev) => {
        if (ev.pointerType === 'touch' || ev.button > 0) return;
        ev.preventDefault();
        const g = gesture(t, c, draggable, ev.clientX, ev.clientY);
        const move = (e) => { if (g.dragging() || g.far(e.clientX, e.clientY)) g.move(e.clientX, e.clientY); };
        const up = (e) => {
            window.removeEventListener('pointermove', move);
            window.removeEventListener('pointerup', up);
            window.removeEventListener('pointercancel', up);
            g.end(e.clientX, e.clientY, e.type === 'pointercancel');
        };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
        window.addEventListener('pointercancel', up);
    });

    t.addEventListener('touchstart', (ev) => {
        if (ev.touches.length > 1) return;
        const p = ev.changedTouches[0];
        const id = p.identifier;
        const g = gesture(t, c, draggable, p.clientX, p.clientY);
        const find = (list) => [...list].find((x) => x.identifier === id);

        // A token that can't move lets the page scroll (touch-action:
        // manipulation); the others are touch-action: none, so the browser
        // never turns the gesture into a scroll or a back swipe.
        let scrolling = !draggable;
        const move = (e) => {
            const q = find(e.changedTouches);
            if (!q || scrolling) return;
            if (e.cancelable) e.preventDefault();
            if (g.dragging() || g.far(q.clientX, q.clientY)) g.move(q.clientX, q.clientY);
        };
        const end = (e) => {
            const q = find(e.changedTouches);
            if (!q) return;
            t.removeEventListener('touchmove', move);
            t.removeEventListener('touchend', end);
            t.removeEventListener('touchcancel', end);
            const jitter = Math.hypot(q.clientX - p.clientX, q.clientY - p.clientY) < DRAG_START;
            if (scrolling && (!jitter || e.type === 'touchcancel')) return;
            if (e.cancelable) e.preventDefault();   // no synthetic mouse events or click
            if (scrolling) { tap(c); return; }
            g.end(q.clientX, q.clientY, e.type === 'touchcancel');
        };
        t.addEventListener('touchmove', move, { passive: false });
        t.addEventListener('touchend', end);
        t.addEventListener('touchcancel', end);
    }, { passive: true });
}

field.addEventListener('click', (ev) => {
    if (ev.target.closest('.tok')) return;
    if (state.mode === 'results') {
        if (state.focus) { state.focus = null; render(); }
        return;
    }
    if (!state.selected) return;
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

$('medCb').addEventListener('change', (ev) => {
    state.medians = ev.target.checked;
    store.set('xy.medians', state.medians);
    render();
});

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
