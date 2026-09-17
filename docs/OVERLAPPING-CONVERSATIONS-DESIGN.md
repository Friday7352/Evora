# Continuous overlapping-conversation recognition

Status: proposed redesign; not implemented or accuracy-validated. Existing
1.3.0 changes remain in the working tree and are not a solution to this target.

## Requirement

Use simultaneous speech as the normal input. Maintain session identities for
unknown people without requiring solo enrollment; preserve named enrollment
across sessions. Speakers can leave, return, interrupt, and participate in
different conversations. Do not create a permanent identity from one word,
a separator output index, or one uncertain embedding.

## Input boundary

Confirmed input: Frivo sends complete mixed VRChat playback to remote Evora.
There are no independent participant tracks. Normal conditions range from
one-on-one conversations to two/three speakers, with four to six simultaneous
speakers and sometimes more in busy worlds. Target hardware is an RTX 2070
Super with 8 GB VRAM; low delay is preferred. Four/six-speaker support is a
requirement to evaluate, not a demonstrated capability.

Preserve original stereo or multichannel
audio when present, alongside a mono recognition copy. Stereo cues are evidence,
not person IDs: movement and game attenuation can change them. Independent
participant tracks should bypass blind separation when available. The current
decoder downmixes to mono and cannot exploit these cues.

Evaluate 1, 2, 3, 4, 6, and greater-than-6 simultaneous voices separately.
The current two/three-output separators cannot support an arbitrary number of
concurrent voices. More tracking objects do not increase separation capacity.
Do not recursively apply a two-speaker checkpoint to arbitrary mixtures and
claim six-speaker support: recursive extraction requires a model trained for
one-target-versus-remainder behavior and a validated stopping rule.

## Model evaluation decision

Evaluate variable-output separation with persistent speaker inventory, or a
model explicitly trained for the required concurrent-speaker capacity. Ordinary
two/three-speaker checkpoints remain baselines, not the crowded-world solution.
An inventory can be built from the mixture itself in research systems; this
does not establish reliable recognition on VRChat or real-time execution on
the 2070 Super. Verify available weights, license, Windows/runtime support,
memory use, and identity-aware accuracy before integrating a candidate.

Required representative input: a short ordinary conversation and a 30–60 second
busy-world recording captured through Frivo's actual input route. These samples
support qualitative diagnosis; quantitative speaker-attribution evaluation
also needs speaker annotations or known isolated sources before mixing.

## Processing path

1. Continuous capture and timestamped transport. Replace gated utterance
   recording for this mode with contiguous audio frames. Explicit sequence
   numbers, session offsets, backpressure, and gap reporting prevent overlapping
   windows or retries from counting the same evidence twice. Keep silence in
   the timeline. Existing MediaRecorder restarts are not a guaranteed gapless
   transport; later WebM fragments are not independent encoded files.
2. Rolling separation. Evaluate a continuous-speech separation model or a
   properly stitched windowed separator. Carry shared context across windows.
   Preserve output signals and align their permutations across window overlap;
   channel 1 must never automatically mean speaker 1. Do not gate separation
   solely on an upstream diarizer detecting exactly two or three people.
3. Quality measurements. Check residual mixture energy, duplicate-source
   leakage, silence, clipping, repeated words on different streams, voice
   consistency within a source, and agreement across adjacent windows. A
   voice embedding alone does not establish successful separation. These
   measurements need thresholds calibrated on held-out mixtures.
4. Joint track assignment. Match all simultaneous sources to active tracks in
   one assignment step, with explicit unassigned choices and a margin between
   competing assignments. Two simultaneous distinct sources cannot claim the
   same person. Combine voice similarity with continuity and available spatial
   evidence. Retain competing hypotheses instead of irreversible merges.
5. Candidate memory. Accumulate quality-weighted evidence from separated
   speech across distinct time intervals. Solo evidence is helpful but not
   mandatory. Keep candidates separate from public identities until repeated
   independent agreement supports promotion. Contradictions reduce confidence
   or split a candidate; they must not contaminate a confirmed reference.
6. Session identity memory. Keep a stable anchor and a separate tentative
   adaptation buffer. Use the anchor to recognize returning people, and require
   sustained agreement before incorporating new channel conditions. Enrollment
   seeds named anchors. A departure does not free an ID for someone else.
7. Per-track transcription and translation. Recognize separated audio with its
   own language/context. Publish revision IDs and time ranges, then reconcile
   provisional rows when tracking becomes stable. Do not force the language of
   the mixed transcript onto every source. Keep speaker identity independent
   of conversation-topic grouping.

## Device budget and failure behavior

Measure separation, embeddings, ASR, and translation together on the remote
RTX 2070 Super (8 GB), with normal concurrent workloads. Record median/p95
end-to-end delay, peak VRAM, and processing-time/audio-time ratio. Bound queues;
report overload rather than silently dropping voices or accumulating delay.
Do not promise low latency before measurements. Model selection remains open.

When capacity or evidence is insufficient, keep unidentified tracks and mark
uncertain attribution. Strict selected-speaker filtering must exclude uncertain
tracks even if a provisional display label exists.

## Validation before another accuracy claim

Use labeled isolated source recordings to construct realistic two-, three-,
and higher-speaker mixtures, and separately test representative real playback.
Include continuous overlap with no solo introduction, similar voices,
speaker arrival/departure/return, changing distance, different languages,
music/noise, abrupt interruptions, and separator output permutations.

Measure wrong-speaker attribution, identity switches, fragmentation per person,
duplicate tracks, time to stable identity, unassigned coverage, and word error
rate separately. Reporting low wrong-label rate while leaving everything
unassigned is not success. Count evidence in unique audio seconds. Reserve
held-out speakers and mixtures for final evaluation. Compare against current
Evora on exactly the same recordings; synthetic unit tests only validate logic.

## Research references

- Continuous Speech Separation: Dataset and Analysis:
  https://arxiv.org/abs/2001.11482
- Microsoft's NOTSOFAR baseline (separation, ASR, diarization, single- and
  multichannel evaluation; Linux-oriented, not a drop-in Windows dependency):
  https://github.com/microsoft/NOTSOFAR1-Challenge
- Current three-output separator:
  https://huggingface.co/speechbrain/sepformer-wsj03mix
- Fixed-channel separation limitations and selective attention research:
  https://www.microsoft.com/en-us/research/publication/continuous-speech-separation-with-recurrent-selective-attention-network/
- Speaker inventory formed from mixed recordings:
  https://arxiv.org/abs/2012.09727
- Variable-count recursive separation and its training requirement:
  https://www.isca-archive.org/interspeech_2019/takahashi19_interspeech.pdf
