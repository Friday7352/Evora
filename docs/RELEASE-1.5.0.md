# Evora 1.5.0

## Frees the graphics card when idle (off unless you turn it on)

Two timers, because dictation and listening use Evora differently:

- **The listening models** (who is talking, overlapping voices, voice fingerprints) are the biggest part, and only listening uses them. They stay loaded while listening is on, and can be freed a while after it stops.
- **Speech-to-text** (Whisper), used by dictation and listening alike, can be freed after a quiet spell.

Both are **off (Never) by default.** The first build of this defaulted to 5 minutes and 1 hour, and on a graphics card Evora shares with Ollama that went badly: memory Evora gave up was memory Ollama took, and when listening started again the models came back into a full card: slowly, with Evora blocking each clip while it loaded, or not at all, leaving speaker labelling broken until a restart. Now:

- The timers default to Never, and Frivo's Settings > Evora says so.
- Loading the listening models again never blocks a clip: it runs in the background, as at startup, and clips in the meantime are transcribed without speaker labels.
- A load that fails (the card is full) leaves the models asleep, not broken, with a notice on the speaker settings page, and is tried again a minute later.
- Speech-to-text that can't load again says why in the clip's error instead of a bare failure, and is tried again on the next clip.

Either comes back by itself on the next clip. Once PyTorch has used the graphics card, its own working memory (a few hundred MB) stays until Evora restarts; everything else is given back.

The timers are kept in `idle_settings.json` and can be changed with `POST /idle` (`listen_minutes`: 5, 15, 60 or 0 for never; `speech_minutes`: 15, 60, 240 or 0). Frivo sets them from its own Settings. `GET /idle`, and `GET /gpu` under `idle`, say what's loaded and when it'll be freed; `/health` lists the `idle` capability. The status page shows whether speech-to-text is loaded.
