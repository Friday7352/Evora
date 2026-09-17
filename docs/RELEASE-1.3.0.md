# Evora 1.3.0

## 1.3.1: overlap continuity experiment and language correction

Recovered sources now detect their own language rather than inheriting the
mixed transcript's detected language. Frivo 1.3.3 translates each source
using that language and preserves per-track metadata.

An optional Follow repeated separated voices setting adds session-local,
unverified track memory. It is OFF by default. It requires consistent voice
embeddings across the halves of each sample, rejects similar simultaneous
outputs, and matches sources jointly so one track cannot claim two simultaneous
sources. Track labels require at least three distinct clips and six seconds of
eligible separated audio. The same clip cannot repeatedly reinforce a track.
Ambiguous matches are withheld. Pending candidates expire after 20 analysis
calls; memory is bounded at 64 tracks. Anchors do not drift.

Tracks are separate from SpeakerRegistry: they never enroll, persist to disk,
pass selected-speaker filters, or count as confirmed people. Frivo displays
Voice track N with an overlap/unverified warning and a separate track count.
The experimental rolling endpoint does not train these tracks because its
windows overlap; normal listening sessions can. Saved voice behavior is unchanged.

Validation: synthetic tests cover swapped outputs, repeated evidence, duplicate
sources, session isolation, language separation, and filtering boundaries. A
local replay of the supplied 40-second VRChat clip using three-output separation
in eight-second blocks produced no candidates passing the new consistency
checks. That is rejection behavior, NOT demonstrated speaker recognition.
Overlap tracking is therefore optional and is not advertised as a crowded-world
solution. No six-speaker model or 2070 Super latency for this experiment has
been validated. Production separation capacity remains two/three outputs.

Random speakers remain unidentified while evidence is collected privately.
A public speaker ID now requires at least three consistent clean samples and
nine seconds of solo speech. Each eligible clip needs at least two seconds
of uninterrupted solo speech and three transcribed words. Samples must match
each other; one-word clips and recovered overlap cannot create profiles.

Confirmed session profiles are frozen to prevent feedback from mistaken
matches changing their identity. Moderately similar but inconclusive speech
stays unidentified instead of creating another profile. Matching applies
minimum cosine thresholds of 0.72 for clean speech and 0.82 for separated
speech, with runner-up margins of 0.15 and 0.20 respectively. The existing
threshold control may make this stricter, but cannot lower these floors.
These scores are not probabilities and have not been calibrated against
recordings from the user's environment.

Long samples are checked for consistency between their halves to reject
some missed speaker changes. Only the longest eligible solo stretch in each
clip contributes learning evidence. Pending candidates are bounded at 32 and
never returned as public speaker profiles. No retrospective relabeling is
performed; earlier unidentified speech remains unidentified.

Overlap still uses two- or three-source separation, followed by independent
transcription and conservative matching. It cannot guarantee persistent
identities for strangers who are never heard clearly alone, or isolate an
arbitrary number of simultaneous conversations. Expect fewer labels in noisy
rooms. Validate identity stability and transcription on representative audio.

Restart listening after updating to clear previous session profiles. Enrolled
voices are retained. Frivo 1.3.2 displays unidentified solo speech explicitly.
