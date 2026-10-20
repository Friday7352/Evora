"""NEW (1.5.0): freeing the graphics card when idle (evora_idle.py), with a
made-up clock and stand-ins for the models: no graphics card needed."""

import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import evora_idle


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def keeper(tmp=None, **settings):
    clock = Clock()
    evora_idle.time.monotonic = clock
    state = {"listen": True, "speech": True, "freed": []}

    def unloader(kind):
        def go():
            state[kind] = False
            state["freed"].append(kind)
        return go

    k = evora_idle.IdleKeeper(tmp or tempfile.mkdtemp(), unloader("listen"), unloader("speech"),
                              lambda: state["listen"], lambda: state["speech"])
    if settings:
        k.configure(settings)
    return k, clock, state


def test_defaults_free_listening_after_5_minutes_and_speech_after_an_hour():
    k, clock, state = keeper()
    clock.now += 4 * 60
    k.check()
    assert state["freed"] == []
    clock.now += 61
    k.check()
    assert state["freed"] == ["listen"]
    clock.now += 60 * 60
    k.check()
    assert state["freed"] == ["listen", "speech"]


def test_listening_keeps_the_speaker_models_however_quiet():
    k, clock, state = keeper()
    k.session_opened("s1")
    clock.now += 25 * 60     # nobody talking for 25 minutes, still listening
    k.check()
    assert "listen" not in state["freed"]
    assert k.status()["listen"]["unload_in"] is None and k.status()["listening"]
    k.session_closed("s1")   # the countdown starts when listening stops
    clock.now += 4 * 60
    k.check()
    assert "listen" not in state["freed"]
    clock.now += 61
    k.check()
    assert "listen" in state["freed"]


def test_a_session_nobody_closed_goes_stale():
    k, clock, state = keeper()
    k.session_opened("s1")
    clock.now += evora_idle.STALE_SESSION_SECONDS + 1
    assert not k.status()["listening"]
    clock.now += 5 * 60
    k.check()
    assert "listen" in state["freed"]


def test_use_resets_the_clock():
    k, clock, state = keeper()
    clock.now += 50 * 60
    k.touch("speech")
    clock.now += 50 * 60
    k.check()
    assert "speech" not in state["freed"]


def test_status_counts_down():
    k, clock, state = keeper()
    clock.now += 60
    s = k.status()
    assert s["listen"]["unload_in"] == 240 and s["speech"]["unload_in"] == 3540
    assert s["listen"]["loaded"] and not s["listen"]["never"]


def test_never_never_frees():
    k, clock, state = keeper(listen_minutes=0, speech_minutes=0)
    clock.now += 100 * 3600
    k.check()
    assert state["freed"] == []
    assert k.status()["speech"]["unload_in"] is None and k.status()["speech"]["never"]


def test_settings_are_checked_and_kept():
    tmp = tempfile.mkdtemp()
    k, _, _ = keeper(tmp)
    k.configure({"listen_minutes": 15, "speech_minutes": 240})
    assert json.loads(open(os.path.join(tmp, "idle_settings.json")).read()) == {"listen_minutes": 15, "speech_minutes": 240}
    again, _, _ = keeper(tmp)
    assert again.settings == {"listen_minutes": 15, "speech_minutes": 240}
    for bad in ({"listen_minutes": 7}, {"speech_minutes": "soon"}):
        try:
            k.configure(bad)
            assert False, bad
        except ValueError:
            pass


def test_nothing_loaded_reports_no_countdown():
    k, clock, state = keeper()
    state["speech"] = False
    assert k.status()["speech"]["unload_in"] is None


# --- the speaker models' sleep and wake --------------------------------------

def test_speaker_models_sleep_and_wake():
    import evora_speakers

    models = evora_speakers.LocalModels.__new__(evora_speakers.LocalModels)
    models.lock = threading.RLock()
    models.device = "cpu"
    models.pipeline = object()
    models.separator = object()
    models.state = "ready"
    models.stage = "ready"
    used, slept, loads = [], [], []
    models.on_use = lambda: used.append(1)
    models.on_sleep = lambda: slept.append(1)

    def fake_load():
        loads.append(1)
        models.pipeline = object()
        models.state = "ready"

    models._load = fake_load
    assert models.is_loaded()
    assert models.sleep() is True
    assert models.state == "sleeping" and not models.is_loaded() and slept == [1]
    models.wake()
    assert loads == [1] and models.state == "ready" and used
    models.wake()                       # already awake: no second load
    assert loads == [1]
    models.state = "not_configured"     # never set up: nothing to sleep
    assert models.sleep() is False
