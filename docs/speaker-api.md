# Evora 1.2.0 speaker API

## Compatibility and setup

`GET /health` includes `version`, `capabilities`, and `speaker_pipeline` status.
`GET /speaker-status` distinguishes `not_configured`, `loading`, `ready`,
`disabled`, and `error`. It never returns the download token.
Configure recognition at `http://localhost:9000/speaker-settings` on the server
computer. Settings and enrollment mutations require its local-only nonce.

## Clip transcription

Create isolated temporary voice memory with `POST /v1/listening-sessions`.
Pass the returned `listening_session` with every clip, including interim
clips. Saved voices seed each session; random voices stay within it. Close
with `DELETE /v1/listening-sessions/ID` after all pending clips complete.
Eight sessions are supported; sessions idle for 24 hours are pruned when a
new one opens. An invalidated session returns 409. Enrollment, forgetting,
configuration, and global voice reset invalidate sessions. Frivo 1.3.0 uses
these endpoints and displays individual speaker turns.

`POST /v1/audio/transcriptions`, multipart:

- `file` (or `audio`): decodable audio, at most 30 seconds / 16 MB upload.
- `language`: optional language code; omitted means detect.
- `speaker`: `0` skips diarization and learning for interim text.
- `listening_session`: optional session token; omitted uses legacy memory.
- `allowed_speakers`: optional comma-separated IDs. An empty value accepts
  nobody. Omitted means return all speech, including unassigned speech.

The legacy `text`, `language`, `duration`, `processing_seconds`, and `speaker`
fields remain. `speaker` is non-null only when all returned words confidently
belong to one speaker. The response also includes `speaker_session`,
`speaker_status`, and `segments`, each with:

```json
{"start": 0.2, "end": 0.6, "text": "Hello", "speaker": {
  "id": 1, "name": "Alice", "confirmed": true, "uncertain": false,
  "similarity": 0.71, "new": false
}, "overlapping": false, "source": "clean", "accepted": true}
```

Times are seconds relative to the uploaded clip. Similarity is a cosine score,
**not a probability of correctness**. Keep names scoped to `speaker_session`;
do not assume an anonymous ID means the same person after a reset or restart.
Strict filtering uses only `accepted` clean speech from confirmed speakers.
Unknown, provisional, boundary-crossing, and separated words cannot pass it.

`source` is `clean`, `unknown`, `overlap`, `separated`, `unavailable`, or
`provisional`. Separation is experimental and can distort words. Overlap that
cannot be recovered retains the mixed transcript with no accepted identity.
Two or three concurrent voices can be recovered when enabled. Unidentified
separated words include a clip-local `track` key so simultaneous streams
remain distinct without assigning a remembered identity. Separation never
trains voice profiles. It is enabled by default for new configurations;
existing settings are preserved.

## Incremental audio (experimental)

`POST /v1/audio/stream` accepts consecutive, non-overlapping encoded chunks of
up to four seconds. Each chunk must be independently decodable (for example
WAV); arbitrary later fragments of one MediaRecorder WebM are not valid files.
This is rolling-window inference with Community-1, not a native streaming
Sortformer implementation. Frivo does not call it yet.

Start with `sequence=0` and no `session_id`. Reuse the returned `session_id`
with the returned `next_sequence`. Include `final=1` with the last audio chunk.
Do not resend cumulative recordings. `language` and `allowed_speakers` behave
as above. Saved enrollment voices seed each stream; anonymous voices are
isolated between streams. Four sessions are supported, idle sessions expire
after two minutes, and closing a session releases its slot on the next call.

`segments` contains newly finalized words; `provisional_segments` replaces
the previous provisional display. Timestamps are absolute from stream start.
There is a 1.2-second stabilization window **in addition to** recording,
network, ASR, and diarization time. No end-to-end latency claim has been
validated on the RTX 2070 Super. Processing is serialized and returns 429
when busy; apply backpressure instead of dropping chunks. Sequence mismatches
return 400. There is no response replay cache: after a lost response, start
a fresh session rather than blindly retrying an already consumed sequence.

## Voice management

`GET /speakers` lists legacy-session voices and saved enrollment voices.
`DELETE /speakers` resets anonymous voices and active streams while retaining
saved voices. `POST /speakers/threshold` retains the existing threshold API;
lowering it can admit wrong speakers. Default match threshold is 0.55 with a
0.10 lead over the next candidate, not a calibrated accuracy guarantee.

Local settings page: `POST /speakers/enroll` takes `name` and `file` and saves
only audio-derived embeddings from detected solo speech.
Enrollment accepts up to 30 seconds; 20–30 seconds of clear speech is
recommended. Several samples are retained, and saved voices are not updated
by live conversation audio.
`DELETE /speakers/ID` forgets one voice. Enrolling or forgetting invalidates open streams so stale
identity selections cannot keep being used.

## Validation before enabling automatic replies

Use labeled recordings from the actual input route: solo speech, quick turns,
similar voices, distance changes, two-person overlap, and three-person overlap.
Compare word error rate, wrong-speaker acceptance, rejected target speech,
speaker fragmentation, and wall-clock processing time separately. Test with
VRChat running if it shares the GPU. The automated tests validate logic with
model doubles; they do not establish recognition accuracy or latency.
