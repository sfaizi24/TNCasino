const isAuth = window.isAuthenticated;
const QUICK_STAKES = [10, 25, 50, 100];

// The endpoint listing each kind of card.
const SOURCES = {
    ml: '/api/matchups',
    ou: '/api/team_performance',
    hi: '/api/highest_scorer',
    lo: '/api/lowest_scorer',
    fp: '/api/first_place',
    mp: '/api/make_playoffs',
};

// The kinds of card each tab lists. Futures lists two, each under its own heading.
const TABS = {
    ml: [{ kind: 'ml' }],
    ou: [{ kind: 'ou' }],
    hi: [{ kind: 'hi' }],
    lo: [{ kind: 'lo' }],
    fu: [
        { kind: 'fp', title: 'First place' },
        { kind: 'mp', title: 'Make playoffs' },
    ],
};

// Active bets are listed in one group per tab.
const ACTIVE_GROUPS = [
    { label: 'ML', types: ['moneyline'] },
    { label: 'O/U', types: ['team_total'] },
    { label: 'HIGH', types: ['highest_scorer'] },
    { label: 'LOW', types: ['lowest_scorer'] },
    { label: 'FUTURES', types: ['first_place', 'make_playoffs'] },
];

const state = {
    tab: 'ml',
    bets: [],
    balance: window.userBalance || 0,
    picks: {},
    stakes: {},
    opens: {},
    lineupCache: {},
    rows: { ml: [], ou: [], hi: [], lo: [], fp: [], mp: [] },
};

function americanToDecimal(odds) {
    const n = typeof odds === 'string' ? parseInt(odds, 10) : odds;
    return n > 0 ? 1 + n / 100 : 1 + 100 / Math.abs(n);
}

function fmtMoney(n) {
    return `$${Number(n).toFixed(2)}`;
}

function fmtOdds(odds) {
    if (odds === 'EVEN') return 'EVEN';
    const n = typeof odds === 'string' ? parseInt(odds, 10) : odds;
    return n > 0 ? `+${n}` : `${n}`;
}

function fmtPct(p) {
    return `${(p * 100).toFixed(0)}%`;
}

// The selections a card offers, each with its price and its chance as a fraction.
function sidesOf(kind, row) {
    if (kind === 'ml') {
        return [
            { selection: String(row.team1_id), label: row.team1_name, odds: row.team1_ml, chance: row.team1_win_prob },
            { selection: String(row.team2_id), label: row.team2_name, odds: row.team2_ml, chance: row.team2_win_prob },
        ];
    }
    if (kind === 'ou') {
        return [
            { selection: 'over', label: 'Over', odds: row.over_odds, chance: row.over_prob },
            { selection: 'under', label: 'Under', odds: row.under_odds, chance: row.under_prob },
        ];
    }
    const selection = kind === 'mp' ? 'yes' : String(row.team_id);
    return [{ selection, odds: row.odds, chance: row.win_prob / 100 }];
}

// Every scorer or first-place card shares one market, so a bet marks the card that offers its selection.
function buildCard(kind, row, idx) {
    const key = `${kind}-${idx}`;
    const sides = sidesOf(kind, row);
    const selections = sides.map(side => side.selection);
    return {
        kind,
        row,
        key,
        sides,
        pick: state.picks[key],
        open: state.opens[key],
        placed: state.bets.filter(bet => bet.market === row.market && selections.includes(bet.selection)),
    };
}

function cardForKey(key) {
    const [kind, idx] = key.split('-');
    return { kind, row: state.rows[kind][Number(idx)] };
}

function pickedOdds(key) {
    const { kind, row } = cardForKey(key);
    return sidesOf(kind, row).find(side => side.selection === state.picks[key]).odds;
}

function ownersOf(kind, row) {
    return kind === 'ml' ? [row.team1_name, row.team2_name] : [row.owner];
}

function isFuture(kind) {
    return kind === 'fp' || kind === 'mp';
}

function chevronSvg() {
    return '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"/></svg>';
}

function renderPlacedStrip(bets) {
    if (!bets.length) return '';
    return `
        <div class="tnc-mc-placed-strip">
            ${bets.map(b => `
                <div class="tnc-mc-placed-row">
                    <span class="tnc-mc-placed-tag">Bet Placed</span>
                    <span class="tnc-mc-placed-info tnc-tab-num"><strong>${fmtMoney(b.amount)}</strong></span>
                    ${b.removable ? `<button class="tnc-mc-placed-rm" data-action="cancel" data-bet-id="${b.id}">Cancel bet</button>` : ''}
                </div>
            `).join('')}
        </div>
    `;
}

function renderStakeSection(key, odds) {
    if (!isAuth) {
        return `
            <div class="tnc-mc-stake">
                <div class="tnc-mc-stake-row">
                    <a class="tnc-mc-place" href="/login" style="flex:1;text-decoration:none;">Login to bet</a>
                </div>
            </div>
        `;
    }
    const stake = state.stakes[key] || '';
    const stakeNum = Number(stake) || 0;
    const payout = stakeNum > 0 ? stakeNum * americanToDecimal(odds) : 0;
    return `
        <div class="tnc-mc-stake">
            <div class="tnc-mc-quick">
                ${QUICK_STAKES.map(v => `<button data-action="quick" data-amount="${v}">$${v}</button>`).join('')}
            </div>
            <div class="tnc-mc-stake-row">
                <div class="tnc-mc-stake-field">
                    <input class="tnc-mc-input" data-action="stake" placeholder="Stake" inputmode="decimal" value="${stake}">
                    <span class="tnc-mc-payout tnc-tab-num"${payout > 0 ? '' : ' style="display:none;"'}>${payout > 0 ? `Pays out ${fmtMoney(payout)}` : ''}</span>
                </div>
                <button class="tnc-mc-place" data-action="place"${stakeNum > 0 ? '' : ' disabled'}>Place Bet</button>
            </div>
        </div>
    `;
}

function splitPlayerName(name) {
    const i = (name || '').indexOf(' ');
    if (i === -1) return name;
    return `${name.slice(0, i)}<br>${name.slice(i + 1)}`;
}

function renderLineupCol(players) {
    if (!players || !players.length) {
        return '<div class="tnc-lp-col"><div class="tnc-lp-empty">No lineup</div></div>';
    }
    return `
        <div class="tnc-lp-col">
            ${players.map(p => `
                <div class="tnc-lp-row">
                    <span class="tnc-lp-name">${splitPlayerName(p.player_name)}</span>
                    <span class="tnc-lp-pos">${p.position}</span>
                    <span class="tnc-lp-pts tnc-tab-num">${(p.projected_points || 0).toFixed(1)}</span>
                </div>
            `).join('')}
        </div>
    `;
}

function renderLineups(owners, solo) {
    const cols = owners.map(owner => {
        const cached = state.lineupCache[owner];
        return cached ? renderLineupCol(cached) : '<div class="tnc-lp-col"><div class="tnc-lp-empty">Loading…</div></div>';
    });
    const inner = solo ? cols[0] : `${cols[0]}<div class="tnc-mc-divider"></div>${cols[1]}`;
    return `<div class="tnc-mc-lineups${solo ? ' tnc-mc-lineups-solo' : ''}">${inner}</div>`;
}

function renderShowButton(open, solo) {
    const label = open ? (solo ? 'Hide lineup' : 'Hide lineups') : (solo ? 'Show lineup' : 'Show lineups');
    return `<button class="tnc-mc-show" data-action="show">${chevronSvg()}${label}</button>`;
}

// Futures run to the end of the season, so their cards show no week's lineup.
function renderLineupToggle(card) {
    if (isFuture(card.kind)) return '';
    const solo = card.kind !== 'ml';
    const lineups = card.open ? renderLineups(ownersOf(card.kind, card.row), solo) : '';
    return renderShowButton(card.open, solo) + lineups;
}

function renderPickButton(card, side, content, extraClass = '') {
    if (side.odds == null) return '<span class="tnc-mc-noprice">No price</span>';

    const classes = ['tnc-mc-odd', 'tnc-mc-odd-inline'];
    if (extraClass) classes.push(extraClass);
    if (card.pick === side.selection) classes.push('is-on');
    if (card.placed.some(bet => bet.selection === side.selection)) classes.push('is-placed');

    const totalAttrs = card.kind === 'ou' ? ` data-line="${card.row.line.toFixed(2)}" data-run-id="${card.row.run_id}"` : '';
    const disabled = card.placed.length ? ' disabled' : '';
    return `
        <button class="${classes.join(' ')}" data-action="pick" data-selection="${side.selection}"${totalAttrs}${disabled}>
            ${content}
        </button>
    `;
}

function renderMatchupPicks(card) {
    const renderSide = side => `
        <div class="tnc-mc-side">
            <div class="tnc-mc-name">${side.label}</div>
            ${renderPickButton(card, side, `
                <span class="tnc-mc-odd-num tnc-tab-num">${fmtOdds(side.odds)}</span>
                <span class="tnc-mc-odd-prob tnc-tab-num">${fmtPct(side.chance)}</span>
            `)}
        </div>
    `;
    const [team1, team2] = card.sides;
    return `
        <div class="tnc-mc-teams">
            ${renderSide(team1)}
            <div class="tnc-mc-vs">VS</div>
            ${renderSide(team2)}
        </div>
    `;
}

function renderTotalPicks(card) {
    const line = card.row.line.toFixed(2);
    const buttons = card.sides.map(side => renderPickButton(card, side, `
        <span class="tnc-mc-odd-label">${side.label}</span>
        <span class="tnc-mc-odd-num tnc-tab-num">${line}</span>
        <span class="tnc-mc-odd-prob tnc-tab-num">${fmtOdds(side.odds)}</span>
    `));
    return `
        <div class="tnc-mc-name tnc-ou-name-solo">${card.row.owner}</div>
        <div class="tnc-ou-buttons">${buttons.join('')}</div>
    `;
}

function renderScorerPicks(card) {
    const [side] = card.sides;
    const proj = card.row.proj_pts != null ? `${card.row.proj_pts.toFixed(1)} pts` : '—';
    return `
        <div class="tnc-mc-name tnc-ou-name-solo">${card.row.owner}</div>
        ${renderPickButton(card, side, `
            <span class="tnc-mc-odd-num tnc-tab-num">${fmtOdds(side.odds)}</span>
            <span class="tnc-mc-odd-prob tnc-tab-num">${fmtPct(side.chance)}</span>
            <span class="tnc-mc-odd-prob tnc-tab-num">${proj}</span>
        `, 'tnc-scorer-pick')}
    `;
}

function renderFuturePicks(card) {
    const [side] = card.sides;
    const yes = card.kind === 'mp' ? '<span class="tnc-mc-odd-label">Yes</span>' : '';
    return `
        <div class="tnc-mc-name tnc-ou-name-solo">${card.row.owner}</div>
        ${renderPickButton(card, side, `
            ${yes}
            <span class="tnc-mc-odd-num tnc-tab-num">${fmtOdds(side.odds)}</span>
            <span class="tnc-mc-odd-prob tnc-tab-num">${fmtPct(side.chance)}</span>
        `, 'tnc-scorer-pick')}
    `;
}

function renderPicks(card) {
    if (card.kind === 'ml') return renderMatchupPicks(card);
    if (card.kind === 'ou') return renderTotalPicks(card);
    if (card.kind === 'hi' || card.kind === 'lo') return renderScorerPicks(card);
    return renderFuturePicks(card);
}

function renderCard(kind, row, idx) {
    const card = buildCard(kind, row, idx);
    const hasBet = card.placed.length > 0;
    const classes = ['tnc-mc'];
    if (isFuture(kind)) classes.push('tnc-fu-card');
    if (hasBet) classes.push('has-bet');
    if (card.open) classes.push('is-open');
    return `
        <div class="${classes.join(' ')}" data-key="${card.key}" data-market="${row.market}">
            ${renderPlacedStrip(card.placed)}
            ${renderPicks(card)}
            ${card.pick && !hasBet ? renderStakeSection(card.key, pickedOdds(card.key)) : ''}
            ${renderLineupToggle(card)}
        </div>
    `;
}

// The card side a bet backs. A bet placed before markets existed has no market and matches none.
function sideForBet(bet) {
    for (const kind of Object.keys(state.rows)) {
        for (const row of state.rows[kind]) {
            if (row.market !== bet.market) continue;
            const side = sidesOf(kind, row).find(s => s.selection === bet.selection);
            if (side) return { kind, row, side };
        }
    }
    return null;
}

// A chip names the pick and leaves the market to its group, except in Futures, whose group holds two.
function chipLabel(bet) {
    const match = sideForBet(bet);
    if (!match) return bet.description.replace(` ${bet.odds}`, '');
    const { kind, row, side } = match;
    if (kind === 'ml') return side.label;
    if (kind === 'ou') return `${row.owner} ${side.label[0]} ${bet.line.toFixed(2)}`;
    if (kind === 'fp') return `${row.owner} to finish first`;
    if (kind === 'mp') return `${row.owner} to make playoffs`;
    return row.owner;
}

function renderActiveChip(bet) {
    const cancel = bet.removable
        ? `<button class="tnc-active-chip-rm" data-action="cancel" data-bet-id="${bet.id}">Cancel</button>`
        : '';
    return `
        <span class="tnc-active-chip">
            <span class="tnc-active-chip-name">${chipLabel(bet)}</span>
            <span class="tnc-active-chip-odds tnc-tab-num">${fmtOdds(bet.odds)}</span>
            <span class="tnc-active-chip-stake tnc-tab-num">${fmtMoney(bet.amount)}</span>
            ${cancel}
        </span>
    `;
}

function renderActiveBets() {
    const container = document.getElementById('activeBets');
    const groups = ACTIVE_GROUPS
        .map(group => ({ label: group.label, bets: state.bets.filter(bet => group.types.includes(bet.bet_type)) }))
        .filter(group => group.bets.length);
    if (!groups.length) {
        container.innerHTML = '';
        return;
    }
    container.innerHTML = `
        <div class="tnc-active">
            ${groups.map(group => `
                <div class="tnc-active-group">
                    <span class="tnc-active-label">${group.label}</span>
                    <div class="tnc-active-chips">
                        ${group.bets.map(renderActiveChip).join('')}
                    </div>
                </div>
            `).join('')}
        </div>
    `;
}

function renderList({ kind, title }) {
    const heading = title ? `<h2 class="tnc-fu-head">${title}</h2>` : '';
    return heading + state.rows[kind].map((row, idx) => renderCard(kind, row, idx)).join('');
}

function renderGrid() {
    const grid = document.getElementById('grid');
    const lists = TABS[state.tab].filter(list => state.rows[list.kind].length);
    if (!lists.length) {
        grid.innerHTML = '<p class="tnc-mc-loading">No bets available right now.</p>';
        return;
    }
    grid.innerHTML = lists.map(renderList).join('');
}

function render() {
    renderActiveBets();
    renderGrid();
}

function setBalance(value) {
    state.balance = value;
    const el = document.getElementById('userBalance');
    if (el) el.textContent = fmtMoney(value);
}

function toast(message, type = 'success') {
    const stack = document.getElementById('toastStack');
    if (!stack) return;
    const el = document.createElement('div');
    el.className = `tnc-toast is-${type}`;
    el.innerHTML = `<span class="ic">${type === 'success' ? '✓' : '⚠'}</span><span>${message}</span>`;
    stack.appendChild(el);
    setTimeout(() => {
        el.classList.add('is-removing');
        setTimeout(() => el.remove(), 240);
    }, 2400);
}

async function loadRows(kind) {
    const r = await fetch(SOURCES[kind]);
    state.rows[kind] = await r.json();
}

async function loadBets() {
    if (!isAuth) return;
    try {
        const r = await fetch('/api/my_bets');
        if (!r.ok) return;
        state.bets = await r.json();
    } catch (e) {
        console.error('Error loading bets', e);
    }
}

async function loadLineup(owner) {
    if (state.lineupCache[owner]) return;
    const r = await fetch(`/api/lineup/${encodeURIComponent(owner)}`);
    state.lineupCache[owner] = await r.json();
}

// A newer run is live: show its prices, and drop the picks made on the old ones.
async function reloadTab() {
    const kinds = TABS[state.tab].map(list => list.kind);
    await Promise.all([...kinds.map(loadRows), loadBets()]);
    state.picks = {};
    state.stakes = {};
    render();
}

function payloadForKey(key, amount) {
    const { row } = cardForKey(key);
    return { market: row.market, selection: state.picks[key], line: row.line ?? null, run_id: row.run_id, amount };
}

function handlePick(card, btn) {
    const key = card.dataset.key;
    const selection = btn.dataset.selection;
    state.picks[key] = state.picks[key] === selection ? null : selection;
    renderGrid();
}

function updatePayoutInPlace(card) {
    const key = card.dataset.key;
    const stake = Number(state.stakes[key]) || 0;
    const odds = pickedOdds(key);
    const payoutEl = card.querySelector('.tnc-mc-payout');
    const placeBtn = card.querySelector('.tnc-mc-place');

    if (payoutEl) {
        if (stake > 0) {
            payoutEl.textContent = `Pays out ${fmtMoney(stake * americanToDecimal(odds))}`;
            payoutEl.style.display = '';
        } else {
            payoutEl.textContent = '';
            payoutEl.style.display = 'none';
        }
    }
    if (placeBtn) placeBtn.disabled = !(stake > 0);
}

function handleStakeInput(card, input) {
    const key = card.dataset.key;
    const cleaned = input.value.replace(/[^0-9.]/g, '');
    if (cleaned !== input.value) input.value = cleaned;
    state.stakes[key] = cleaned;
    updatePayoutInPlace(card);
}

function handleQuick(card, amount) {
    const key = card.dataset.key;
    state.stakes[key] = String(amount);
    const input = card.querySelector('.tnc-mc-input');
    if (input) input.value = String(amount);
    updatePayoutInPlace(card);
}

async function handlePlace(card) {
    const key = card.dataset.key;
    const stake = Number(state.stakes[key]);
    if (!stake || stake <= 0) return;
    if (stake > state.balance) {
        toast('Insufficient balance', 'error');
        return;
    }

    const oldBalance = state.balance;
    setBalance(state.balance - stake);

    try {
        const r = await fetch('/api/place_bet', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payloadForKey(key, stake)),
        });
        const result = await r.json();
        if (result.success) {
            setBalance(result.new_balance);
            delete state.picks[key];
            delete state.stakes[key];
            await loadBets();
            render();
            toast('Bet placed!');
        } else {
            setBalance(oldBalance);
            toast(result.error || 'Failed to place bet', 'error');
            if (result.error === 'Odds have changed') await reloadTab();
        }
    } catch (e) {
        console.error('Error placing bet', e);
        setBalance(oldBalance);
        toast('Failed to place bet', 'error');
    }
}

async function handleCancel(betId) {
    const bet = state.bets.find(b => b.id === betId);
    if (!bet) return;

    const oldBalance = state.balance;
    setBalance(state.balance + bet.amount);

    try {
        const r = await fetch(`/api/remove_bet/${betId}`, { method: 'DELETE' });
        const result = await r.json();
        if (result.success) {
            setBalance(result.new_balance);
            await loadBets();
            render();
            toast('Bet removed');
        } else {
            setBalance(oldBalance);
            toast(result.error || 'Failed to remove bet', 'error');
        }
    } catch (e) {
        console.error('Error removing bet', e);
        setBalance(oldBalance);
        toast('Failed to remove bet', 'error');
    }
}

async function handleShow(card) {
    const key = card.dataset.key;
    state.opens[key] = !state.opens[key];

    if (state.opens[key]) {
        const { kind, row } = cardForKey(key);
        const missing = ownersOf(kind, row).filter(o => !state.lineupCache[o]);
        if (missing.length) {
            renderGrid();
            try {
                await Promise.all(missing.map(loadLineup));
            } catch (e) {
                console.error('Error loading lineup', e);
            }
        }
    }
    renderGrid();
}

function handleTab(tab) {
    state.tab = tab;
    document.querySelectorAll('.tnc-tab').forEach(t => {
        t.classList.toggle('is-on', t.dataset.tab === tab);
    });
    renderGrid();
}

function bindEvents() {
    document.getElementById('tabs').addEventListener('click', e => {
        const tab = e.target.closest('.tnc-tab');
        if (tab) handleTab(tab.dataset.tab);
    });

    document.body.addEventListener('click', e => {
        const target = e.target.closest('[data-action]');
        if (!target) return;
        const action = target.dataset.action;
        if (action === 'cancel') {
            handleCancel(parseInt(target.dataset.betId, 10));
            return;
        }
        const card = target.closest('.tnc-mc');
        if (!card) return;
        if (target.disabled) return;
        if (action === 'pick') handlePick(card, target);
        else if (action === 'quick') handleQuick(card, parseInt(target.dataset.amount, 10));
        else if (action === 'place') handlePlace(card);
        else if (action === 'show') handleShow(card);
    });

    document.body.addEventListener('input', e => {
        if (e.target.dataset.action !== 'stake') return;
        const card = e.target.closest('.tnc-mc');
        if (card) handleStakeInput(card, e.target);
    });
}

async function init() {
    bindEvents();
    await Promise.all(Object.keys(SOURCES).map(loadRows));
    if (isAuth) await loadBets();
    render();
}

init();
