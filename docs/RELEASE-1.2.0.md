# Evora 1.2.0

Saved voices can be enrolled from up to 30 seconds of clear solo speech. Enrollment keeps several samples of each voice; live conversations do not modify saved profiles.

Frivo 1.3.0 creates a listening session with its own temporary speaker memory, seeded with saved voices. Random voices are learned from clean speech and keep their IDs across clips until the session ends. Short clear clips accumulate evidence before a temporary voice is confirmed.

Experimental overlap recovery now supports two or three simultaneous voices. Returning voices can be recognized even when the current clip contains only overlap. Unmatched recovered tracks remain separate but unidentified. Recovered speech is never used to learn identities or pass strict speaker filtering.

Enable speaker recognition at http://localhost:9000/speaker-settings and wait for Ready. Model access still requires accepting Community-1's conditions and entering a download token locally. Overlap recovery is on for new configurations; upgrades preserve the existing setting, so enable it if previously disabled. The additional three-speaker model downloads when first needed. Switching between overlap models adds delay.

Overlap recovery is experimental: it cannot guarantee correct words or speaker labels. More than three concurrent voices fall back to unidentified mixed speech. Real-world recognition accuracy and latency on the RTX 2070 Super have not been measured for this release.
