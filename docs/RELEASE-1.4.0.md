# Evora 1.4.0 — joint separation preview and guided setup

This release adds an opt-in PixIT / ToTaToNet path that obtains speaker activity
and separated waveforms from the same pipeline. Standard remains the default.
This is NOT a measured accuracy improvement or a six-speaker crowd solution.
The published model handles up to three speakers in a five-second model window.

## Setup on the Evora PC

Run EvoraSetup.exe and choose Update. Required dependencies refresh automatically.
Open /speaker-settings using the Evora PC's network address from either PC.
Choose Joint voice separation (preview), follow both Hugging Face access links,
and click Prepare voices. A saved token is reused; fine-grained tokens need
access to the additional repositories. Preparation runs a model forward pass
before reporting Ready. No audio is uploaded to Hugging Face.

Stop Frivo listening and test a real conversation recording (up to 60 seconds).
The page reports elapsed time, transcript labels, and playable isolated channels.
Speed is not accuracy: listen for leakage, missed speech, identity swaps and
duplicate people. Test audio and previews are held in memory, not saved to disk.
Previews expire after ten minutes; a new test replaces the old one. The test does
not update enrollment or live sessions. Restart Frivo listening after setup.

## Session behavior

Joint mode uses the listening-session transcription API. It reanalyzes ten seconds
of previous audio with each incoming clip. Local model labels are reconciled using
shared timestamps plus read-only separated-source ECAPA anchors; local label numbers
are never treated as identities. Similar simultaneous channels and ambiguous
assignments are withheld. A temporary Voice label requires at least two observations
and three seconds of detected speech. These are engineering defaults, not calibrated
confidence probabilities. Tracks are bounded, session-local, and explicitly unverified.
They do not train named profiles or pass strict allowed_speakers filters.

The experimental /v1/audio/stream endpoint is not supported in Joint mode; use
/v1/audio/transcriptions with listening_session, as Frivo already does. Names can
be matched against enrolled voices, but separated output remains unverified.
Inference failure preserves an unidentified mixed transcript and reports a notice.
Previously unidentified speech is not retrospectively relabeled.

## Windows dependencies

Transformers 4.57.6 supplies WavLM. An inference-only Asteroid subset is bundled;
users do not need a compiler, training metrics, or a conflicting torchmetrics
version. Its DPRNN weights and numerical behavior match the upstream component.
The SpeechBrain 1.1 adapter converts the legacy pyannote fetch arguments and uses
file copies instead of Windows symlinks. Scoped safe checkpoint metadata loading
remains enabled; arbitrary pickle loading is not enabled.

## Validation and remaining work

Automated checks cover source-label alignment, swapped simultaneous channels,
duplicate sources, short speech, repeated context, returning voices, failed
inference, strict filtering, and isolated recording tests. The bundled DPRNN was
numerically compared with the upstream implementation. A ToTaToNet forward pass
with synthetic weights exercises the architecture but does not validate separation.

The gated pretrained PixIT weights have NOT been evaluated on the user's VRChat
recording or RTX 2070 Super. Model access and running the updated installer on the
remote PC are still required. Do not claim this is the best-performing model,
real-time on that GPU, or a demonstrated fix for crowded-world recognition.
Reference-guided target-speaker extraction is not implemented in this release.
