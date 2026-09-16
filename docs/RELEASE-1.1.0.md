# Evora 1.1.0

Speaker analysis now operates on timed voice segments instead of assigning a
whole mixed clip to one person. Clean exclusive speech builds voice profiles;
overlapping or ambiguous speech cannot update them. Automatic identity merging
and low-confidence dominant-speaker guesses have been removed.

Added local speaker setup, persistent voice enrollment, word timestamps,
strict speaker filtering, and an experimental rolling audio API. Optional
two-speaker overlap recovery is off by default and its output is never accepted
by the strict speaker filter. The GPU transcription default is quantized
large-v3-turbo; explicit existing environment settings remain respected.

Setup now updates dependencies in an existing environment, installs matching
CUDA PyTorch on NVIDIA systems, and checks imports before marking an update
complete. VERSION supplies the setup executable, native host, service, and
installed-app version. Download caches include the optional separation model.

## After updating

Open speaker settings on the Evora PC, accept Community-1's download conditions,
and provide a Hugging Face read token. Enroll the voices you want remembered.
Transcription remains available before setup, with no guessed speaker labels.

Existing Frivo receives compatible text responses. Multi-speaker display,
streaming, and speaker selection need a later Frivo integration. This release
does not claim near-perfect overlap recognition or measured 2070 Super latency.

## Validation

20 automated checks cover identity isolation, overlap rejection, persistence,
filtering, stream ordering, bounded silence handling, and failed-request rollback.
The Windows dependency set resolves and passes `pip check`. Real CPU smoke tests
passed for the Flask service, audio decoding, Whisper inference, ECAPA embeddings,
and SepFormer output conversion. PowerShell syntax and installer compilation
were checked. Community-1 inference still needs the user's model authorization;
the full pipeline has not been benchmarked on the target GPU or VRChat audio.
