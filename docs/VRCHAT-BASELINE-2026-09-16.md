# Supplied VRChat recording: baseline results

## Follow-up: experimental overlap track memory

The proposed 1.3.1 track memory was exercised locally using the cached
three-output WSJ03Mix separator and real ECAPA embeddings. The first 40 seconds
were separated in five eight-second blocks and assessed in four-second portions;
the 0.34-second tail was excluded. No candidate passed within-sample consistency,
so no unverified public track was created. The local CPU run took 29.91 seconds
excluding model loading and transcription. The shorter 28–36-second comparison
with two- and three-output separation also produced zero candidates.

This is a negative recognition result, not an accuracy success. Accordingly,
overlap track learning ships disabled by default. Its synthetic tests establish
bookkeeping and rejection behavior only. Per-source language handling and
clear uncertainty display can be used without enabling this experiment.

## Original baseline

Input: user-provided 40.371-second MKV; one stereo AAC stream at 48 kHz.
Decoded audio duration: 40.341 seconds. Original preserved. Audio and private
transcript artifacts are under ignored dist/audio-evaluation-20260916/.

Remote baseline: Evora 1.2.4, RTX 2070 Super/CUDA, large-v3-turbo/int8_float16,
speaker recognition ready and overlap enabled. Both trials used new isolated
listening sessions, closed afterward. No existing session was reset and no
model settings were changed.

| Approximate clip size | Summed request wall time | Public temporary IDs | Confirmed IDs | Returned word segments |
| --- | ---: | ---: | ---: | ---: |
| 20 seconds (2 requests) | 4.47 s | 3 | 0 | 49 |
| 10 seconds (4 requests) | 6.29 s | 4 | 0 | 48 |

The same recording produces different speaker inventories with different clip
boundaries. Neither run confirms an identity. These findings demonstrate
boundary sensitivity, not a measured identity error rate: no speaker-attributed
ground truth is provided. Do not equate IDs with the actual number of people.
The request times exclude recording delay, translation, and UI delivery.

Left/right sample correlation is approximately 0.747. This establishes that
the channels differ, not that each contains a distinct person. Evora currently
downmixes them, discarding possible spatial evidence.

Separately, the existing two-output WHAMR and three-output WSJ03Mix separators
were run locally on the 28–36-second excerpt. CPU inference took 5.65 and
5.51 seconds respectively in the measured runs, excluding model loading and
ASR. These are not 2070 Super performance measurements. Exported tracks and
the original excerpt can be compared in review.html. Playback peak protection
does not denoise or improve separation. Auditory or reference-based assessment
is still required; waveform decorrelation alone is not separation accuracy.

No six-speaker model was evaluated, no crowd-recognition accuracy claim is
supported, and these results do not validate the local 1.3.0 stricter policy
or the proposed continuous redesign. The next step is model evaluation with
variable-capacity separation and persistent track assignment, including
ground-truth coverage as well as false-label rate.
