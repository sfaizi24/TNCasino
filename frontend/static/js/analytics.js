const currentWeek = parseInt(document.querySelector('[data-week]').getAttribute('data-week')) || 10;

const POSITION_GROUPS = ['QB', 'RB', 'WR', 'TE', 'FLEX', 'K', 'DEF'];
const POS_COLORS = {
    QB:   '#1493FF',
    RB:   '#22C55E',
    WR:   '#FCD34D',
    TE:   '#C084FC',
    FLEX: '#FB923C',
    K:    '#94A3B8',
    DEF:  '#F87171',
};
const PLAYOFF_CUTOFF = 8;
const fmt1 = (v) => (v == null ? '—' : v.toFixed(1));

const playoffCutoffPlugin = {
    id: 'playoffCutoff',
    afterDatasetsDraw(chart, _args, opts) {
        const { ctx, chartArea, scales: { y } } = chart;
        if (!y) return;
        const top = y.getPixelForValue(opts.cutoff - 0.5);
        ctx.save();
        ctx.setLineDash([6, 4]);
        ctx.strokeStyle = 'rgba(255, 107, 107, 0.8)';
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(chartArea.left, top);
        ctx.lineTo(chartArea.right, top);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.restore();
    },
};

const inBarLabelsPlugin = {
    id: 'inBarLabels',
    afterDatasetsDraw(chart, _args, opts) {
        const { ctx, scales: { x, y } } = chart;
        const teams = opts.teams || [];
        ctx.save();
        ctx.font = '700 13px system-ui, -apple-system, Segoe UI, sans-serif';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillStyle = '#FFFFFF';
        teams.forEach((t, i) => {
            const py = y.getPixelForValue(i);
            const px = x.getPixelForValue(0) + 8;
            ctx.fillText(`${t.label} (${t.record})`, px, py);
        });
        ctx.restore();
    },
};

const winLabelsPlugin = {
    id: 'winLabels',
    afterDatasetsDraw(chart, _args, opts) {
        const { ctx, chartArea, scales: { x, y } } = chart;
        const teams = opts.teams || [];
        ctx.save();
        ctx.font = '700 13px system-ui, -apple-system, Segoe UI, sans-serif';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        teams.forEach((t, i) => {
            if (t.win_prob == null) return;
            const px = x.getPixelForValue(t.proj_mean ?? 0);
            const py = y.getPixelForValue(i);
            ctx.fillStyle = i < PLAYOFF_CUTOFF ? '#1493FF' : '#8B949E';
            ctx.fillText(`${t.win_prob.toFixed(1)}%`, Math.min(px + 8, chartArea.right - 4), py);
        });
        ctx.restore();
    },
};

// Draws an axis title centered on the canvas (card) rather than on the
// chartArea, so right-padding for the win-prob labels doesn't pull it left.
const cardCenteredAxisTitlePlugin = {
    id: 'cardCenteredAxisTitle',
    afterDraw(chart, _args, opts) {
        if (!opts.text) return;
        const { ctx, chartArea, width } = chart;
        ctx.save();
        ctx.font = '12px system-ui, -apple-system, Segoe UI, sans-serif';
        ctx.fillStyle = '#8B949E';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(opts.text, width / 2, chartArea.bottom + 16);
        ctx.restore();
    },
};

Chart.register(playoffCutoffPlugin, inBarLabelsPlugin, winLabelsPlugin, cardCenteredAxisTitlePlugin);

async function fetchWithTimeout(url, options = {}, timeout = 10000) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    try {
        const response = await fetch(url, { ...options, signal: controller.signal });
        clearTimeout(timeoutId);
        return response;
    } catch (error) {
        clearTimeout(timeoutId);
        throw error;
    }
}

function showError(message, shouldRedirect = false) {
    const errorDiv = document.getElementById('teams-error');
    errorDiv.textContent = message;
    errorDiv.style.display = 'block';

    if (shouldRedirect) {
        setTimeout(() => { window.location.href = '/auth/google'; }, 2000);
    }
}

function hideError() {
    document.getElementById('teams-error').style.display = 'none';
}

async function checkSession() {
    try {
        const response = await fetchWithTimeout('/api/session-check', {}, 5000);
        if (response.status === 401) return { authenticated: false, error: null };
        const data = await response.json();
        return { authenticated: data.authenticated, error: null };
    } catch (error) {
        if (error.name === 'AbortError') return { authenticated: null, error: 'timeout' };
        if (error.message.includes('Failed to fetch')) return { authenticated: null, error: 'network' };
        return { authenticated: null, error: 'unknown' };
    }
}

async function loadTeams() {
    hideError();

    try {
        const sessionResult = await checkSession();
        if (sessionResult.error) {
            const msg = {
                timeout: 'Session check timed out. Please refresh the page and try again.',
                network: 'Unable to connect to server. Please check your connection and refresh the page.',
            }[sessionResult.error] || 'Error checking session. Please refresh the page and try again.';
            showError(msg);
            return;
        }
        if (!sessionResult.authenticated) {
            showError('Your session has expired. Redirecting to login...', true);
            return;
        }

        const response = await fetchWithTimeout('/api/teams', {}, 10000);
        if (!response.ok) {
            if (response.status === 401) {
                showError('Your session has expired. Redirecting to login...', true);
                return;
            }
            throw new Error(`Server error: ${response.status}`);
        }

        const data = await response.json();
        if (!data.teams?.length) {
            showError('No teams are available for the current week.');
            return;
        }
        const defaultSlug = data.teams[0].slug;
        populateTeamSelect(document.getElementById('team-select'), data.teams, defaultSlug);
        populateTeamSelect(document.getElementById('opponent-select'), data.teams, '', 'Auto (scheduled opponent)');
        populateTeamSelect(document.getElementById('single-team-select'), data.teams, defaultSlug);
        syncDisabledOptions();
        hideError();
    } catch (error) {
        if (error.name === 'AbortError') showError('Request timed out. Please refresh the page and try again.');
        else if (error.message.includes('Failed to fetch')) showError('Unable to connect to server. Please check your connection and try again.');
        else showError('Failed to load teams. Please refresh the page and try again.');
    }
}

function populateTeamSelect(select, teams, defaultValue, defaultLabel) {
    select.innerHTML = '';

    if (defaultLabel !== undefined) {
        const defaultOption = document.createElement('option');
        defaultOption.value = defaultValue;
        defaultOption.textContent = defaultLabel;
        select.appendChild(defaultOption);
    }

    teams.forEach(team => {
        const option = document.createElement('option');
        option.value = team.slug;
        option.textContent = team.label;
        if (team.slug === defaultValue) option.selected = true;
        select.appendChild(option);
    });
}

function syncDisabledOptions() {
    const left = document.getElementById('team-select');
    const right = document.getElementById('opponent-select');
    const leftLocked = left.value;
    const rightLocked = right.value || null;

    Array.from(right.options).forEach(opt => {
        opt.disabled = opt.value !== '' && opt.value === leftLocked;
    });
    Array.from(left.options).forEach(opt => {
        opt.disabled = opt.value === rightLocked;
    });
}

let matchupChart = null;
let marginChart = null;
let singleTeamChart = null;
let standingsChart = null;
let positionStrengthChart = null;

function clearMatchupArea() {
    if (matchupChart) { matchupChart.destroy(); matchupChart = null; }
    if (marginChart) { marginChart.destroy(); marginChart = null; }
    document.getElementById('matchup-header').innerHTML = '';
    document.getElementById('matchup-stats').innerHTML = '';
    document.getElementById('matchup-lineups').innerHTML = '';
    const marginWrapper = document.getElementById('margin-canvas').parentElement;
    if (marginWrapper) marginWrapper.style.display = 'none';
}

function clearSingleTeamArea() {
    if (singleTeamChart) { singleTeamChart.destroy(); singleTeamChart = null; }
    const stats = document.getElementById('single-team-stats');
    if (stats) stats.innerHTML = '';
}

function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
}

function winProbToFairML(p) {
    if (p == null || p <= 0 || p >= 1) return null;
    const raw = p >= 0.5 ? -100 * p / (1 - p) : 100 * (1 - p) / p;
    const rounded = Math.round(raw);
    return (rounded > 0 ? '+' : '') + rounded;
}

function formatMoneyline(storedMoneyline, winProb) {
    if (storedMoneyline) return ` (${storedMoneyline})`;
    const fair = winProbToFairML(winProb);
    return fair ? ` (${fair})` : '';
}


function renderMatchupHeader(team, opponent) {
    const header = document.getElementById('matchup-header');
    const teamProb = team.win_prob != null ? (team.win_prob * 100).toFixed(1) + '%' : '—';
    const oppProb = opponent.win_prob != null ? (opponent.win_prob * 100).toFixed(1) + '%' : '—';
    header.innerHTML = `
        <div class="side team">
            <div class="label">Team</div>
            <div class="name">${escapeHtml(team.label)}</div>
            <div class="win-prob">${teamProb}</div>
            <div class="moneyline">Win prob${formatMoneyline(team.moneyline, team.win_prob)}</div>
        </div>
        <div class="side opponent">
            <div class="label">Opponent</div>
            <div class="name">${escapeHtml(opponent.label)}</div>
            <div class="win-prob">${oppProb}</div>
            <div class="moneyline">Win prob${formatMoneyline(opponent.moneyline, opponent.win_prob)}</div>
        </div>
    `;
}

function renderMatchupStats(team, opponent) {
    const container = document.getElementById('matchup-stats');
    const rows = [
        ['Floor (10th)', team.p10, opponent.p10],
        ['Median', team.p50, opponent.p50],
        ['Ceiling (90th)', team.p90, opponent.p90],
    ];
    container.innerHTML = rows.map(([label, t, o]) => `
        <div class="stat">
            <div class="stat-label">${label}</div>
            <div class="stat-row">
                <span class="team-val">${t.toFixed(1)}</span>
                <span class="opp-val">${o.toFixed(1)}</span>
            </div>
        </div>
    `).join('');
}

const chartDefaults = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'nearest', intersect: false },
    scales: {
        x: {
            type: 'linear',
            title: { display: true, text: 'Projected points', color: '#8B949E' },
            ticks: { color: '#8B949E' },
            grid: { color: 'rgba(255,255,255,0.05)' },
        },
        y: {
            title: { display: true, text: 'Probability density', color: '#8B949E' },
            ticks: { color: '#8B949E', callback: () => '' },
            grid: { color: 'rgba(255,255,255,0.05)' },
        },
    },
    plugins: {
        legend: { labels: { color: '#FFFFFF' } },
        tooltip: {
            callbacks: {
                title: (items) => `${items[0].parsed.x.toFixed(1)} pts`,
                label: (item) => `${item.dataset.label}: ${(item.parsed.y * 100).toFixed(2)}% density`,
            },
        },
    },
};

function densityDataset(label, x, y, color) {
    const fillRgba = color === '#1493FF' ? 'rgba(20, 147, 255, 0.25)' : 'rgba(255, 107, 107, 0.25)';
    return {
        label,
        data: x.map((xi, i) => ({ x: xi, y: y[i] })),
        borderColor: color,
        backgroundColor: fillRgba,
        fill: true,
        tension: 0.3,
        pointRadius: 0,
        borderWidth: 2,
    };
}

function renderDistributionChart(payload) {
    const ctx = document.getElementById('distribution-canvas').getContext('2d');
    const { team, opponent } = payload;

    if (opponent) {
        renderMatchupHeader(team, opponent);
        renderMatchupStats(team, opponent);
    } else {
        document.getElementById('matchup-header').innerHTML =
            `<div class="side team"><div class="label">Team</div><div class="name">${escapeHtml(team.label)}</div></div>` +
            `<div class="matchup-warning">No opponent data available for this pair.</div>`;
        document.getElementById('matchup-stats').innerHTML = '';
    }

    if (matchupChart) matchupChart.destroy();

    const datasets = [densityDataset(team.label, payload.x, team.y, '#1493FF')];
    if (opponent) datasets.push(densityDataset(opponent.label, payload.x, opponent.y, '#FF6B6B'));

    matchupChart = new Chart(ctx, {
        type: 'line',
        data: { datasets },
        options: chartDefaults,
    });

    const marginWrapper = document.getElementById('margin-canvas').parentElement;
    if (payload.margin && opponent) {
        marginWrapper.style.display = '';
        renderMarginChart(payload.margin, team.label, opponent.label);
    } else {
        marginWrapper.style.display = 'none';
        if (marginChart) { marginChart.destroy(); marginChart = null; }
    }
}

function renderMarginChart(margin, teamLabel, opponentLabel) {
    const ctx = document.getElementById('margin-canvas').getContext('2d');
    const left = margin.left_x.map((x, i) => ({ x, y: margin.left_y[i] }));
    const right = margin.right_x.map((x, i) => ({ x, y: margin.right_y[i] }));

    if (marginChart) marginChart.destroy();

    marginChart = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [
                {
                    label: `${opponentLabel} wins by ≥ x`,
                    data: left,
                    borderColor: '#FF6B6B',
                    backgroundColor: 'rgba(255, 107, 107, 0.25)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                    borderWidth: 2,
                },
                {
                    label: `${teamLabel} wins by ≥ x`,
                    data: right,
                    borderColor: '#1493FF',
                    backgroundColor: 'rgba(20, 147, 255, 0.25)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0,
                    borderWidth: 2,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'nearest', intersect: false },
            scales: {
                x: {
                    type: 'linear',
                    min: -40,
                    max: 40,
                    title: { display: true, text: 'Win margin (points)', color: '#8B949E' },
                    ticks: { color: '#8B949E', stepSize: 10, callback: (v) => Math.abs(v) },
                    grid: {
                        color: (c) => c.tick.value === 0 ? 'rgba(255,255,255,0.25)' : 'rgba(255,255,255,0.05)',
                        lineWidth: (c) => c.tick.value === 0 ? 2 : 1,
                    },
                },
                y: {
                    min: 0,
                    max: 1,
                    title: { display: true, text: 'Probability', color: '#8B949E' },
                    ticks: { color: '#8B949E', stepSize: 0.2, callback: (v) => (v * 100).toFixed(0) + '%' },
                    grid: { color: 'rgba(255,255,255,0.05)' },
                },
            },
            plugins: {
                legend: { labels: { color: '#FFFFFF' } },
                tooltip: {
                    callbacks: {
                        title: () => '',
                        label: (item) => {
                            const who = item.datasetIndex === 0 ? opponentLabel : teamLabel;
                            const margin = Math.abs(item.parsed.x).toFixed(0);
                            return `${who} wins by ≥ ${margin}: ${(item.parsed.y * 100).toFixed(1)}%`;
                        },
                    },
                },
            },
        },
    });
}

function renderSingleTeamChart(payload) {
    const ctx = document.getElementById('single-distribution-canvas').getContext('2d');
    const { team } = payload;

    const statsContainer = document.getElementById('single-team-stats');
    statsContainer.innerHTML = `
        <div class="stat">
            <div class="stat-label">Floor (10th)</div>
            <div class="stat-row"><span class="team-val">${team.p10.toFixed(1)}</span></div>
        </div>
        <div class="stat">
            <div class="stat-label">Median</div>
            <div class="stat-row"><span class="team-val">${team.p50.toFixed(1)}</span></div>
        </div>
        <div class="stat">
            <div class="stat-label">Ceiling (90th)</div>
            <div class="stat-row"><span class="team-val">${team.p90.toFixed(1)}</span></div>
        </div>
    `;

    if (singleTeamChart) singleTeamChart.destroy();
    singleTeamChart = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [{
                label: team.label,
                data: payload.x.map((xi, i) => ({ x: xi, y: team.cdf[i] })),
                borderColor: '#1493FF',
                backgroundColor: 'rgba(20, 147, 255, 0.25)',
                fill: true,
                tension: 0.3,
                pointRadius: 0,
                borderWidth: 2,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'nearest', intersect: false },
            scales: {
                x: {
                    type: 'linear',
                    min: 50,
                    max: 200,
                    title: { display: true, text: 'Projected points', color: '#8B949E' },
                    ticks: { color: '#8B949E' },
                    grid: { color: 'rgba(255,255,255,0.05)' },
                },
                y: {
                    min: 0,
                    max: 1,
                    title: { display: true, text: 'Cumulative probability', color: '#8B949E' },
                    ticks: { color: '#8B949E', stepSize: 0.2, callback: (v) => (v * 100).toFixed(0) + '%' },
                    grid: { color: 'rgba(255,255,255,0.05)' },
                },
            },
            plugins: {
                legend: { labels: { color: '#FFFFFF' } },
                tooltip: {
                    callbacks: {
                        title: (items) => `${items[0].parsed.x.toFixed(1)} pts`,
                        label: (item) => `P(score ≤ x): ${(item.parsed.y * 100).toFixed(1)}%`,
                    },
                },
            },
        },
    });
}

async function loadDistribution(teamParam, opponentParam) {
    try {
        let url = `/api/team_distribution?team=${teamParam}&week=${currentWeek}`;
        if (opponentParam) url += `&opponent=${opponentParam}`;
        const response = await fetchWithTimeout(url, {}, 10000);
        if (!response.ok) {
            clearMatchupArea();
            if (response.status === 401) showError('Your session has expired. Redirecting to login...', true);
            else if (response.status === 404) showError('Matchup data is not available for the selected teams yet.');
            else showError('Failed to load matchup chart. Please refresh the page and try again.');
            return false;
        }
        const payload = await response.json();
        hideError();
        renderDistributionChart(payload);
        if (payload.opponent) {
            loadMatchupLineups(payload.team.owner, payload.opponent.owner);
        } else {
            document.getElementById('matchup-lineups').innerHTML = '';
        }
        return true;
    } catch (error) {
        clearMatchupArea();
        if (error.name === 'AbortError') showError('Matchup chart request timed out. Please try again.');
        else showError('Unable to load matchup chart. Please check your connection and try again.');
        return false;
    }
}

function splitPlayerName(name) {
    const safe = escapeHtml(name);
    const i = safe.indexOf(' ');
    return i === -1 ? safe : `${safe.slice(0, i)}<br>${safe.slice(i + 1)}`;
}

function lockMarker(player) {
    return player.is_locked ? ' <span class="tnc-lp-lock" title="Final: game over, points locked">final</span>' : '';
}

function renderLineupCol(lineup) {
    if (!lineup?.length) return '<div class="tnc-lp-col"><div class="tnc-lp-empty">No lineup</div></div>';
    const rows = lineup.map(p => `
        <div class="tnc-lp-row">
            <span class="tnc-lp-name">${splitPlayerName(p.player_name)}${lockMarker(p)}</span>
            <span class="tnc-lp-pos">${escapeHtml(p.position)}</span>
            <span class="tnc-lp-pts tnc-tab-num">${(p.projected_points || 0).toFixed(1)}</span>
        </div>
    `).join('');
    return `<div class="tnc-lp-col">${rows}</div>`;
}

async function loadMatchupLineups(leftOwner, rightOwner) {
    const container = document.getElementById('matchup-lineups');
    const placeholder = '<div class="tnc-lp-col"><div class="tnc-lp-empty">Loading…</div></div>';
    container.innerHTML = `${placeholder}<div class="tnc-mc-divider"></div>${placeholder}`;

    try {
        const [leftRes, rightRes] = await Promise.all([
            fetchWithTimeout(`/api/lineup/${encodeURIComponent(leftOwner)}`, {}, 10000),
            fetchWithTimeout(`/api/lineup/${encodeURIComponent(rightOwner)}`, {}, 10000),
        ]);
        if (!leftRes.ok || !rightRes.ok) return;
        const [left, right] = await Promise.all([leftRes.json(), rightRes.json()]);
        container.innerHTML = `${renderLineupCol(left)}<div class="tnc-mc-divider"></div>${renderLineupCol(right)}`;
    } catch (error) {
        console.error('Error loading matchup lineups:', error);
        container.innerHTML = '';
    }
}

async function loadSingleTeamDistribution(teamParam) {
    try {
        const url = `/api/team_distribution?team=${teamParam}&week=${currentWeek}`;
        const response = await fetchWithTimeout(url, {}, 10000);
        if (!response.ok) {
            clearSingleTeamArea();
            if (response.status === 401) showError('Your session has expired. Redirecting to login...', true);
            else if (response.status === 404) showError('Distribution data is not available for the selected team yet.');
            else showError('Failed to load team chart. Please refresh the page and try again.');
            return false;
        }
        hideError();
        renderSingleTeamChart(await response.json());
        return true;
    } catch (error) {
        clearSingleTeamArea();
        if (error.name === 'AbortError') showError('Team chart request timed out. Please try again.');
        else showError('Unable to load team chart. Please check your connection and try again.');
        return false;
    }
}

async function loadPlayers(teamParam, playersContainer) {
    try {
        const response = await fetchWithTimeout(`/api/team_players?team=${teamParam}`, {}, 10000);
        if (!response.ok) {
            if (response.status === 401) {
                showError('Your session has expired. Redirecting to login...', true);
                return;
            }
            throw new Error(`Server error: ${response.status}`);
        }

        const data = await response.json();
        if (!(data.starters?.length || data.bench?.length)) {
            playersContainer.innerHTML = '<p style="color: var(--tnc-fg-muted); text-align: center; padding: 30px;">No player data available for this team.</p>';
            return;
        }

        let html = `
            <div style="overflow-x: auto;">
                <table style="width: 100%; background: var(--tnc-surface); border-radius: 12px; overflow: hidden; border: 1px solid var(--tnc-border);">
                    <thead>
                        <tr style="background: var(--tnc-bg-deep); border-bottom: 2px solid var(--tnc-blue);">
                            <th style="padding: 8px 10px; text-align: left; color: var(--tnc-blue); font-weight: 600; font-size: 12px;">Player</th>
                            <th style="padding: 8px 10px; text-align: center; color: var(--tnc-blue); font-weight: 600; font-size: 12px;">Pos</th>
                            <th style="padding: 8px 10px; text-align: right; color: var(--tnc-blue); font-weight: 600; font-size: 12px;">Proj</th>
                            <th style="padding: 8px 10px; text-align: right; color: var(--tnc-blue); font-weight: 600; font-size: 12px;">Var</th>
                        </tr>
                    </thead>
                    <tbody>
        `;

        let rowIndex = 0;
        const renderRow = (player) => {
            const muDisplay = player.mu !== null ? player.mu.toFixed(1) : '-';
            const varDisplay = player.var !== null ? player.var.toFixed(1) : '-';
            html += `
                <tr style="border-bottom: 1px solid var(--tnc-border); ${rowIndex % 2 === 0 ? 'background: rgba(20, 147, 255, 0.05);' : ''}">
                    <td style="padding: 6px 8px; color: var(--tnc-fg); font-size: 13px;">${escapeHtml(player.player_first_name)} ${escapeHtml(player.player_last_name)}</td>
                    <td style="padding: 6px 8px; text-align: center; color: var(--tnc-fg-muted); font-weight: 600; font-size: 12px;">${escapeHtml(player.position)}</td>
                    <td style="padding: 6px 8px; text-align: right; color: var(--tnc-blue); font-weight: 600; font-size: 14px;">${muDisplay}</td>
                    <td style="padding: 6px 8px; text-align: right; color: var(--tnc-fg-muted); font-weight: 600; font-size: 13px;">${varDisplay}</td>
                </tr>
            `;
            rowIndex++;
        };

        data.starters?.forEach(renderRow);
        if (data.bench?.length) {
            html += `<tr><td colspan="4" class="roster-divider">Bench Players</td></tr>`;
            data.bench.forEach(renderRow);
        }
        html += `</tbody></table></div>`;
        playersContainer.innerHTML = html;
    } catch (error) {
        const msg = error.name === 'AbortError' ? 'Request timed out. Please try again.'
            : error.message.includes('Failed to fetch') ? 'Unable to connect to server. Please check your connection.'
            : 'Error loading player data.';
        playersContainer.innerHTML = `<p style="color: #ef4444; text-align: center; padding: 30px;">${msg}</p>`;
    }
}

async function renderMatchup() {
    syncDisabledOptions();
    const left = document.getElementById('team-select').value;
    const right = document.getElementById('opponent-select').value;
    if (!left) return false;

    const teamParam = encodeURIComponent(left);
    const opponentParam = right ? encodeURIComponent(right) : null;
    return loadDistribution(teamParam, opponentParam);
}

async function renderSingleTeam() {
    const team = document.getElementById('single-team-select').value;
    if (!team) return false;
    const teamParam = encodeURIComponent(team);
    const [ok] = await Promise.all([
        loadSingleTeamDistribution(teamParam),
        loadPlayers(teamParam, document.getElementById('players-table-container')),
    ]);
    return ok;
}

function renderStandingsChart(data) {
    const teams = data.teams;
    if (!teams.length) return;
    const cutoff = data.playoff_cutoff ?? PLAYOFF_CUTOFF;
    const ctx = document.getElementById('standings-chart').getContext('2d');
    const colors = teams.map((_, i) => i < cutoff ? '#1493FF' : 'rgba(139, 148, 158, 0.6)');
    const maxProj = Math.max(...teams.map(t => t.proj_mean ?? 0));

    if (standingsChart) standingsChart.destroy();
    standingsChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: teams.map(() => ''),
            datasets: [{
                data: teams.map(t => t.proj_mean ?? 0),
                backgroundColor: colors,
                borderColor: colors,
                borderWidth: 0,
                borderRadius: 4,
                barPercentage: 0.88,
                categoryPercentage: 0.92,
            }],
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            layout: { padding: { left: 0, right: 44, top: 4, bottom: 28 } },
            scales: {
                x: {
                    beginAtZero: true,
                    max: maxProj * 1.02,
                    ticks: { display: false },
                    grid: { display: false },
                    border: { display: false },
                },
                y: { display: false },
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: (items) => teams[items[0].dataIndex].label,
                        label: (item) => {
                            const t = teams[item.dataIndex];
                            return [
                                `Record: ${t.record}`,
                                `Projected: ${fmt1(t.proj_mean)} pts`,
                                `P10–P90: ${fmt1(t.p10)} – ${fmt1(t.p90)}`,
                                `Win prob: ${t.win_prob != null ? t.win_prob.toFixed(1) + '%' : '—'}`,
                            ];
                        },
                    },
                },
                playoffCutoff: { cutoff },
                inBarLabels: { teams },
                winLabels: { teams },
                cardCenteredAxisTitle: { text: `Week ${currentWeek} Projected Points` },
            },
        },
    });
}

function renderPositionStrengthChart(data) {
    if (!data.teams.length) return;
    const ctx = document.getElementById('position-strength-chart').getContext('2d');
    const positions = data.positions || POSITION_GROUPS;
    const labels = data.teams.map(t => t.label);
    const datasets = positions.map(pos => ({
        label: pos,
        data: data.teams.map(t => t.by_position[pos]?.mu ?? 0),
        backgroundColor: POS_COLORS[pos],
        borderColor: POS_COLORS[pos],
        borderWidth: 0,
        stack: 'stack',
        playersByTeam: data.teams.map(t => t.by_position[pos]?.players ?? []),
    }));

    if (positionStrengthChart) positionStrengthChart.destroy();
    positionStrengthChart = new Chart(ctx, {
        type: 'bar',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            layout: { padding: { left: 0, right: 0 } },
            scales: {
                x: {
                    stacked: true,
                    ticks: { color: '#FFFFFF', font: { weight: '600', size: 11 }, autoSkip: false, maxRotation: 60, minRotation: 60 },
                    grid: { display: false },
                },
                y: { stacked: true, beginAtZero: true, display: false },
            },
            plugins: {
                legend: {
                    labels: {
                        color: '#FFFFFF',
                        boxWidth: 10,
                        boxHeight: 10,
                        padding: 6,
                        font: { size: 11 },
                    },
                },
                tooltip: {
                    callbacks: {
                        label: (item) => {
                            const players = item.dataset.playersByTeam[item.dataIndex] || [];
                            const head = `${item.dataset.label}: ${fmt1(item.parsed.y)} pts`;
                            const list = players.map(p => `  ${p.name} (${fmt1(p.mu)})`);
                            return [head, ...list];
                        },
                    },
                },
            },
        },
    });
}

const fmtChance = (pct) => (pct == null ? '—' : pct < 1 && pct > 0 ? '<1%' : `${Math.round(pct)}%`);

function placeBars(places, cutoff, peak, className) {
    return places.map((pct, i) => {
        const height = pct > 0 ? Math.max(4, (pct / peak) * 100) : 0;
        const side = i < cutoff ? ' is-in' : '';
        return `<span class="${className}${side}"><span style="height: ${height}%"></span></span>`;
    }).join('');
}

function renderPlayoffDetail(team, cutoff, peak) {
    const columns = team.places.map((pct, i) => `
        <div class="pp-place${i < cutoff ? ' is-in' : ''}">
            <span class="pp-place-pct tnc-tab-num">${fmtChance(pct)}</span>
            ${placeBars([pct], i < cutoff ? 1 : 0, peak, 'pp-place-bar')}
            <span class="pp-place-n tnc-tab-num">${i + 1}</span>
        </div>
    `).join('');
    return `
        <div class="pp-detail-grid">${columns}</div>
        <div class="pp-detail-note">Average finish: ${team.expected_place.toFixed(1)}</div>
    `;
}

function renderPlayoffPicture(data) {
    const list = document.getElementById('playoff-picture');
    if (!data.teams.length) {
        list.innerHTML = '<div class="pp-empty">No futures yet.</div>';
        return;
    }
    const cutoff = data.playoff_cutoff ?? PLAYOFF_CUTOFF;
    const peak = Math.max(...data.teams.flatMap(team => team.places));
    document.getElementById('playoff-picture-caption').textContent =
        `Chance of each final place. The top ${cutoff} make the playoffs. Tap a team for the numbers.`;

    const head = `
        <div class="pp-head">
            <span></span>
            <span class="pp-head-places"><span>1st</span><span>${data.teams.length}th</span></span>
            <span>Playoffs</span>
        </div>
    `;
    const rows = data.teams.map((team, i) => `
        <div class="pp-team">
            <button type="button" class="pp-row" data-team="${i}" aria-expanded="false">
                <span class="pp-name">${escapeHtml(team.label)}</span>
                <span class="pp-bars">${placeBars(team.places, cutoff, peak, 'pp-bar')}</span>
                <span class="pp-odds tnc-tab-num">${fmtChance(team.playoffs)}</span>
            </button>
            <div class="pp-detail" hidden>${renderPlayoffDetail(team, cutoff, peak)}</div>
        </div>
    `).join('');
    list.innerHTML = head + rows;
}

function togglePlayoffTeam(row) {
    const open = row.getAttribute('aria-expanded') !== 'true';
    row.setAttribute('aria-expanded', String(open));
    row.nextElementSibling.hidden = !open;
}

const RACE_COLORS = [
    '#1493FF', '#FF6B6B', '#3FB950', '#FCD34D', '#C084FC', '#FB923C',
    '#22D3EE', '#F472B6', '#A3E635', '#94A3B8', '#E879F9', '#2DD4BF',
];
let seasonRace = null;
let seasonRaceChart = null;

function renderSeasonRace(series) {
    if (!seasonRace?.weeks.length) return;
    const ctx = document.getElementById('season-race-chart').getContext('2d');
    const datasets = seasonRace.teams.map((team, i) => ({
        label: team.label,
        data: team[series],
        borderColor: RACE_COLORS[i % RACE_COLORS.length],
        backgroundColor: RACE_COLORS[i % RACE_COLORS.length],
        borderWidth: 2,
        pointRadius: 3,
        tension: 0.2,
    }));

    if (seasonRaceChart) seasonRaceChart.destroy();
    seasonRaceChart = new Chart(ctx, {
        type: 'line',
        data: { labels: seasonRace.weeks.map(week => `Wk ${week}`), datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: { offset: true, ticks: { color: '#8B949E' }, grid: { display: false } },
                // Few teams hold much of a title chance, so that scale fits the leader rather than 100%.
                y: {
                    min: 0,
                    max: series === 'playoffs' ? 100 : undefined,
                    ticks: { color: '#8B949E', maxTicksLimit: 5, callback: (v) => `${v}%` },
                    grid: { color: 'rgba(255,255,255,0.05)' },
                },
            },
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: { color: '#FFFFFF', boxWidth: 8, boxHeight: 8, padding: 8, font: { size: 11 } },
                },
                tooltip: {
                    itemSort: (a, b) => b.parsed.y - a.parsed.y,
                    callbacks: { label: (item) => `${item.dataset.label}: ${fmtChance(item.parsed.y)}` },
                },
            },
        },
    });
}

function showRaceSeries(button) {
    document.querySelectorAll('.race-toggle-btn').forEach(btn => {
        const on = btn === button;
        btn.classList.toggle('is-on', on);
        btn.setAttribute('aria-pressed', String(on));
    });
    renderSeasonRace(button.dataset.series);
}

async function loadLeagueView() {
    try {
        const responses = await Promise.all([
            fetchWithTimeout('/api/league_overview', {}, 10000),
            fetchWithTimeout('/api/position_strength', {}, 10000),
            fetchWithTimeout('/api/playoff_picture', {}, 10000),
            fetchWithTimeout('/api/season_race', {}, 10000),
        ]);
        if (responses.some(response => !response.ok)) {
            showError('Could not load league analytics. Please refresh the page.');
            return false;
        }
        const [overview, posStrength, picture, race] = await Promise.all(responses.map(response => response.json()));
        seasonRace = race;
        renderPlayoffPicture(picture);
        renderSeasonRace('playoffs');
        renderStandingsChart(overview);
        renderPositionStrengthChart(posStrength);
        return true;
    } catch (error) {
        console.error('Error loading league view:', error);
        showError('Could not load league analytics. Please refresh the page.');
        return false;
    }
}

const record = ({ won, lost }) => `${won}–${lost}`;
const share = ({ inside, teams }) => (teams ? `${Math.round((inside / teams) * 100)}%` : '—');

function renderMisses(misses) {
    if (!misses.length) return '<div class="pp-empty">None.</div>';
    return misses.map(miss => {
        const diff = miss.actual - miss.projected;
        const sign = diff > 0 ? '+' : '−';
        return `
            <div class="miss-row">
                <div class="miss-who">
                    <span class="miss-name">${escapeHtml(miss.player)}</span>
                    <span class="miss-meta">${escapeHtml(miss.position)} · ${escapeHtml(miss.owner ?? '')}</span>
                </div>
                <div class="miss-points tnc-tab-num">${miss.projected.toFixed(1)} → ${miss.actual.toFixed(1)}</div>
                <div class="miss-diff tnc-tab-num ${diff > 0 ? 'is-up' : 'is-down'}">${sign}${Math.abs(diff).toFixed(1)}</div>
            </div>
        `;
    }).join('');
}

function renderModelReport(report) {
    const hasReport = report.week != null;
    document.getElementById('model-empty').hidden = hasReport;
    document.getElementById('model-report').hidden = !hasReport;
    if (!hasReport) return;

    const { moneyline, coverage } = report;
    document.getElementById('model-report-title').textContent = `Week ${report.week}`;
    document.getElementById('model-moneyline').textContent = record(moneyline.week);
    document.getElementById('model-moneyline-season').textContent = `Season ${record(moneyline.season)}`;
    document.getElementById('model-coverage').textContent = `${coverage.week.inside} of ${coverage.week.teams}`;
    document.getElementById('model-coverage-season').textContent = `Season ${share(coverage.season)}`;
    document.getElementById('model-booms').innerHTML = renderMisses(report.booms);
    document.getElementById('model-busts').innerHTML = renderMisses(report.busts);
}

async function loadModelReport() {
    try {
        const response = await fetchWithTimeout('/api/model_report', {}, 10000);
        if (!response.ok) {
            showError('Could not load the model report. Please refresh the page.');
            return false;
        }
        renderModelReport(await response.json());
        return true;
    } catch (error) {
        console.error('Error loading model report:', error);
        showError('Could not load the model report. Please refresh the page.');
        return false;
    }
}

const dollars = (amount) => `$${Math.round(Math.abs(amount)).toLocaleString()}`;

function renderMoneyMarkets(markets) {
    if (!markets.length) return '<div class="pp-empty">No bets yet.</div>';
    return markets.map(market => {
        const sides = market.sides.map(side => {
            const width = market.stake ? (side.stake / market.stake) * 100 : 0;
            return `
                <div class="money-side">
                    <span class="money-side-label">${escapeHtml(side.label)}</span>
                    <span class="money-side-bar"><span style="width: ${width}%"></span></span>
                    <span class="money-side-stake tnc-tab-num">${dollars(side.stake)}</span>
                </div>
            `;
        }).join('');
        return `
            <div class="money-market">
                <div class="money-market-head">
                    <span>${escapeHtml(market.title)}</span>
                    <span class="tnc-tab-num">${dollars(market.stake)}</span>
                </div>
                ${sides}
            </div>
        `;
    }).join('');
}

function renderMoney(money) {
    const house = document.getElementById('money-house');
    house.textContent = `${money.house_net < 0 ? '−' : money.house_net > 0 ? '+' : ''}${dollars(money.house_net)}`;
    house.classList.toggle('is-up', money.house_net > 0);
    house.classList.toggle('is-down', money.house_net < 0);
    document.getElementById('money-favorite').textContent =
        money.favorite_share == null ? '—' : `${Math.round(money.favorite_share)}%`;

    const { slips, stake } = money.parlays;
    document.getElementById('money-parlays').textContent =
        slips ? `Plus ${dollars(stake)} on ${slips} parlay${slips === 1 ? '' : 's'}.` : 'Singles only.';
    document.getElementById('money-markets').innerHTML = renderMoneyMarkets(money.markets);
    document.getElementById('money-futures').innerHTML = renderMoneyMarkets(money.futures);
}

async function loadMoney() {
    try {
        const response = await fetchWithTimeout('/api/money', {}, 10000);
        if (!response.ok) {
            showError('Could not load the money. Please refresh the page.');
            return false;
        }
        renderMoney(await response.json());
        return true;
    } catch (error) {
        console.error('Error loading money:', error);
        showError('Could not load the money. Please refresh the page.');
        return false;
    }
}

let leagueRendered = false;
let moneyRendered = false;
let modelRendered = false;
let matchupsRendered = false;
let teamsRendered = false;

async function activateTab(tab) {
    document.querySelectorAll('.tnc-tab').forEach(t => {
        t.classList.toggle('is-on', t.dataset.tab === tab);
    });
    document.querySelectorAll('.tab-panel').forEach(panel => {
        panel.style.display = panel.dataset.panel === tab ? '' : 'none';
    });

    if (tab === 'league' && !leagueRendered) {
        leagueRendered = await loadLeagueView();
    }
    if (tab === 'matchups' && !matchupsRendered) {
        matchupsRendered = await renderMatchup();
    }
    if (tab === 'teams' && !teamsRendered) {
        teamsRendered = await renderSingleTeam();
    }
    if (tab === 'model' && !modelRendered) {
        modelRendered = await loadModelReport();
    }
    if (tab === 'money' && !moneyRendered) {
        moneyRendered = await loadMoney();
    }
}

function bindEvents() {
    document.getElementById('analytics-tabs').addEventListener('click', e => {
        const tab = e.target.closest('.tnc-tab');
        if (tab) activateTab(tab.dataset.tab);
    });
    document.getElementById('playoff-picture').addEventListener('click', e => {
        const row = e.target.closest('.pp-row');
        if (row) togglePlayoffTeam(row);
    });
    document.getElementById('race-toggle').addEventListener('click', e => {
        const button = e.target.closest('.race-toggle-btn');
        if (button) showRaceSeries(button);
    });

    document.getElementById('team-select').addEventListener('change', renderMatchup);
    document.getElementById('opponent-select').addEventListener('change', renderMatchup);
    document.getElementById('single-team-select').addEventListener('change', renderSingleTeam);
}

bindEvents();
loadTeams();
loadLeagueView().then(ok => { leagueRendered = ok; });
