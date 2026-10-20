"""NEW (1.5.0): frees the graphics card when Evora isn't being used.

Two timers, because the two halves of Evora are used differently:

  listen  the speaker models (who is talking, overlapping voices, voice
          fingerprints). Only listening uses them. They stay loaded while a
          listening session is open, and are freed a short while after the
          last one closes (default 5 minutes). Once PyTorch has used the
          card, its own working memory (a few hundred MB) stays until Evora
          restarts; everything else is given back.
  speech  Whisper, which turns speech into text for dictation and
          listening alike. Freed after a longer quiet spell (default 1 hour).

Either comes back by itself the next time it's needed: the first clip
after waits a few seconds while it loads, and nothing is lost.

The settings are kept in idle_settings.json beside Evora, and Frivo sets
them from its own Settings through POST /idle.
"""

import json
import threading
import time
from pathlib import Path

# Minutes; 0 means never.
DEFAULTS = {"listen_minutes": 5, "speech_minutes": 60}
CHOICES = {"listen_minutes": (5, 15, 60, 0), "speech_minutes": (15, 60, 240, 0)}
# A listening session nobody has sent audio to for this long is treated as
# closed (Frivo was shut without stopping listening, say).
STALE_SESSION_SECONDS = 30 * 60


class IdleKeeper:
    def __init__(self, root, unload_listen, unload_speech, is_listen_loaded, is_speech_loaded):
        self.path = Path(root) / "idle_settings.json"
        self.unload = {"listen": unload_listen, "speech": unload_speech}
        self.loaded = {"listen": is_listen_loaded, "speech": is_speech_loaded}
        self.lock = threading.Lock()
        now = time.monotonic()
        self.last_used = {"listen": now, "speech": now}
        self.sessions = {}            # listening session id -> last audio (monotonic)
        self.freed_at = {"listen": None, "speech": None}
        self.settings = dict(DEFAULTS)
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.settings.update({k: v for k, v in saved.items() if k in DEFAULTS and v in CHOICES[k]})
        except (OSError, ValueError):
            pass
        self._thread = None

    # ---- what Evora tells it ----

    def touch(self, kind):
        with self.lock:
            self.last_used[kind] = time.monotonic()
            self.freed_at[kind] = None

    def session_opened(self, ident):
        with self.lock:
            self.sessions[ident] = time.monotonic()
            self.last_used["listen"] = time.monotonic()

    def session_audio(self, ident):
        with self.lock:
            if ident in self.sessions:
                self.sessions[ident] = time.monotonic()
            self.last_used["listen"] = time.monotonic()

    def session_closed(self, ident):
        with self.lock:
            self.sessions.pop(ident, None)
            # The countdown starts when listening stops, not at the last word.
            self.last_used["listen"] = time.monotonic()

    def configure(self, values):
        updated = dict(self.settings)
        for key in DEFAULTS:
            if key in values:
                try:
                    value = int(values[key])
                except (TypeError, ValueError):
                    raise ValueError(f"{key} must be one of {CHOICES[key]}")
                if value not in CHOICES[key]:
                    raise ValueError(f"{key} must be one of {CHOICES[key]}")
                updated[key] = value
        with self.lock:
            self.settings = updated
        try:
            self.path.write_text(json.dumps(updated, indent=2), encoding="utf-8")
        except OSError:
            pass
        return self.status()

    # ---- what it reports ----

    def _listening(self, now):
        return any(now - seen < STALE_SESSION_SECONDS for seen in self.sessions.values())

    def status(self):
        now = time.monotonic()
        with self.lock:
            listening = self._listening(now)
            out = {"listening": listening, "settings": dict(self.settings)}
            for kind, key in (("listen", "listen_minutes"), ("speech", "speech_minutes")):
                timeout = self.settings[key] * 60
                loaded = bool(self.loaded[kind]())
                idle = now - self.last_used[kind]
                unload_in = None
                if loaded and timeout and not (kind == "listen" and listening):
                    unload_in = max(0, round(timeout - idle))
                out[kind] = {
                    "loaded": loaded,
                    "timeout_seconds": timeout,
                    "idle_seconds": round(idle),
                    "unload_in": unload_in,
                    "never": timeout == 0,
                    "freed_seconds_ago": round(now - self.freed_at[kind]) if self.freed_at[kind] else None,
                }
        return out

    # ---- the clock ----

    def check(self):
        """Frees whatever has been idle long enough. Run every few seconds."""
        now = time.monotonic()
        with self.lock:
            listening = self._listening(now)
            due = []
            for kind, key in (("listen", "listen_minutes"), ("speech", "speech_minutes")):
                timeout = self.settings[key] * 60
                if not timeout or (kind == "listen" and listening):
                    continue
                if now - self.last_used[kind] >= timeout:
                    due.append(kind)
        for kind in due:
            try:
                if self.loaded[kind]():
                    self.unload[kind]()
                    with self.lock:
                        self.freed_at[kind] = time.monotonic()
                    print(f"Idle: freed the {'speaker' if kind == 'listen' else 'speech-to-text'} "
                          f"models from the graphics card.", flush=True)
            except Exception as exc:
                print(f"Idle: couldn't free {kind}: {exc!r}", flush=True)

    def start(self, every=10):
        if self._thread:
            return

        def loop():
            while True:
                time.sleep(every)
                self.check()

        self._thread = threading.Thread(target=loop, daemon=True, name="evora-idle")
        self._thread.start()
