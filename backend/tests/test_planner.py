from app import planner


def kinds(blocks):
    return [b["kind"] for b in blocks]


def test_30_minute_standard_plan():
    p = planner.build_plan(30, {})
    # review, then one Context -> Sentence -> Transfer block (about 10 + 10 + 5 minutes)
    assert kinds(p) == ["review", "new"] and p[1]["minutes"] == 25
    assert planner.study_minutes(p) == 30


def test_study_minutes_match_request_for_all_presets_and_custom():
    for m in (10, 15, 30, 45, 60, 75, 90, 120, 150, 180, 240):
        p = planner.build_plan(m, {"break_enabled": True, "break_every": 45, "break_minutes": 5})
        assert abs(planner.study_minutes(p) - m) < 0.6, m


def test_long_sessions_add_variety_not_repetition():
    p120 = kinds(planner.build_plan(120, {"break_enabled": False}))
    for k in ("multi", "long", "vocab", "words", "final"):
        assert k in p120
    p30 = kinds(planner.build_plan(30, {}))
    assert "long" not in p30


def test_breaks_inserted_for_long_sessions_only():
    s = {"break_enabled": True, "break_every": 45, "break_minutes": 7}
    assert "break" not in kinds(planner.build_plan(30, s))
    p = planner.build_plan(120, s)
    breaks = [b for b in p if b["kind"] == "break"]
    assert len(breaks) >= 2 and all(b["minutes"] == 7 for b in breaks)
    assert p[-1]["kind"] != "break"
    assert "break" not in kinds(planner.build_plan(120, {**s, "break_enabled": False}))
