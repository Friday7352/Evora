# Evora 1.5.0

## Frees the graphics card when idle

Two timers, because dictation and listening use Evora differently:

- **The listening models** (who is talking, overlapping voices, voice fingerprints) are the biggest part, and only listening uses them. They stay loaded while listening is on, and are freed 5 minutes after it stops. Starting listening again reloads them in a few seconds.
- **Speech-to-text** (Whisper), used by dictation and listening alike, is freed after an hour with neither.

Either comes back by itself on the next clip, which waits a few seconds while it loads. Once PyTorch has used the graphics card, its own working memory (a few hundred MB) stays until Evora restarts; everything else is given back.

The timers are kept in `idle_settings.json` and can be changed with `POST /idle` (`listen_minutes`: 5, 15, 60 or 0 for never; `speech_minutes`: 15, 60, 240 or 0). Frivo sets them from its own Settings. `GET /idle`, and `GET /gpu` under `idle`, say what's loaded and when it'll be freed; `/health` lists the `idle` capability. The status page shows whether speech-to-text is loaded.
