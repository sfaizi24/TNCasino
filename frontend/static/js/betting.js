const isAuth = window.isAuthenticated;
const QUICK_STAKES = [10, 25, 50, 100];
const QUOTE_DELAY_MS = 250;

// The endpoint listing each kind of card. Each tab lists one kind, and its data-tab names it.
const SOURCES = {
    ml: '/api/matchups',
    sp: '/api/spreads',
    ou: '/api/team_performance',
    hi: '/api/highest_scorer',
    lo: '/api/lowest_scorer',
    fp: '/api/first_place',
    mp: '/api/make_playoffs',
    lp: '/api/last_place',
    ch: '/api/champion',
};

// Active bets are listed in one group per tab.
const ACTIVE_GROUPS = [
    { label: 'ML', types: ['moneyline'] },
    { label: 'SPREAD', types: ['spread'] },
    { label: 'O/U', types: ['team_total'] },
    { label: 'HIGH', types: ['highest_scorer'] },
    { label: 'LOW', types: ['lowest_scorer'] },
    { label: 'FUTURES', types: ['first_place', 'make_playoffs', 'last_place', 'champion'] },
    { label: 'PARLAY', types: ['parlay'] },
];

const state = {
    tab: 'ml',
    window: null,
    bets: [],
    balance: window.userBalance || 0,
    picks: {},
    stakes: {},
    opens: {},
    lineupCache: {},
    spreadLines: {},
    rows: { ml: [], sp: [], ou: [], hi: [], lo: [], fp: [], mp: [], lp: [], ch: [] },
    slip: emptySlip(),
};

let quoteTimer = null;

function emptySlip() {
    return { legs: [], quote: null, refusal: null, stake: '', collapsed: false };
}

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

// A side's handicap: "-5.5", "+5.5", "0.0".
function fmtLine(line) {
    return line > 0 ? `+${line.toFixed(1)}` : line.toFixed(1);
}

function fmtPct(p) {
    return `${(p * 100).toFixed(0)}%`;
}

// Digits and at most one dot: what Number() reads as a stake.
function cleanStake(value) {
    const [whole, ...rest] = value.replace(/[^0-9.]/g, '').split('.');
    return rest.length ? `${whole}.${rest.join('')}` : whole;
}

// A kickoff or publish time in the visitor's own zone: "Thu 8:15 PM".
function fmtWhen(iso) {
    return new Date(iso).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
}

function bettingOpen() {
    return state.window !== null && state.window.state === 'open';
}

function windowText() {
    const w = state.window;
    if (!w) return '';
    if (w.state === 'open') return `Odds updated ${fmtWhen(w.run_created_at)}. Betting closes ${fmtWhen(w.closes_at)}.`;
    if (w.state === 'paused') return `Betting paused since ${fmtWhen(w.closes_at)}, until the odds update.`;
    return `Betting is closed for week ${w.week}.`;
}

// The server refuses a place, cancel or cash-out outside the window; disabling the button just says why up front.
function closedAttrs() {
    return bettingOpen() ? '' : ` disabled title="${windowText()}"`;
}

function mainLineIndex(row) {
    return row.lines.findIndex(entry => entry.line === row.line);
}

// row.lines speaks in team1's line, so a team2 side's line is flipped to find its entry.
function lineIndexOf(row, selection, line) {
    const team1Line = selection === String(row.team1_id) ? line : -line;
    return row.lines.findIndex(entry => entry.line === team1Line);
}

// A card with a bet on it stays at the bet's line; any other shows the line picked, or the main line.
function lineIndex(row) {
    const bet = state.bets.find(b => b.market === row.market);
    const betIndex = bet ? lineIndexOf(row, bet.selection, bet.legs[0].line) : -1;
    if (betIndex !== -1) return betIndex;
    return state.spreadLines[row.market] ?? mainLineIndex(row);
}

function spreadSides(row, entry) {
    return [
        { selection: String(row.team1_id), label: row.team1_name, odds: entry.team1_odds, chance: entry.team1_prob, line: entry.line },
        { selection: String(row.team2_id), label: row.team2_name, odds: entry.team2_odds, chance: entry.team2_prob, line: -entry.line },
    ];
}

// The selections a card offers, each with its price and its chance as a fraction.
function sidesOf(kind, row) {
    if (kind === 'sp') return spreadSides(row, row.lines[lineIndex(row)]);
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

// Every scorer, first-place, last-place or champion card shares one market, so a bet marks the card that offers its selection.
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

function isMatchup(kind) {
    return kind === 'ml' || kind === 'sp';
}

function ownersOf(kind, row) {
    return isMatchup(kind) ? [row.team1_name, row.team2_name] : [row.owner];
}

function isFuture(kind) {
    return ['fp', 'mp', 'lp', 'ch'].includes(kind);
}

// The pick's short name in the slip: "Bob B +105", "Alice A -5.5", "Alice A Over 110.50", "Bob B highest scorer".
function legLabel(kind, row, side) {
    if (kind === 'ml') return `${side.label} ${fmtOdds(side.odds)}`;
    if (kind === 'sp') return `${side.label} ${fmtLine(side.line)}`;
    if (kind === 'ou') return `${row.owner} ${side.label} ${row.line.toFixed(2)}`;
    if (kind === 'hi') return `${row.owner} highest scorer`;
    return `${row.owner} lowest scorer`;
}

// A spread side carries its own line and a total's is the card's; a moneyline has none.
function lineOf(row, side) {
    return side.line ?? row.line ?? null;
}

function legFor(kind, row, side) {
    return {
        kind,
        market: row.market,
        selection: side.selection,
        line: lineOf(row, side),
        run_id: row.run_id,
        label: legLabel(kind, row, side),
    };
}

function inSlip(market, selection) {
    return state.slip.legs.some(leg => leg.market === market && leg.selection === selection);
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
                    ${b.cash_out_offer !== null ? `<button class="tnc-mc-placed-cash tnc-tab-num" data-action="cashout" data-bet-id="${b.id}"${closedAttrs()}>Cash out ${fmtMoney(b.cash_out_offer)}</button>` : ''}
                    ${b.removable ? `<button class="tnc-mc-placed-rm" data-action="cancel" data-bet-id="${b.id}"${closedAttrs()}>Cancel bet</button>` : ''}
                </div>
            `).join('')}
        </div>
    `;
}

// Futures run past the week, so only the weekly markets can join a parlay.
function renderParlayButton(key) {
    const { kind, row } = cardForKey(key);
    if (isFuture(kind)) return '';
    if (inSlip(row.market, state.picks[key])) {
        return '<button class="tnc-mc-parlay" data-action="parlay" disabled>In parlay</button>';
    }
    return '<button class="tnc-mc-parlay" data-action="parlay">Add to parlay</button>';
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
                ${renderParlayButton(key)}
                <button class="tnc-mc-place" data-action="place"${stakeNum > 0 ? '' : ' disabled'}${closedAttrs()}>Place Bet</button>
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
    const solo = !isMatchup(card.kind);
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

// The steps stop at the ends of the offered lines, and a card with a bet on it keeps the bet's line.
function renderLinePicker(card) {
    const { row } = card;
    const index = lineIndex(row);
    const frozen = card.placed.length > 0;
    const lowest = frozen || index === 0 ? ' disabled' : '';
    const highest = frozen || index === row.lines.length - 1 ? ' disabled' : '';
    const main = index === mainLineIndex(row)
        ? ''
        : `<button class="tnc-sp-main" data-action="spread-main"${frozen ? ' disabled' : ''}>Main line</button>`;
    return `
        <div class="tnc-sp-picker">
            <div class="tnc-sp-stepper">
                <button class="tnc-sp-step" data-action="spread-line" data-delta="-0.5" aria-label="Line down half a point"${lowest}>&minus;&frac12;</button>
                <span class="tnc-sp-line tnc-tab-num">${fmtLine(row.lines[index].line)}</span>
                <button class="tnc-sp-step" data-action="spread-line" data-delta="0.5" aria-label="Line up half a point"${highest}>+&frac12;</button>
            </div>
            ${main}
        </div>
    `;
}

function renderSpreadPicks(card) {
    const [team1, team2] = card.sides;
    const buttons = card.sides.map(side => renderPickButton(card, side, `
        <span class="tnc-mc-odd-num tnc-tab-num">${fmtOdds(side.odds)}</span>
        <span class="tnc-mc-odd-prob tnc-tab-num">${fmtPct(side.chance)}</span>
    `));
    return `
        <div class="tnc-mc-teams">
            <div class="tnc-mc-name">${team1.label}</div>
            <div class="tnc-mc-vs">VS</div>
            <div class="tnc-mc-name">${team2.label}</div>
        </div>
        ${renderLinePicker(card)}
        <div class="tnc-ou-buttons">${buttons.join('')}</div>
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
    if (card.kind === 'sp') return renderSpreadPicks(card);
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

// The card side a bet or slip leg backs. A parlay, or a bet placed before markets existed, has no market and matches none.
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

// A chip names the pick and leaves the market to its group, except in Futures, whose group holds four.
function chipLabel(bet) {
    if (bet.bet_type === 'parlay') return `${bet.legs.length}-leg parlay`;
    const match = sideForBet(bet);
    if (!match) return bet.description.replace(` ${bet.odds}`, '');
    const { kind, row, side } = match;
    if (kind === 'ml') return side.label;
    if (kind === 'sp') return `${side.label} ${fmtLine(bet.legs[0].line)}`;
    if (kind === 'ou') return `${row.owner} ${side.label[0]} ${bet.line.toFixed(2)}`;
    if (kind === 'fp') return `${row.owner} to finish first`;
    if (kind === 'mp') return `${row.owner} to make playoffs`;
    if (kind === 'lp') return `${row.owner} to finish last`;
    if (kind === 'ch') return `${row.owner} to win the championship`;
    return row.owner;
}

function renderActiveChip(bet) {
    const cashOut = bet.cash_out_offer !== null
        ? `<button class="tnc-active-chip-cash tnc-tab-num" data-action="cashout" data-bet-id="${bet.id}"${closedAttrs()}>Cash out ${fmtMoney(bet.cash_out_offer)}</button>`
        : '';
    const cancel = bet.removable
        ? `<button class="tnc-active-chip-rm" data-action="cancel" data-bet-id="${bet.id}"${closedAttrs()}>Cancel</button>`
        : '';
    return `
        <span class="tnc-active-chip">
            <span class="tnc-active-chip-name">${chipLabel(bet)}</span>
            <span class="tnc-active-chip-odds tnc-tab-num">${fmtOdds(bet.odds)}</span>
            <span class="tnc-active-chip-stake tnc-tab-num">${fmtMoney(bet.amount)}</span>
            ${cashOut}${cancel}
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

function isFlagged(leg) {
    return Boolean(state.slip.refusal?.legs?.includes(leg.market));
}

function renderSlipLeg(leg, index) {
    return `
        <div class="tnc-slip-leg${isFlagged(leg) ? ' is-flagged' : ''}">
            <span class="tnc-slip-leg-label">${leg.label}</span>
            <button class="tnc-slip-rm" data-action="slip-remove" data-index="${index}" aria-label="Remove ${leg.label}">&times;</button>
        </div>
    `;
}

function renderSlipStatus() {
    const { legs, quote, refusal } = state.slip;
    if (legs.length < 2) return '<p class="tnc-slip-note">Add another leg</p>';
    if (refusal) return `<p class="tnc-slip-refusal">${refusal.error}</p>`;
    if (!quote) return '<p class="tnc-slip-note">Pricing&hellip;</p>';
    return `
        <p class="tnc-slip-quote tnc-tab-num">
            <strong>${fmtOdds(quote.odds)}</strong> &middot; ${fmtPct(quote.probability)} chance
        </p>
    `;
}

function slipPayout() {
    const stake = Number(state.slip.stake) || 0;
    if (!state.slip.quote || stake <= 0) return 0;
    return stake * americanToDecimal(state.slip.quote.odds);
}

function renderSlipStake() {
    const payout = slipPayout();
    return `
        <div class="tnc-slip-stake">
            <div class="tnc-mc-quick">
                ${QUICK_STAKES.map(v => `<button data-action="slip-quick" data-amount="${v}">$${v}</button>`).join('')}
            </div>
            <div class="tnc-mc-stake-row">
                <div class="tnc-mc-stake-field">
                    <input class="tnc-mc-input" id="slipStake" data-action="slip-stake" placeholder="Stake" inputmode="decimal" value="${state.slip.stake}">
                    <span class="tnc-mc-payout tnc-tab-num" id="slipPayout"${payout > 0 ? '' : ' style="display:none;"'}>${payout > 0 ? `Pays out ${fmtMoney(payout)}` : ''}</span>
                </div>
                <button class="tnc-mc-place" id="slipPlace" data-action="slip-place"${payout > 0 ? '' : ' disabled'}${closedAttrs()}>Place parlay</button>
            </div>
        </div>
    `;
}

// The head toggles the slip between the whole slip and a single line; collapsed, the price moves up into it.
function renderSlipHead() {
    const { legs, quote, collapsed } = state.slip;
    const odds = quote && collapsed ? `<span class="tnc-slip-odds tnc-tab-num">${fmtOdds(quote.odds)}</span>` : '';
    return `
        <div class="tnc-slip-head">
            <button class="tnc-slip-toggle" data-action="slip-toggle" aria-expanded="${!collapsed}">
                <span class="tnc-slip-title">Parlay</span>
                <span class="tnc-slip-count">${legs.length} ${legs.length === 1 ? 'leg' : 'legs'}</span>
                ${odds}
                ${chevronSvg()}
            </button>
            <button class="tnc-slip-clear" data-action="slip-clear">Clear</button>
        </div>
    `;
}

function renderSlipBody() {
    const legs = state.slip.legs;
    return `
        <div class="tnc-slip-legs">${legs.map(renderSlipLeg).join('')}</div>
        ${renderSlipStatus()}
        ${legs.length > 1 ? renderSlipStake() : ''}
    `;
}

// The slip floats over the page, so the page grows by its height to let the last cards scroll clear of it.
function reserveSlipSpace() {
    const slip = document.querySelector('.tnc-slip');
    document.body.style.paddingBottom = slip ? `${slip.offsetHeight + 16}px` : '';
}

function renderSlip() {
    const container = document.getElementById('slip');
    const { legs, collapsed } = state.slip;
    if (!isAuth || !legs.length) {
        container.innerHTML = '';
    } else {
        container.innerHTML = `
            <div class="tnc-slip${collapsed ? ' is-collapsed' : ''}">
                ${renderSlipHead()}
                ${collapsed ? '' : renderSlipBody()}
            </div>
        `;
    }
    reserveSlipSpace();
}

function renderGrid() {
    const grid = document.getElementById('grid');
    const rows = state.rows[state.tab];
    if (!rows.length) {
        grid.innerHTML = '<p class="tnc-mc-loading">No bets available right now.</p>';
        return;
    }
    grid.innerHTML = rows.map((row, idx) => renderCard(state.tab, row, idx)).join('');
}

function renderWindow() {
    const el = document.getElementById('bettingWindow');
    if (!state.window) return;
    el.textContent = windowText();
    el.className = `tnc-window is-${state.window.state}`;
}

function render() {
    renderWindow();
    renderActiveBets();
    renderGrid();
    renderSlip();
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

async function loadWindow() {
    const r = await fetch('/api/betting_window');
    state.window = await r.json();
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

// A newer run is live: show its prices and its window, drop the picks made on the old ones and move the slip onto it.
async function reloadTab() {
    const slipKinds = state.slip.legs.map(leg => leg.kind);
    const kinds = new Set([state.tab, ...slipKinds]);
    await Promise.all([...[...kinds].map(loadRows), loadWindow(), loadBets()]);
    state.picks = {};
    state.stakes = {};
    state.spreadLines = {};
    refreshSlipLegs();
    render();
}

// A refusal may mean a kickoff passed while the page sat open: show the window the server saw.
async function reloadWindow() {
    await Promise.all([loadWindow(), loadBets()]);
    render();
}

// Every change to the legs voids the last quote and asks for a new one.
function setSlipLegs(legs) {
    state.slip.legs = legs;
    state.slip.quote = null;
    state.slip.refusal = null;
    scheduleQuote();
}

// A slip leg as the rows now loaded offer it, or null once they no longer do. A spread leg keeps its own line.
function currentLeg(leg) {
    const match = sideForBet(leg);
    if (!match) return null;
    const { kind, row } = match;
    const side = kind === 'sp' ? spreadSideAt(row, leg.selection, leg.line) : match.side;
    if (!side || side.odds == null) return null;
    return legFor(kind, row, side);
}

function spreadSideAt(row, selection, line) {
    const index = lineIndexOf(row, selection, line);
    if (index === -1) return null;
    return spreadSides(row, row.lines[index]).find(side => side.selection === selection);
}

// Legs whose run, line or price moved are re-quoted; legs whose market is gone are dropped.
function refreshSlipLegs() {
    const legs = state.slip.legs.map(currentLeg).filter(leg => leg !== null);
    if (JSON.stringify(legs) !== JSON.stringify(state.slip.legs)) setSlipLegs(legs);
}

function parlayPayload(legs) {
    return {
        legs: legs.map(leg => ({ market: leg.market, selection: leg.selection, line: leg.line })),
        run_id: legs[0].run_id,
    };
}

function scheduleQuote() {
    clearTimeout(quoteTimer);
    if (state.slip.legs.length < 2) return;
    quoteTimer = setTimeout(quoteSlip, QUOTE_DELAY_MS);
}

// Moved odds reload the tab, which moves the legs onto the new run and so quotes them again.
async function refuseSlip(result) {
    state.slip.quote = null;
    state.slip.refusal = result;
    if (result.rule === 'odds_changed') await reloadTab();
    else if (!result.rule) await reloadWindow();
}

async function quoteSlip() {
    const legs = state.slip.legs;
    try {
        const r = await fetch('/api/parlay_quote', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(parlayPayload(legs)),
        });
        const result = await r.json();
        // The slip changed while this quote was out; the change asked for its own.
        if (state.slip.legs !== legs) return;
        if (result.success) state.slip.quote = result;
        else await refuseSlip(result);
    } catch (e) {
        console.error('Error quoting parlay', e);
    }
    renderSlip();
}

function payloadForKey(key, amount) {
    const { kind, row } = cardForKey(key);
    const side = sidesOf(kind, row).find(s => s.selection === state.picks[key]);
    return { market: row.market, selection: side.selection, line: lineOf(row, side), run_id: row.run_id, amount };
}

function handlePick(card, btn) {
    const key = card.dataset.key;
    const selection = btn.dataset.selection;
    state.picks[key] = state.picks[key] === selection ? null : selection;
    renderGrid();
}

// A new line is a new price, so the pick and stake made at the old one go.
function setSpreadLine(card, index) {
    const key = card.dataset.key;
    const { row } = cardForKey(key);
    state.spreadLines[row.market] = index;
    delete state.picks[key];
    delete state.stakes[key];
    renderGrid();
}

function handleSpreadStep(card, delta) {
    const { row } = cardForKey(card.dataset.key);
    const line = row.lines[lineIndex(row)].line + delta;
    setSpreadLine(card, row.lines.findIndex(entry => entry.line === line));
}

function handleSpreadMain(card) {
    const { row } = cardForKey(card.dataset.key);
    setSpreadLine(card, mainLineIndex(row));
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
    if (placeBtn) placeBtn.disabled = !(stake > 0) || !bettingOpen();
}

function handleStakeInput(card, input) {
    const key = card.dataset.key;
    const cleaned = cleanStake(input.value);
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
            else await reloadWindow();
        }
    } catch (e) {
        console.error('Error placing bet', e);
        setBalance(oldBalance);
        toast('Failed to place bet', 'error');
    }
}

function handleAddToParlay(card) {
    const key = card.dataset.key;
    const { kind, row } = cardForKey(key);
    const side = sidesOf(kind, row).find(s => s.selection === state.picks[key]);
    setSlipLegs([...state.slip.legs, legFor(kind, row, side)]);
    delete state.picks[key];
    delete state.stakes[key];
    renderGrid();
    renderSlip();
}

function handleSlipRemove(index) {
    setSlipLegs(state.slip.legs.filter((leg, i) => i !== index));
    renderGrid();
    renderSlip();
}

function handleSlipClear() {
    clearTimeout(quoteTimer);
    state.slip = emptySlip();
    renderGrid();
    renderSlip();
}

function updateSlipInPlace() {
    const payout = slipPayout();
    const payoutEl = document.getElementById('slipPayout');
    payoutEl.textContent = payout > 0 ? `Pays out ${fmtMoney(payout)}` : '';
    payoutEl.style.display = payout > 0 ? '' : 'none';
    document.getElementById('slipPlace').disabled = !(payout > 0) || !bettingOpen();
}

function handleSlipStakeInput(input) {
    const cleaned = cleanStake(input.value);
    if (cleaned !== input.value) input.value = cleaned;
    state.slip.stake = cleaned;
    updateSlipInPlace();
}

function handleSlipQuick(amount) {
    state.slip.stake = String(amount);
    document.getElementById('slipStake').value = String(amount);
    updateSlipInPlace();
}

async function handleSlipPlace() {
    const stake = Number(state.slip.stake);
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
            body: JSON.stringify({ ...parlayPayload(state.slip.legs), amount: stake }),
        });
        const result = await r.json();
        if (result.success) {
            setBalance(result.new_balance);
            state.slip = emptySlip();
            await loadBets();
            render();
            toast('Parlay placed!');
        } else {
            setBalance(oldBalance);
            toast(result.error || 'Failed to place parlay', 'error');
            if (result.rule) await refuseSlip(result);
            else await reloadWindow();
        }
    } catch (e) {
        console.error('Error placing parlay', e);
        setBalance(oldBalance);
        toast('Failed to place parlay', 'error');
    }
}

function handleSlipToggle() {
    state.slip.collapsed = !state.slip.collapsed;
    renderSlip();
}

function handleSlipClick(action, target) {
    if (action === 'slip-toggle') handleSlipToggle();
    else if (action === 'slip-remove') handleSlipRemove(parseInt(target.dataset.index, 10));
    else if (action === 'slip-clear') handleSlipClear();
    else if (action === 'slip-quick') handleSlipQuick(parseInt(target.dataset.amount, 10));
    else if (action === 'slip-place') handleSlipPlace();
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
            await reloadWindow();
        }
    } catch (e) {
        console.error('Error removing bet', e);
        setBalance(oldBalance);
        toast('Failed to remove bet', 'error');
    }
}

async function handleCashOut(betId) {
    const bet = state.bets.find(b => b.id === betId);
    if (!bet) return;
    const offer = bet.cash_out_offer;
    if (!confirm(`Cash out "${bet.description}" for ${fmtMoney(offer)}? The bet closes for good.`)) return;

    try {
        const r = await fetch(`/api/cash_out/${betId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ offer }),
        });
        const result = await r.json();
        if (result.success) {
            setBalance(result.new_balance);
            await loadBets();
            render();
            toast(`Cashed out for ${fmtMoney(result.cash_out_amount)}`);
        } else {
            toast(result.error || 'Failed to cash out', 'error');
            await reloadWindow();
        }
    } catch (e) {
        console.error('Error cashing out', e);
        toast('Failed to cash out', 'error');
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

// Each view keeps its own tab bar, so switching back returns to the tab left on.
function handleTab(bar, tab) {
    state.tab = tab;
    bar.querySelectorAll('.tnc-tab').forEach(t => {
        t.classList.toggle('is-on', t.dataset.tab === tab);
    });
    renderGrid();
}

function setViewMenuOpen(open) {
    document.getElementById('viewMenu').hidden = !open;
    document.getElementById('viewToggle').setAttribute('aria-expanded', String(open));
}

function handleView(option) {
    const view = option.dataset.view;
    document.querySelectorAll('.tnc-view-option').forEach(o => o.classList.toggle('is-on', o === option));
    document.getElementById('viewTitle').textContent = option.textContent;
    document.querySelectorAll('.tnc-tabs').forEach(bar => {
        bar.hidden = bar.dataset.view !== view;
    });
    setViewMenuOpen(false);
    state.tab = document.querySelector(`.tnc-tabs[data-view="${view}"] .tnc-tab.is-on`).dataset.tab;
    renderGrid();
}

function bindViewPicker() {
    const menu = document.getElementById('viewMenu');
    document.getElementById('viewToggle').addEventListener('click', () => setViewMenuOpen(menu.hidden));
    menu.addEventListener('click', e => {
        const option = e.target.closest('.tnc-view-option');
        if (option) handleView(option);
    });
    document.addEventListener('click', e => {
        if (!e.target.closest('.tnc-view-picker')) setViewMenuOpen(false);
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') setViewMenuOpen(false);
    });
}

function bindEvents() {
    bindViewPicker();
    document.querySelectorAll('.tnc-tabs').forEach(bar => {
        bar.addEventListener('click', e => {
            const tab = e.target.closest('.tnc-tab');
            if (tab) handleTab(bar, tab.dataset.tab);
        });
    });

    document.body.addEventListener('click', e => {
        const target = e.target.closest('[data-action]');
        if (!target || target.disabled) return;
        const action = target.dataset.action;
        if (action === 'cancel') {
            handleCancel(parseInt(target.dataset.betId, 10));
            return;
        }
        if (action === 'cashout') {
            handleCashOut(parseInt(target.dataset.betId, 10));
            return;
        }
        if (target.closest('#slip')) {
            handleSlipClick(action, target);
            return;
        }
        const card = target.closest('.tnc-mc');
        if (!card) return;
        if (action === 'pick') handlePick(card, target);
        else if (action === 'quick') handleQuick(card, parseInt(target.dataset.amount, 10));
        else if (action === 'place') handlePlace(card);
        else if (action === 'parlay') handleAddToParlay(card);
        else if (action === 'show') handleShow(card);
        else if (action === 'spread-line') handleSpreadStep(card, Number(target.dataset.delta));
        else if (action === 'spread-main') handleSpreadMain(card);
    });

    document.body.addEventListener('input', e => {
        if (e.target.dataset.action === 'slip-stake') {
            handleSlipStakeInput(e.target);
            return;
        }
        if (e.target.dataset.action !== 'stake') return;
        const card = e.target.closest('.tnc-mc');
        if (card) handleStakeInput(card, e.target);
    });
}

async function init() {
    bindEvents();
    await Promise.all([...Object.keys(SOURCES).map(loadRows), loadWindow()]);
    if (isAuth) await loadBets();
    render();
}

init();
