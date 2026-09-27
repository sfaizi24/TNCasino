from pipeline.waivers import Assignment, Hole, ProjectedPlayer, allocate

NO_CAPS: dict[str, float] = {}


def hole(roster_id, slot, position, faab_remaining=100, waiver_position=None):
    return Hole(roster_id, slot, position, faab_remaining, waiver_position or roster_id)


def free_agent(player_id, position, mu, positions=None):
    return ProjectedPlayer(
        sleeper_player_id=player_id,
        player_name=f"Player {player_id}",
        position=position,
        positions=frozenset(positions or {position}),
        nfl_team="SEA",
        mu=mu,
        sigma=mu / 2,
        var=(mu / 2) ** 2,
        n_sources=3,
    )


def picks(assignments: list[Assignment]) -> dict[tuple[int, str], str | None]:
    return {
        (a.hole.roster_id, a.hole.slot): a.free_agent.sleeper_player_id if a.free_agent else None for a in assignments
    }


def test_more_faab_claims_first():
    holes = [hole(1, "WR2", "WR", faab_remaining=40), hole(2, "WR1", "WR", faab_remaining=180)]
    pool = [free_agent("wr_b", "WR", 9.0), free_agent("wr_a", "WR", 12.0), free_agent("wr_c", "WR", 6.0)]

    assert picks(allocate(holes, pool, NO_CAPS)) == {(2, "WR1"): "wr_a", (1, "WR2"): "wr_b"}


def test_waiver_position_breaks_faab_ties():
    holes = [hole(3, "WR1", "WR", waiver_position=7), hole(1, "WR1", "WR", waiver_position=2)]
    pool = [free_agent("wr_a", "WR", 12.0), free_agent("wr_b", "WR", 9.0)]

    assert picks(allocate(holes, pool, NO_CAPS)) == {(1, "WR1"): "wr_a", (3, "WR1"): "wr_b"}


def test_team_with_two_holes_gets_one_free_agent_per_hole():
    holes = [hole(1, "RB1", "RB"), hole(1, "RB2", "RB")]
    pool = [free_agent("rb_a", "RB", 10.0), free_agent("rb_b", "RB", 8.0)]

    assert picks(allocate(holes, pool, NO_CAPS)) == {(1, "RB1"): "rb_a", (1, "RB2"): "rb_b"}


def test_cap_binds_on_an_outlier_but_keeps_the_free_agents_own_spread():
    pool = [free_agent("rb_star", "RB", 21.0)]

    [assignment] = allocate([hole(1, "RB1", "RB")], pool, {"RB": 11.5})

    assert assignment.mu == 11.5
    assert assignment.free_agent.sigma == 10.5
    assert assignment.free_agent.var == 10.5**2


def test_cap_does_not_raise_a_weak_free_agent():
    [assignment] = allocate([hole(1, "TE", "TE")], [free_agent("te", "TE", 4.0)], {"TE": 9.0})

    assert assignment.mu == 4.0


def test_position_without_a_cap_is_uncapped():
    [assignment] = allocate([hole(1, "K", "K")], [free_agent("k", "K", 8.0)], {"RB": 1.0})

    assert assignment.mu == 8.0


def test_flex_is_filled_last_from_the_leftover_pool():
    holes = [hole(1, "FLEX", "FLEX", faab_remaining=250), hole(2, "RB2", "RB", faab_remaining=10)]
    pool = [free_agent("rb_a", "RB", 11.0), free_agent("wr_a", "WR", 10.0), free_agent("qb_a", "QB", 20.0)]

    assignments = allocate(holes, pool, {"RB": 12.0, "FLEX": 9.5})

    assert picks(assignments) == {(2, "RB2"): "rb_a", (1, "FLEX"): "wr_a"}
    assert [a.hole.slot for a in assignments] == ["RB2", "FLEX"]
    assert assignments[1].mu == 9.5


def test_each_free_agent_is_used_once_and_an_empty_pool_leaves_the_hole_unresolved():
    holes = [hole(1, "DEF", "DEF", faab_remaining=200), hole(2, "DEF", "DEF", faab_remaining=100)]

    assignments = allocate(holes, [free_agent("SEA", "DEF", 7.0)], NO_CAPS)

    assert picks(assignments) == {(1, "DEF"): "SEA", (2, "DEF"): None}
    assert assignments[1].mu == 0.0


def test_multi_position_free_agent_can_fill_either_position():
    pool = [free_agent("hybrid", "RB", 9.0, positions={"RB", "WR"})]

    assert picks(allocate([hole(1, "WR1", "WR")], pool, NO_CAPS)) == {(1, "WR1"): "hybrid"}
