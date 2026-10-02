from couchctl.config import Device, Redirect
from couchctl.redirect import watch

APP = "app.unwanted"
dev = Device(name="tv", kind="samsung", host="x", redirects=[Redirect(APP, "app.wanted", "test")])


def run(sequence, polls):
    """Drive watch() with a scripted screen. False means the device could not be reached."""
    steps = iter(sequence)
    launched = []

    def peek(_dev):
        v = next(steps, None)
        return (False, None) if v is False else (True, v)

    def perform(d, app_id):
        launched.append(app_id)
        return {"device": d.name, "from": app_id, "to": d.redirect_for(app_id).target, "outcome": "success", "message": "ok"}

    told = []
    watch(dev, polls=polls, poll_s=0, perform=perform, on_redirect=told.append, peek_fn=peek)
    return launched, told


def test_fires_only_when_the_app_becomes_visible():
    # seed, then: pressed, still there, left, pressed again
    launched, _ = run([None, APP, APP, None, APP], polls=4)
    assert launched == [APP, APP]


def test_an_app_already_on_screen_at_start_is_left_alone():
    launched, _ = run([APP, APP, APP], polls=2)
    assert launched == []


def test_losing_the_device_and_coming_back_is_not_a_press():
    launched, _ = run([APP, False, APP], polls=2)
    assert launched == []


def test_a_real_press_after_coming_back_is_still_answered():
    launched, _ = run([APP, False, None, APP], polls=3)
    assert launched == [APP]


def test_each_redirect_is_reported_as_it_happens():
    _, told = run([None, APP], polls=1)
    assert len(told) == 1 and told[0]["outcome"] == "success"


def test_an_app_without_a_redirect_is_ignored():
    launched, _ = run([None, "app.other"], polls=1)
    assert launched == []
