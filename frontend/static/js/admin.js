    let currentWeek = null;
    let currentPeriodData = null;
    let pendingBets = [];
    let settlementPreview = null;

    const OUTCOME_LABELS = { won: 'Won', lost: 'Lost', push: 'Push', undecided: 'Undecided' };

    async function getCurrentWeek() {
        try {
            const periodsResponse = await fetch('/api/admin/betting_periods');
            const periods = await periodsResponse.json();

            const unsettledPeriods = periods.filter(p => !p.is_settled);
            if (unsettledPeriods.length > 0) {
                currentPeriodData = unsettledPeriods[0];
                return unsettledPeriods[0].week;
            }

            return 10;
        } catch (error) {
            console.error('Error getting current week:', error);
            return 10;
        }
    }

    function updateActiveWeekBanner(periods) {
        const unsettledPeriod = periods.find(p => !p.is_settled);

        if (!unsettledPeriod) {
            document.getElementById('activeWeekNum').textContent = '--';
            document.getElementById('activeWeekStatus').innerHTML = `
                <span class="status-dot settled"></span>
                <span>No Active Period</span>
            `;
            document.getElementById('activeWeekStatus').className = 'status-badge settled';
            document.getElementById('lockCountdown').textContent = 'Create a new betting period to enable betting';
            document.getElementById('windowLine').textContent = '';
            document.getElementById('quickActions').innerHTML = `
                <button class="btn btn-success btn-sm" onclick="suggestNextWeek()">Create Next Week</button>
            `;
            return;
        }

        currentPeriodData = unsettledPeriod;
        const week = unsettledPeriod.week;
        const isLocked = unsettledPeriod.is_locked;
        const lockTime = new Date(unsettledPeriod.lock_time);
        const now = new Date();

        document.getElementById('activeWeekNum').textContent = week;
        showWindow(week);

        if (isLocked) {
            document.getElementById('activeWeekStatus').innerHTML = `
                <span class="status-dot locked"></span>
                <span>Locked</span>
            `;
            document.getElementById('activeWeekStatus').className = 'status-badge locked';
            document.getElementById('lockCountdown').textContent = 'Bets are locked - waiting for settlement';
            document.getElementById('quickActions').innerHTML = `
                <button class="btn btn-warning btn-sm" onclick="unlockPeriod(${week})">Unlock Betting</button>
                <button class="btn btn-outline btn-sm" onclick="document.getElementById('settleWeekNumber').focus()">Settle Week</button>
            `;
        } else {
            document.getElementById('activeWeekStatus').innerHTML = `
                <span class="status-dot open"></span>
                <span>Open for Betting</span>
            `;
            document.getElementById('activeWeekStatus').className = 'status-badge open';

            // Calculate time remaining
            const timeDiff = lockTime - now;
            if (timeDiff > 0) {
                const days = Math.floor(timeDiff / (1000 * 60 * 60 * 24));
                const hours = Math.floor((timeDiff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
                const mins = Math.floor((timeDiff % (1000 * 60 * 60)) / (1000 * 60));

                let countdown = 'Locks in ';
                if (days > 0) countdown += `${days}d `;
                if (hours > 0) countdown += `${hours}h `;
                countdown += `${mins}m`;

                document.getElementById('lockCountdown').textContent = countdown;
            } else {
                document.getElementById('lockCountdown').textContent = 'Lock time passed - will lock on next action';
            }

            document.getElementById('quickActions').innerHTML = `
                <button class="btn btn-outline btn-sm" onclick="suggestNextWeek()">Setup Week ${week + 1}</button>
            `;
        }
    }

    // The window the runs set between the admin's lock times, in the admin's own zone.
    async function showWindow(week) {
        const response = await fetch(`/api/betting_window?week=${week}`);
        const betting = await response.json();
        const when = iso => new Date(iso).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' });
        let line = 'Window: closed';
        if (betting.state === 'open') line = `Window: open until ${when(betting.closes_at)}`;
        if (betting.state === 'paused') line = `Window: paused since ${when(betting.closes_at)}`;
        document.getElementById('windowLine').textContent = line;
    }

    function suggestNextWeek() {
        const nextWeek = currentPeriodData ? currentPeriodData.week + 1 : 14;
        document.getElementById('periodWeek').value = nextWeek;
        document.getElementById('periodWeek').focus();
    }

    async function loadBettingPeriods() {
        try {
            const response = await fetch('/api/admin/betting_periods');
            const periods = await response.json();

            // Update the active week banner
            updateActiveWeekBanner(periods);

            if (periods.length === 0) {
                document.getElementById('periodsTable').innerHTML = '<p style="color: var(--tnc-fg-muted);">No betting periods set</p>';
                return;
            }

            let html = '<table><thead><tr><th>Week</th><th>Lock Time</th><th>Status</th><th>Actions</th></tr></thead><tbody>';
            periods.forEach(period => {
                const isActive = !period.is_settled && !periods.some(p => !p.is_settled && p.week > period.week);
                let statusClass = period.is_settled ? 'settled' : (period.is_locked ? 'locked' : 'open');
                let statusLabel = period.is_settled ? 'Settled' : (period.is_locked ? 'Locked' : 'Open');
                let unlockBtn = period.is_locked && !period.is_settled ?
                    `<button class="btn btn-warning btn-sm" onclick="unlockPeriod(${period.week})">Unlock</button>` :
                    '';
                let activeIndicator = isActive ? ' (Active)' : '';
                html += `<tr class="${isActive ? 'active-row' : ''}">
                    <td>Week ${period.week}${activeIndicator}</td>
                    <td>${period.lock_time}</td>
                    <td><span class="status-badge ${statusClass}"><span class="status-dot ${statusClass}"></span>${statusLabel}</span></td>
                    <td>${unlockBtn}</td>
                </tr>`;
            });
            html += '</tbody></table>';
            document.getElementById('periodsTable').innerHTML = html;
        } catch (error) {
            console.error('Error loading betting periods:', error);
            document.getElementById('periodsTable').innerHTML = '<p style="color: #ff4444;">Error loading periods</p>';
        }
    }

    async function loadPendingBets() {
        try {
            const response = await fetch(`/api/admin/pending_bets?week=${currentWeek}`);
            const bets = await response.json();
            pendingBets = bets;

            if (bets.length === 0) {
                document.getElementById('pendingBetsTable').innerHTML = '<p style="color: var(--tnc-fg-muted);">No pending bets for this week</p>';
                return;
            }

            let html = '<table class="bets-table"><thead><tr><th>User</th><th>Description</th><th>Amount</th><th>Odds</th><th>Actions</th></tr></thead><tbody>';
            bets.forEach(bet => {
                html += `<tr>
                    <td>${bet.user_id.substring(0, 8)}...</td>
                    <td>${escapeHtml(bet.description)}</td>
                    <td>$${bet.amount.toFixed(2)}</td>
                    <td>${bet.odds}</td>
                    <td>
                        <button class="btn btn-success btn-sm" onclick="settleBet(${bet.id}, true)">Win</button>
                        <button class="btn btn-danger btn-sm" onclick="settleBet(${bet.id}, false)">Loss</button>
                        <button class="btn btn-outline btn-sm" onclick="voidBet(${bet.id})">Void</button>
                    </td>
                </tr>`;
            });
            html += '</tbody></table>';
            document.getElementById('pendingBetsTable').innerHTML = html;
        } catch (error) {
            console.error('Error loading pending bets:', error);
            document.getElementById('pendingBetsTable').innerHTML = '<p style="color: #ff4444;">Error loading bets</p>';
        }
    }

    document.getElementById('setPeriodForm').addEventListener('submit', async (e) => {
        e.preventDefault();

        const week = document.getElementById('periodWeek').value;
        const lockTime = document.getElementById('periodLockTime').value;

        try {
            const response = await fetch('/api/admin/set_betting_period', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ week, lock_time: lockTime })
            });

            const result = await response.json();

            if (result.success) {
                document.getElementById('setPeriodMessage').innerHTML = '<div class="message message-success">Betting period set successfully!</div>';
                loadBettingPeriods();
            } else {
                document.getElementById('setPeriodMessage').innerHTML = `<div class="message message-error">${result.error}</div>`;
            }
        } catch (error) {
            console.error('Error setting betting period:', error);
            document.getElementById('setPeriodMessage').innerHTML = '<div class="message message-error">Error setting betting period</div>';
        }
    });

    async function settleBet(betId, won) {
        try {
            const response = await fetch('/api/admin/settle_bet', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bet_id: betId, won: won })
            });

            const result = await response.json();

            if (result.success) {
                document.getElementById('settleMessage').innerHTML = `<div class="message message-success">Bet settled as ${won ? 'WON' : 'LOST'}!</div>`;
                loadPendingBets();
                loadSettlementPreview();
            } else {
                document.getElementById('settleMessage').innerHTML = `<div class="message message-error">${result.error}</div>`;
            }
        } catch (error) {
            console.error('Error settling bet:', error);
            document.getElementById('settleMessage').innerHTML = '<div class="message message-error">Error settling bet</div>';
        }
    }

    async function voidBet(betId) {
        const bet = pendingBets.find(pending => pending.id === betId);
        if (!confirm(`Void "${bet.description}" ($${bet.amount.toFixed(2)})? The stake goes back to the bettor and the bet stops counting.`)) {
            return;
        }

        const message = document.getElementById('settleMessage');
        try {
            const response = await fetch('/api/admin/void_bet', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bet_id: betId })
            });
            const result = await response.json();

            if (result.success) {
                message.innerHTML = '<div class="message message-success">Bet voided and the stake refunded</div>';
                loadPendingBets();
                loadSettlementPreview();
            } else {
                message.innerHTML = `<div class="message message-error">${escapeHtml(result.error)}</div>`;
            }
        } catch (error) {
            console.error('Error voiding bet:', error);
            message.innerHTML = '<div class="message message-error">Error voiding bet</div>';
        }
    }

    async function loadSettlementPreview() {
        const container = document.getElementById('settlementPreview');
        try {
            const response = await fetch(`/api/admin/settlement_preview?week=${currentWeek}`);
            const preview = await response.json();

            if (!preview.success) {
                container.innerHTML = `<div class="message message-error">${escapeHtml(preview.error)}</div>`;
                return;
            }

            settlementPreview = preview;
            container.innerHTML = scoresStrip(preview) + outcomesTable(preview);
        } catch (error) {
            console.error('Error loading settlement preview:', error);
            container.innerHTML = '<p style="color: #ff4444;">Error loading settlement preview</p>';
        }
    }

    function scoresStrip(preview) {
        const played = preview.scores.filter(score => score.points !== null);
        if (played.length === 0) {
            return `<p class="scores-strip" style="color: var(--tnc-fg-muted);">No scores published for week ${preview.week} yet</p>`;
        }

        let html = '<div class="scores-strip">';
        preview.scores.forEach(score => {
            const points = score.points === null ? 'no score' : score.points.toFixed(2);
            html += `<span class="score-chip">${escapeHtml(score.team)} <strong>${points}</strong></span>`;
        });
        return html + '</div>';
    }

    function outcomesTable(preview) {
        if (preview.bets.length === 0) {
            return '<p style="color: var(--tnc-fg-muted);">No pending bets for this week</p>';
        }

        let html = '<table class="bets-table"><thead><tr><th>Bettor</th><th>Bet</th><th>Reason</th><th>Outcome</th></tr></thead><tbody>';
        preview.bets.forEach(bet => {
            html += `<tr>
                <td>${escapeHtml(bet.user)}</td>
                <td>${escapeHtml(bet.description)}</td>
                <td>${escapeHtml(bet.reason)}</td>
                <td><span class="status-badge ${bet.outcome}">${OUTCOME_LABELS[bet.outcome]}</span></td>
            </tr>`;
        });
        html += '</tbody></table>';

        if (preview.decided > 0) {
            const noun = preview.decided === 1 ? 'bet' : 'bets';
            html += `<button class="btn btn-success settle-outcomes" onclick="settleOutcomes(this)">Settle ${preview.decided} decided ${noun}</button>`;
        }
        return html;
    }

    async function settleOutcomes(button) {
        button.disabled = true;
        const decided = settlementPreview.bets.filter(bet => bet.outcome !== 'undecided');
        const message = document.getElementById('outcomesMessage');

        try {
            const response = await fetch('/api/admin/settle_outcomes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    week: settlementPreview.week,
                    bets: decided.map(bet => ({ id: bet.id, outcome: bet.outcome }))
                })
            });
            const result = await response.json();

            if (result.success) {
                message.innerHTML = settledMessage(result, decided);
            } else {
                message.innerHTML = `<div class="message message-error">${escapeHtml(result.error)}</div>`;
            }
        } catch (error) {
            console.error('Error settling the week:', error);
            message.innerHTML = '<div class="message message-error">Error settling bets</div>';
        }

        // Bets settled before any failure stand, so both cards reload either way.
        loadSettlementPreview();
        loadPendingBets();
    }

    function settledMessage(result, sent) {
        const betsById = new Map(sent.map(bet => [bet.id, bet]));
        let html = '';

        if (result.settled.length > 0) {
            html += `<div class="message message-success">Settled ${result.settled.length} of ${sent.length}`;
            result.settled.forEach(id => {
                const bet = betsById.get(id);
                html += `<br>${escapeHtml(bet.description)} — ${OUTCOME_LABELS[bet.outcome]}`;
            });
            html += '</div>';
        }

        if (result.skipped.length > 0) {
            html += `<div class="message message-error">Skipped ${result.skipped.length}`;
            result.skipped.forEach(skip => {
                html += `<br>${escapeHtml(betsById.get(skip.id).description)} — ${escapeHtml(skip.reason)}`;
            });
            html += '</div>';
        }
        return html;
    }

    function escapeHtml(text) {
        return String(text ?? '').replace(/[&<>"']/g, c => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[c]));
    }

    document.getElementById('settleWeekForm').addEventListener('submit', async (e) => {
        e.preventDefault();

        const week = document.getElementById('settleWeekNumber').value;

        try {
            const response = await fetch('/api/admin/settle_week', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ week: parseInt(week) })
            });

            const result = await response.json();

            if (result.success) {
                document.getElementById('settleWeekMessage').innerHTML = '<div class="message message-success">Week marked as settled!</div>';
                loadBettingPeriods();
            } else {
                document.getElementById('settleWeekMessage').innerHTML = `<div class="message message-error">${result.error}</div>`;
            }
        } catch (error) {
            console.error('Error settling week:', error);
            document.getElementById('settleWeekMessage').innerHTML = '<div class="message message-error">Error settling week</div>';
        }
    });

    async function unlockPeriod(week) {
        console.log('Unlock period called for week:', week);

        if (!confirm(`Are you sure you want to unlock Week ${week}? Users will be able to place and remove bets again.`)) {
            console.log('User cancelled unlock');
            return;
        }

        console.log('Sending unlock request...');

        try {
            const response = await fetch('/api/admin/unlock_period', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ week: week })
            });

            console.log('Response status:', response.status);

            const result = await response.json();
            console.log('Response data:', result);

            if (result.success) {
                alert(`Week ${week} unlocked successfully!`);
                loadBettingPeriods();
            } else {
                alert('Error unlocking period: ' + result.error);
            }
        } catch (error) {
            console.error('Error unlocking period:', error);
            alert('Error unlocking period: ' + error.message);
        }
    }

    async function initializeAdmin() {
        currentWeek = await getCurrentWeek();
        document.getElementById('currentWeek').textContent = currentWeek;
        document.getElementById('settlementWeek').textContent = currentWeek;
        document.getElementById('periodWeek').value = currentWeek;
        document.getElementById('settleWeekNumber').value = currentWeek;

        loadBettingPeriods();
        loadSettlementPreview();
        loadPendingBets();
    }

    initializeAdmin();