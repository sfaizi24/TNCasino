// A receiver projected for 14 under V4's fitted WR parameters (pipeline/model/params/v2.3.json):
// σ = a + b·μ, a dud chance of 1 / (1 + e^−(c + d·μ)), and a dud scoring anywhere in [0, μ/4].
const PLAYER = { mu: 14, a: 3.5906, b: 0.2966, c: 0.374, d: -0.2019 };
const SIMS = 50000;

const CHART = { left: 12, right: 308, base: 122, top: 18, maxPoints: 42 };

function curveParams({ mu, a, b, c, d }) {
    const dudChance = 1 / (1 + Math.exp(-(c + d * mu)));
    const dudTop = mu / 4;
    // The lognormal part's mean is raised so the mix of dud and lognormal still averages μ.
    const mean = (mu - dudChance * dudTop / 2) / (1 - dudChance);
    const sigma = a + b * mu;
    const phi = Math.sqrt(sigma ** 2 + mean ** 2);
    return {
        dudChance,
        dudTop,
        logMu: Math.log(mean ** 2 / phi),
        logSigma: Math.sqrt(Math.log((phi / mean) ** 2)),
    };
}

function lognormalDensity(x, logMu, logSigma) {
    if (x <= 0) return 0;
    const z = (Math.log(x) - logMu) / logSigma;
    return Math.exp(-z * z / 2) / (x * logSigma * Math.sqrt(2 * Math.PI));
}

function drawPlayerCurve(svg) {
    const p = curveParams(PLAYER);
    const points = [];
    for (let x = 0; x <= CHART.maxPoints; x += 0.25) {
        points.push([x, (1 - p.dudChance) * lognormalDensity(x, p.logMu, p.logSigma)]);
    }
    const dudHeight = p.dudChance / p.dudTop;
    const peak = Math.max(dudHeight, ...points.map(([, y]) => y));

    const toX = x => CHART.left + (x / CHART.maxPoints) * (CHART.right - CHART.left);
    const toY = y => CHART.base - (y / peak) * (CHART.base - CHART.top);

    const body = points.map(([x, y]) => `${toX(x).toFixed(1)},${toY(y).toFixed(1)}`).join(' L');
    const dudLeft = toX(0);
    const dudRight = toX(p.dudTop);
    const dudY = toY(dudHeight);
    const projection = toX(PLAYER.mu);
    const ticks = [0, 10, 20, 30, 40]
        .map(x => `<text x="${toX(x)}" y="${CHART.base + 16}" text-anchor="middle">${x === 40 ? '40 pts' : x}</text>`)
        .join('');

    svg.innerHTML = `
        <path class="body" d="M${body} L${toX(CHART.maxPoints)},${CHART.base} L${dudLeft},${CHART.base} Z"/>
        <path class="dud" d="M${dudLeft},${CHART.base} L${dudLeft},${dudY} L${dudRight},${dudY} L${dudRight},${CHART.base} Z"/>
        <line class="axis" x1="${CHART.left}" x2="${CHART.right}" y1="${CHART.base}" y2="${CHART.base}"/>
        <line class="projection" x1="${projection}" x2="${projection}" y1="${CHART.top - 6}" y2="${CHART.base}"/>
        <text class="label" x="${projection + 5}" y="${CHART.top - 2}">Projection ${PLAYER.mu}</text>
        <text class="label" x="${dudLeft}" y="${dudY - 6}">Dud ${Math.round(p.dudChance * 100)}%</text>
        ${ticks}
    `;
}

// The site's own fair-odds rule: favourites at −100·p/(1 − p), underdogs at +100·(1 − p)/p.
function americanOdds(chance) {
    const odds = chance >= 0.5 ? (-100 * chance) / (1 - chance) : (100 * (1 - chance)) / chance;
    return Math.round(odds);
}

function stakeReturn(odds, stake) {
    const profit = odds > 0 ? (stake * odds) / 100 : (stake * 100) / -odds;
    return stake + profit;
}

function showPrice(wins) {
    const chance = wins / SIMS;
    const odds = americanOdds(chance);
    document.getElementById('winsCount').textContent = wins.toLocaleString('en-US');
    document.getElementById('winsChance').textContent = `${Math.round(chance * 100)}%`;
    document.getElementById('winsOdds').textContent = odds > 0 ? `+${odds}` : `−${-odds}`;
    document.getElementById('winsReturn').textContent = `$${stakeReturn(odds, 100).toFixed(2)}`;
}

drawPlayerCurve(document.getElementById('playerCurve'));

const slider = document.getElementById('winsSlider');
slider.addEventListener('input', () => showPrice(Number(slider.value)));
showPrice(Number(slider.value));
