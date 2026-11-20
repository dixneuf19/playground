from coco_lanta.show import GRACE, MAX_HOLD, MIN_HOLD, ROTATE, Show


def show_with(cameras: int) -> Show:
    show = Show()
    for i in range(cameras):
        show.join(f"cam-{i}", f"Cam {i}", 0)
    return show


def test_first_camera_goes_on_air() -> None:
    show = show_with(1)
    assert show.on_air() == ["cam-0"]


def test_auto_rotates_between_cameras() -> None:
    show = show_with(3)
    show.tick(ROTATE - 1)
    assert show.program == "cam-0"
    show.tick(ROTATE)
    assert show.program == "cam-1"
    show.tick(2 * ROTATE)
    assert show.program == "cam-2"
    show.tick(3 * ROTATE)
    assert show.program == "cam-0"


def test_a_single_camera_stays_on() -> None:
    show = show_with(1)
    show.tick(10 * ROTATE)
    assert show.program == "cam-0"


def test_loud_camera_cuts_in_after_the_minimum_hold() -> None:
    show = show_with(2)
    show.speakers(["cam-1"], MIN_HOLD - 1)
    assert show.program == "cam-0"
    show.speakers(["cam-1"], MIN_HOLD)
    assert show.program == "cam-1"


def test_loud_program_is_not_rotated_away_until_max_hold() -> None:
    show = show_with(2)
    show.speakers(["cam-0"], ROTATE - 1)
    show.tick(ROTATE)
    assert show.program == "cam-0"
    show.speakers(["cam-0"], MAX_HOLD - 1)
    show.tick(MAX_HOLD)
    assert show.program == "cam-1"


def test_unknown_speakers_are_ignored() -> None:
    show = show_with(2)
    show.speakers(["screen-1", "nobody"], 100)
    assert show.program == "cam-0"


def test_take_holds_the_camera_until_auto() -> None:
    show = show_with(2)
    show.take("cam-1", 1)
    show.speakers(["cam-0"], 50)
    show.tick(10 * ROTATE)
    assert (show.program, show.auto) == ("cam-1", False)
    show.set_auto(300)
    show.tick(300 + ROTATE)
    assert show.program == "cam-0"


def test_show_goes_on_when_the_manual_pick_leaves() -> None:
    show = show_with(2)
    show.take("cam-1", 1)
    show.leave("cam-1", 10)
    show.tick(10 + GRACE - 1)
    assert show.program == "cam-1"  # a flapping socket doesn't cut the feed
    show.tick(10 + GRACE + 1)
    assert (show.program, show.auto) == ("cam-0", True)


def test_camera_back_within_grace_keeps_its_place() -> None:
    show = show_with(2)
    show.take("cam-1", 1)
    show.leave("cam-1", 10)
    show.join("cam-1", "Cam 1", 12)
    show.tick(100)
    assert (show.program, show.auto) == ("cam-1", False)


def test_duel_shows_two_cameras() -> None:
    show = show_with(3)
    show.set_layout("duel", 1)
    assert show.on_air() == ["cam-0", "cam-1"]
    assert show.snapshot()["duo"] == ["cam-0", "cam-1"]
    show.take("cam-2", 2)  # replaces the one picked longest ago
    assert (show.layout, show.on_air()) == ("duel", ["cam-1", "cam-2"])
    show.take("cam-1", 3)  # already there
    assert show.on_air() == ["cam-1", "cam-2"]
    show.kick("cam-2", 4)
    assert show.on_air() == ["cam-1", "cam-0"]


def test_duel_with_a_single_camera() -> None:
    show = show_with(1)
    show.set_layout("duel", 1)
    assert show.on_air() == ["cam-0"]


def test_mosaic_shows_everyone_and_nothing_when_empty() -> None:
    show = show_with(2)
    show.set_layout("mosaic", 1)
    assert show.on_air() == ["cam-0", "cam-1"]
    show.kick("cam-0", 2)
    show.kick("cam-1", 2)
    assert show.on_air() == []
    assert show.program is None


def test_scores_stay_in_range() -> None:
    show = Show()
    show.score("faucille", -1)
    show.score("marteau", 1)
    show.score("supporters", 1)
    assert show.scores == {"faucille": 0, "marteau": 1}


def test_timer_pause_and_resume() -> None:
    show = Show()
    show.timer_start(60, 100)
    show.timer_toggle(130)
    assert (show.timer_ends, show.timer_left) == (None, 30)
    show.timer_toggle(500)
    assert (show.timer_ends, show.timer_left) == (530, None)
    show.timer_reset()
    assert show.snapshot()["timer"] == {"ends": None, "left": None}
