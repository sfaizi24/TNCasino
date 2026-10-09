"""The admin page's actions from a shell, for the weekly routine: `flask --app app admin <command>`.

On the droplet they run against production, as the page does: `cd /opt/tncasino && venv/bin/flask --app app admin ...`.
"""

import click
from flask.cli import AppGroup

from . import admin_actions, ledger, settlement
from .database import db
from .models import Bet, BettingPeriod
from .routes.helpers import friendly_description, get_current_season, get_current_week

admin_cli = AppGroup("admin", help="Betting periods and settlement, as on the admin page.")


@admin_cli.command("periods")
def list_periods():
    """The current season's betting periods, newest first."""
    periods = db.session.query(BettingPeriod).filter_by(season=get_current_season())
    for period in periods.order_by(BettingPeriod.week.desc()):
        state = "settled" if period.is_settled else "locked" if period.is_locked else "open"
        click.echo(f"{period.season} week {period.week:>2}  lock {period.lock_time:%Y-%m-%d %H:%M} UTC  {state}")


@admin_cli.command("set-period")
@click.option("--week", type=int, required=True)
@click.option("--lock", "lock_time", required=True, help="UTC, as 2026-10-11T13:30: the week's first Sunday kickoff.")
def set_period(week, lock_time):
    """Create a week's betting period in the published league's season, or move its lock and reopen it."""
    try:
        period = admin_actions.set_period(week, admin_actions.parse_lock_time(lock_time))
    except admin_actions.NoPublishedLeague as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"{period.season} week {period.week}: locks {period.lock_time:%Y-%m-%d %H:%M} UTC")


@admin_cli.command("settle")
@click.option("--week", type=int, required=True)
@click.option("--apply", is_flag=True, help="Settle every decided bet; without it the outcomes are only shown.")
def settle(week, apply):
    """Judge the week's pending bets on its published scores, and settle the decided ones with --apply."""
    try:
        scores = settlement.team_scores(week)
        outcomes = settlement.outcomes_for_week(week, scores)
    except settlement.SettlementError as error:
        raise click.ClickException(str(error)) from error

    for score in scores.values():
        points = "no score" if score.points is None else f"{score.points:.2f}"
        click.echo(f"{score.team:<24} {points}")
    click.echo()
    for result in outcomes:
        bet = result.bet
        click.echo(f"#{bet.id:<5} {result.outcome:<9} ${bet.amount:<8.2f} {friendly_description(bet.description)}")
        click.echo(f"       {result.reason}")

    decided = [result for result in outcomes if result.outcome != settlement.UNDECIDED]
    click.echo(f"\n{len(decided)} decided, {len(outcomes) - len(decided)} undecided")
    if not apply:
        return

    for result in decided:
        reason = admin_actions.settle_as_shown(result, result.outcome, week)
        click.echo(f"#{result.bet.id}: {reason or result.outcome}")


@admin_cli.command("settle-bet")
@click.argument("bet_id", type=int)
@click.option("--won/--lost", required=True)
@click.option("--week", type=int, help="The week a futures result posts to; the current week by default.")
def settle_bet(bet_id, won, week):
    """Win or lose a bet by hand: the champion, a futures parlay after the final, a bet placed before market keys."""
    bet = _pending_bet(bet_id)
    if not admin_actions.settles_by_hand(bet):
        raise click.ClickException("a weekly parlay settles through `settle`, leg by leg")
    admin_actions.settle_by_hand(bet, won, week or get_current_week())
    click.echo(f"#{bet_id}: {'won' if won else 'lost'}")


@admin_cli.command("void")
@click.argument("bet_id", type=int)
def void(bet_id):
    """Refund a pending bet in full."""
    bet = _pending_bet(bet_id)
    ledger.void(bet)
    db.session.commit()
    click.echo(f"#{bet_id}: void")


@admin_cli.command("close-week")
@click.option("--week", type=int, required=True)
def close_week(week):
    """Mark a week settled once every bet in it is, which closes its window for good."""
    if not admin_actions.close_week(week):
        raise click.ClickException(f"week {week} has no betting period")
    click.echo(f"week {week}: settled")


def _pending_bet(bet_id):
    bet = db.session.get(Bet, bet_id)
    if bet is None:
        raise click.ClickException(f"bet {bet_id} not found")
    if bet.status != "pending":
        raise click.ClickException(f"bet {bet_id} is already {bet.status}")
    return bet
