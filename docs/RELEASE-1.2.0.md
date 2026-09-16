# Evora 1.2.0

## 1.2.4 patch

Fix the reproduced Windows import failure for SpeechBrain's optional k2
integration. SpeechBrain 1.1's inspection guard matches POSIX paths only;
Evora now guards Windows inspection calls too, before loading voice models.
Explicit component imports retain normal behavior. No k2 installation is
needed for Evora's speaker recognition.

## 1.2.3 diagnostic patch

Report the setup stage and detailed dependency import failures in speaker
settings and the service log. Credentials and download URLs are redacted.
This identifies installation-specific missing components instead of incorrectly
suggesting a token change for every ImportError. It does not itself repair
an unknown missing dependency.

## 1.2.2 patch

Add a scoped allowlist for official pyannote checkpoint metadata when loading
with PyTorch's weights-only deserializer. This addresses the known
UnpicklingError compatibility failure without disabling weights-only loading.
Model decoding errors now distinguish compatibility or damaged files from
access-token problems. Existing tokens and enrolled voices are retained.

## 1.2.1 patch

Speaker settings, enrollment, and forgetting now work from another PC on
the trusted private network when Evora network access is enabled. Changes
still require the settings page token. Public peer addresses and spoofed
forwarding headers do not grant access. Saved download tokens are never
returned, and the settings page is not cached.

Saved voices can be enrolled from up to 30 seconds of clear solo speech. Enrollment keeps several samples of each voice; live conversations do not modify saved profiles.

Frivo 1.3.0 creates a listening session with its own temporary speaker memory, seeded with saved voices. Random voices are learned from clean speech and keep their IDs across clips until the session ends. Short clear clips accumulate evidence before a temporary voice is confirmed.

Experimental overlap recovery now supports two or three simultaneous voices. Returning voices can be recognized even when the current clip contains only overlap. Unmatched recovered tracks remain separate but unidentified. Recovered speech is never used to learn identities or pass strict speaker filtering.

Enable speaker recognition at http://localhost:9000/speaker-settings and wait for Ready. Model access still requires accepting Community-1's conditions and entering a download token locally. Overlap recovery is on for new configurations; upgrades preserve the existing setting, so enable it if previously disabled. The additional three-speaker model downloads when first needed. Switching between overlap models adds delay.

Overlap recovery is experimental: it cannot guarantee correct words or speaker labels. More than three concurrent voices fall back to unidentified mixed speech. Real-world recognition accuracy and latency on the RTX 2070 Super have not been measured for this release.
