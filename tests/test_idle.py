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


def test_defaults_never_free_anything():
    # CHANGED (1.5.0): off unless set. On a shared card, freeing memory let
    # Ollama take it, and listening couldn't get it back.
    k, clock, state = keeper()
    clock.now += 100 * 3600
    k.check()
    assert state["freed"] == []
    assert k.settings == {"listen_minutes": 0, "speech_minutes": 0}


def test_timers_free_listening_after_5_minutes_and_speech_after_an_hour_when_set():
    k, clock, state = keeper(listen_minutes=5, speech_minutes=60)
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
    k, clock, state = keeper(listen_minutes=5, speech_minutes=60)
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
    k, clock, state = keeper(listen_minutes=5, speech_minutes=60)
    k.session_opened("s1")
    clock.now += evora_idle.STALE_SESSION_SECONDS + 1
    assert not k.status()["listening"]
    clock.now += 5 * 60
    k.check()
    assert "listen" in state["freed"]


def test_use_resets_the_clock():
    k, clock, state = keeper(listen_minutes=5, speech_minutes=60)
    clock.now += 50 * 60
    k.touch("speech")
    clock.now += 50 * 60
    k.check()
    assert "speech" not in state["freed"]


def test_status_counts_down():
    k, clock, state = keeper(listen_minutes=5, speech_minutes=60)
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
    k, clock, state = keeper(listen_minutes=5, speech_minutes=60)
    state["speech"] = False
    assert k.status()["speech"]["unload_in"] is None


# --- the speaker models' sleep and wake --------------------------------------

def _settle(seconds=2.0):
    """Waits for the background wake thread."""
    import time
    end = time.time() + seconds
    while time.time() < end and any(t.name == "speaker-model-wake" and t.is_alive() for t in threading.enumerate()):
        time.sleep(0.02)


def test_speaker_models_sleep_and_wake():
    import evora_speakers

    models = evora_speakers.LocalModels.__new__(evora_speakers.LocalModels)
    models.lock = threading.RLock()
    models.device = "cpu"
    models.pipeline = object()
    models.separator = object()
    models.state = "ready"
    models.stage = "ready"
    models.error = ""
    models.notice = ""
    models.wake_failed_at = 0.0
    models.wake_error = ""
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
    _settle()
    assert loads == [1] and models.state == "ready" and used
    models.wake()                       # already awake: no second load
    assert loads == [1]
    models.state = "not_configured"     # never set up: nothing to sleep
    assert models.sleep() is False


def test_wake_never_blocks_and_a_failed_wake_is_retried():
    """CHANGED (1.5.0): the load runs in the background; a clip meanwhile
    just has no speaker labels. A load that fails leaves the models
    sleeping (not broken) and is tried again a minute later."""
    import time
    import evora_speakers

    models = evora_speakers.LocalModels.__new__(evora_speakers.LocalModels)
    models.lock = threading.RLock()
    models.device = "cpu"
    models.pipeline = object()
    models.separator = object()
    models.state = "ready"
    models.stage = "ready"
    models.error = ""
    models.notice = ""
    models.on_use = None
    models.on_sleep = None
    models.wake_failed_at = 0.0
    models.wake_error = ""
    gate = threading.Event()
    loads = []

    def slow_load():
        loads.append(1)
        gate.wait(5)
        if len(loads) == 1:
            models.pipeline = None
            models.state = "error"
            models.error = "CUDA out of memory"
        else:
            models.pipeline = object()
            models.state = "ready"

    models._load = slow_load
    assert models.sleep()
    started = time.perf_counter()
    models.wake()                       # returns at once, while the load runs
    assert time.perf_counter() - started < 0.5
    assert models.state == "loading"
    gate.set()
    _settle()
    assert models.state == "sleeping"   # it failed: asleep, not broken
    assert "couldn't load again" in models.notice
    models.wake()                       # too soon: no second try yet
    assert loads == [1]
    models.wake_failed_at -= 61
    gate.clear()
    models.wake()
    gate.set()
    _settle()
    assert loads == [1, 1] and models.state == "ready" and models.notice == ""
