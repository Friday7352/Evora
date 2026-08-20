"""Evora's local OpenAI-compatible transcription service."""

import gc
import os
import sys
import tempfile
import threading
import time

# Keep startup and error output visible in the service log.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def register_cuda_dlls():
    """
    Makes the pip-installed CUDA libraries findable on Windows.

    This exists because of by far the most common way a GPU setup fails
    here: CTranslate2 (the engine under faster-whisper) loads cuBLAS and
    cuDNN as plain DLLs at runtime rather than linking them, so they have to
    be on the DLL search path. Installing them with

        pip install nvidia-cublas-cu12 nvidia-cudnn-cu12

    puts them inside site-packages, which is NOT on that path — producing
    "Library cublas64_12.dll is not found" or "Could not locate
    cudnn_ops64_9.dll" even though the files are plainly right there.

    Rather than making you edit the system PATH, this registers those
    directories at startup. No-op on Linux, and harmless if the packages
    aren't installed.
    """
    if os.name != "nt":
        return

    try:
        import nvidia  # type: ignore
    except ImportError:
        return

    for root in nvidia.__path__:
        for lib in ("cublas", "cudnn", "cuda_runtime"):
            bin_dir = os.path.join(root, lib, "bin")
            if os.path.isdir(bin_dir):
                try:
                    os.add_dll_directory(bin_dir)
                except Exception:
                    pass
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


register_cuda_dlls()

from flask import Flask, jsonify, request

try:
    from faster_whisper import WhisperModel  # type: ignore
except ImportError:
    print("faster-whisper isn't installed for this Python.")
    print(f'Run:  "{sys.executable}" -m pip install faster-whisper flask waitress')
    sys.exit(1)


PORT = int(os.environ.get("WHISPER_PORT", "9000"))


DEVICE_REASON = ""


def pick_device():
    """
    Decides between GPU and CPU.

    Asks CTranslate2, which is the library that actually runs the model and
    is installed as part of faster-whisper. An earlier version of this asked
    torch instead — which quietly forced CPU on every machine that hadn't
    separately installed torch, since faster-whisper doesn't depend on it.
    A GPU sitting idle while the log claimed "running on CPU" is exactly the
    kind of wrong answer that looks like a hardware problem.
    """
    global DEVICE_REASON

    requested = os.environ.get("WHISPER_DEVICE", "auto").lower()
    if requested in {"cuda", "cpu"}:
        DEVICE_REASON = f"WHISPER_DEVICE={requested}"
        return requested

    try:
        import ctranslate2  # type: ignore

        count = ctranslate2.get_cuda_device_count()
        if count > 0:
            DEVICE_REASON = f"CTranslate2 reports {count} CUDA device(s)"
            return "cuda"
        DEVICE_REASON = (
            "CTranslate2 reports no CUDA devices — either no NVIDIA driver is "
            "installed, or the CUDA libraries aren't loadable"
        )
        return "cpu"
    except Exception as e:
        DEVICE_REASON = f"couldn't query CUDA ({e})"
        return "cpu"


DEVICE = pick_device()

# Defaults chosen for accuracy where the hardware allows it.
#
# On a GPU: 'medium' at float16 needs roughly 3GB, which an 8GB card handles
# comfortably, and it is markedly better than 'small' on accents, proper
# nouns and anything with background noise. 'small' at int8 is the sensible
# choice only when something else — an Ollama model, say — needs to share
# the card. If that's your setup, set WHISPER_MODEL=small and
# WHISPER_COMPUTE=int8_float16 to hand the VRAM back.
#
# On CPU: 'small' at int8, because 'medium' on CPU is too slow to be part of
# a conversation.
if DEVICE == "cuda":
    DEFAULT_MODEL, DEFAULT_COMPUTE = "medium", "float16"
else:
    DEFAULT_MODEL, DEFAULT_COMPUTE = "small", "int8"

MODEL_NAME = os.environ.get("WHISPER_MODEL", DEFAULT_MODEL)
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE", DEFAULT_COMPUTE)

# Whisper was trained on 30-second windows and gets noticeably less reliable
# on very short ones. Voice-activity filtering trims silence, which is a big
# speedup on a mostly-quiet stream — but on a two-second clip it can also
# clip the start of the first word. Left on by default, since most clips are
# long enough for it to help; set WHISPER_VAD=0 if short dictations are
# losing their opening words.
USE_VAD = os.environ.get("WHISPER_VAD", "1") not in {"0", "false", "no"}

# ---------------------------------------------------------------------------
# Speaker labelling (optional)
#
# Whisper has no notion of who is speaking. But because the client gates on
# loudness and cuts on silence, each clip that arrives here is normally one
# person — so the useful question isn't "split this recording by speaker",
# it's "is this the same voice as one I heard earlier?".
#
# That's a voice fingerprint: a fixed-length vector per clip, compared
# against the ones seen so far. Same person, high similarity; different
# person, low. No model of who they *are*, just whether two clips match.
#
# Entirely optional. Without resemblyzer installed, transcription works
# exactly as before and no speaker information is returned.
# ---------------------------------------------------------------------------

SPEAKER_ENABLED = False
SPEAKER_BACKEND = ""
SPEAKER_IMPORT_ERROR = ""

# Two possible embedding models, preferring the better one.
#
#   ecapa       - ECAPA-TDNN via SpeechBrain. The current standard for
#                 speaker verification, and markedly better at separating
#                 similar voices: around 1% equal-error-rate on VoxCeleb
#                 against roughly 4-5% for the older approach below.
#   resemblyzer - GE2E, circa 2019. Lighter and fewer dependencies, kept as
#                 a fallback so an existing setup doesn't break.
#
# They produce different embeddings with different similarity scales, so
# each carries its own default threshold — a value tuned for one is
# meaningless for the other.
try:
    import numpy as np  # type: ignore
    import torch  # type: ignore

    try:
        from speechbrain.inference.speaker import EncoderClassifier  # type: ignore
    except ImportError:  # SpeechBrain < 1.0 kept it elsewhere
        from speechbrain.pretrained import EncoderClassifier  # type: ignore

    SPEAKER_ENABLED = True
    SPEAKER_BACKEND = "ecapa"
except Exception as _ecapa_error:
    try:
        import numpy as np  # type: ignore
        from resemblyzer import VoiceEncoder, preprocess_wav  # type: ignore

        SPEAKER_ENABLED = True
        SPEAKER_BACKEND = "resemblyzer"
    except Exception as _speaker_import_error:
        # Deliberately broad, and deliberately keeps the message.
        #
        # "Not installed" is only one of the ways this import fails. These
        # packages pull in torch, librosa and webrtcvad, and any of those
        # can be present but unimportable — webrtcvad is a C extension
        # needing build tools on Windows, and torch fails outright if it has
        # no wheel for the running Python version. Catching bare ImportError
        # and discarding the message turns every one of those into the same
        # unhelpful "install it with pip", which is wrong advice when it IS
        # installed.
        SPEAKER_IMPORT_ERROR = (
            f"{type(_speaker_import_error).__name__}: {_speaker_import_error}"
        )

# Cosine similarity above which two clips are considered the same person.
# Embeddings are L2-normalised, so a dot product is the cosine.
#
# The default depends on the model, because their similarity scales differ:
# ECAPA separates voices much more sharply, so same-speaker pairs sit lower
# than GE2E's do while different-speaker pairs sit far lower still.
# Both are below the textbook figures, which assume clean close-mic studio
# audio — game voice chat is compressed, band-limited and varies with
# distance, and at studio thresholds one person repeatedly fails to match
# themselves. Adjustable at runtime; the right value depends on how alike
# the people you actually talk to sound.
# Measured on real VRChat audio rather than assumed: same-speaker pairs come
# back around 0.45-0.65 and different speakers around 0.33-0.43. That is a
# far narrower band than clean-audio benchmarks suggest, because the codec
# strips out much of what distinguishes voices. 0.40 sits just under the
# genuine matches; higher values leave people unrecognised.
DEFAULT_THRESHOLDS = {"ecapa": 0.40, "resemblyzer": 0.65}
SPEAKER_THRESHOLD = float(
    os.environ.get(
        "WHISPER_SPEAKER_THRESHOLD", DEFAULT_THRESHOLDS.get(SPEAKER_BACKEND, 0.65)
    )
)

# How far ahead of the runner-up the best match must be before it's trusted.
#
# Threshold alone answers "is this close enough to be them?" but not "is it
# clearly them rather than that other person?". When two voices are genuinely
# similar, both clear the threshold and the winner is near-arbitrary — a
# confident, wrong label. Requiring a margin turns those into "unidentified",
# which is the honest answer. Only applies once there are at least two
# known speakers to be confused between.
SPEAKER_MARGIN = float(os.environ.get("WHISPER_SPEAKER_MARGIN", "0.06"))

# Cross-similarity at which two separate profiles are judged to be the same
# person and combined. Set above the match threshold so merging needs
# stronger evidence than a single assignment does — merging two genuinely
# different people is far harder to notice and undo than leaving them split.
SPEAKER_MERGE_THRESHOLD = float(
    os.environ.get("WHISPER_SPEAKER_MERGE", str(SPEAKER_THRESHOLD + 0.10))
)

# How alike the two halves of a clip must sound for it to count as one
# person speaking throughout. Below this, the clip probably contains two
# voices and is not safe to learn a new speaker from.
#
# Deliberately low. Real speech varies within a single clip — different
# words, pitch, emphasis — so one person's halves are nowhere near
# identical, and a strict value here rejects ordinary speech. Clear
# two-voice clips score far lower than genuine variation does (0.1 rather
# than 0.5), so only the obvious cases need catching.
SPEAKER_CONSISTENCY = float(os.environ.get("WHISPER_SPEAKER_CONSISTENCY", "0.35"))

# How far below the match threshold still counts as "partially resembles".
#
# Narrow, and that matters. The gap between a real match and a non-match on
# this audio is only about 0.1, so a wide band swallows the entire range:
# with a 0.15 band nearly every clip "partially resembles" two people and
# gets written off as overlapping. That's what produced a panel full of
# question marks.
SPEAKER_BLEND_BAND = float(os.environ.get("WHISPER_SPEAKER_BLEND_BAND", "0.06"))

# Two separate limits, because matching and enrolling are different jobs
# with different tolerances.
#
# Matching an existing voice is comparatively easy — the profile it's being
# compared against was built from good audio, so even a rough embedding
# from "where are the shrimp?" usually lands on the right person. Setting
# one high limit for both meant every short line came back unlabelled,
# which is how a conversation ends up full of question marks.
#
# Enrolling a NEW voice from a short clip is genuinely dangerous: the
# profile is wrong from the start and every later clip is then compared
# against it. So a clip that matches nobody has to be reasonably long
# before it's allowed to become a new speaker; otherwise it's left
# unlabelled rather than guessed at.
MIN_MATCH_SECONDS = float(os.environ.get("WHISPER_SPEAKER_MIN_SECONDS", "0.7"))
# Lowered from 1.5s: conversation is full of 1-1.5s lines, and refusing to
# enrol from them meant nobody got learned early on — which then left
# nothing for later clips to match against. The overlap checks now guard
# enrolment quality, so length doesn't have to do all the work.
MIN_ENROLL_SECONDS = float(os.environ.get("WHISPER_SPEAKER_ENROLL_SECONDS", "1.0"))

# Recent embeddings per speaker. Keeping a good number matters more than it
# looks: matching considers the closest individual sample as well as the
# average, so a wider spread of stored samples covers more of how one
# person can sound.
SPEAKER_HISTORY = 30

SPEAKERS = []
SPEAKER_LOCK = threading.Lock()
_speaker_encoder = None

# Consecutive embedding failures. Used to give up rather than log the same
# systemic error against every clip.
SPEAKER_FAILURES = 0


print(f"Loading Whisper '{MODEL_NAME}' on {DEVICE} ({COMPUTE_TYPE})…")
print(f"  Device chosen because: {DEVICE_REASON}")
if SPEAKER_ENABLED:
    print(f"  Speaker labelling: on ({SPEAKER_BACKEND}, threshold {SPEAKER_THRESHOLD:.2f})")
    if SPEAKER_BACKEND == "resemblyzer":
        print("    For noticeably better voice separation, install ECAPA-TDNN:")
        print(f'      "{sys.executable}" -m pip install speechbrain')
else:
    print("  Speaker labelling: off")
    print(f"    Reason: {SPEAKER_IMPORT_ERROR or 'resemblyzer not installed'}")
    if "resemblyzer" in SPEAKER_IMPORT_ERROR or not SPEAKER_IMPORT_ERROR:
        print(f'    Install:  "{sys.executable}" -m pip install resemblyzer')
    else:
        # The package is there but something it depends on isn't usable.
        # Naming the failing import is far more actionable than repeating
        # the install command it already ran.
        print("    resemblyzer appears installed but one of its dependencies")
        print("    failed to import. Reproduce it directly with:")
        print(f'      "{sys.executable}" -c "import resemblyzer"')
if DEVICE == "cpu":
    print("  Running on CPU. That works — a strong CPU handles the 'small'")
    print("  model at usable speed — but a GPU is several times faster.")
    print("  If this machine has an NVIDIA card, run 'nvidia-smi'. If that")
    print("  fails, the GPU driver isn't installed (note that NVIDIA's")
    print("  GeForce drivers refuse to install on Windows Server).")

def speaker_encoder_device():
    """
    Which device the voice encoder can actually use.

    Whisper and the encoder run on different stacks: Whisper goes through
    CTranslate2, which has its own CUDA support, while the encoder is
    PyTorch. A machine can therefore have a perfectly working GPU for
    transcription and no GPU for embeddings — which is exactly what happens
    with the default `pip install torch` on Windows, since that ships the
    CPU-only build. Asking that torch for a CUDA device raises "Torch not
    compiled with CUDA enabled" on every single clip.

    The encoder is small enough that CPU is fine — a few tens of
    milliseconds — so falling back is the right call rather than an error.
    """
    if DEVICE != "cuda":
        return "cpu"
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda"
        print("  Note: PyTorch has no CUDA support, so speaker embeddings run on CPU.")
        print("        Whisper itself is unaffected — it uses CTranslate2, not torch.")
        print("        For GPU embeddings, reinstall torch from the CUDA index:")
        print("          pip install --force-reinstall torch "
              "--index-url https://download.pytorch.org/whl/cu121")
    except Exception:
        pass
    return "cpu"


def speaker_encoder():
    """
    Loads the voice encoder on first use. ECAPA is ~80MB and downloads once;
    Resemblyzer's is ~17MB.
    """
    global _speaker_encoder
    if _speaker_encoder is None:
        device = speaker_encoder_device()
        if SPEAKER_BACKEND == "ecapa":
            _speaker_encoder = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "ecapa_model"),
                run_opts={"device": device},
            )
        else:
            _speaker_encoder = VoiceEncoder(device)
    return _speaker_encoder


def embed_voice(wav):
    """
    Turns 16kHz mono audio into an L2-normalised voice fingerprint, using
    whichever backend is available. Normalising here means everything
    downstream can treat a dot product as a cosine regardless of model.
    """
    encoder = speaker_encoder()

    if SPEAKER_BACKEND == "ecapa":
        with torch.no_grad():
            samples = torch.from_numpy(np.asarray(wav, dtype=np.float32)).unsqueeze(0)
            embedding = encoder.encode_batch(samples).squeeze().cpu().numpy()
    else:
        embedding = encoder.embed_utterance(preprocess_wav(wav, source_sr=16000))

    embedding = np.asarray(embedding, dtype=np.float64)
    norm = np.linalg.norm(embedding)
    if norm == 0:
        raise RuntimeError("empty voice embedding")
    return embedding / norm


def score_against(embedding, speaker):
    """
    How much this clip sounds like a known speaker.

    Averages the best few stored samples rather than taking the single
    best. Using the maximum was too trusting: one unlucky sample — a clip
    caught mid-laugh, or with someone else bleeding into it — becomes a
    permanent magnet that pulls other people's audio onto that speaker.
    Averaging the top matches means agreement from several samples is
    needed, while still tolerating a voice that has more than one mode.
    """
    sims = sorted(
        (float(np.dot(embedding, sample)) for sample in speaker["embeddings"]),
        reverse=True,
    )
    top = sims[: min(3, len(sims))]
    sample_score = sum(top) / len(top)
    centroid_score = float(np.dot(embedding, speaker["centroid"]))
    return max(centroid_score, sample_score)


def voice_is_consistent(wav):
    """
    True if one voice appears to run through the whole clip.

    Embeds the first and second half separately and compares them. A single
    speaker sounds like themselves throughout, so the halves agree strongly.
    Two people — someone answering before the other finished, which is most
    of a real conversation — produce halves that disagree.

    This exists because overlapping speech is the main way phantom speakers
    are born. A blended clip resembles neither person, so it clears no
    threshold, and the obvious conclusion ("must be someone new") is exactly
    wrong. The new profile then competes for that person's later clips, and
    from the outside it looks like the app forgot who they were.

    Returns (consistent, similarity) — similarity is None when the clip is
    too short to split meaningfully.
    """
    samples = np.asarray(wav)
    if len(samples) < 16000:  # under a second; halves would be too short
        return True, None

    middle = len(samples) // 2
    try:
        first = embed_voice(samples[:middle])
        second = embed_voice(samples[middle:])
    except Exception:
        return True, None

    similarity = float(np.dot(first, second))
    return similarity >= SPEAKER_CONSISTENCY, similarity


def merge_speakers():
    """
    Collapses profiles that have converged on the same voice.

    Online assignment is greedy and can't undo itself: someone whose first
    clip was unusual gets their own label, and even once later clips reveal
    the two are the same person, both profiles persist forever. Checking
    after each enrolment lets the mistake heal instead of accumulating —
    which is what "it keeps switching speakers for the same person" looks
    like from the outside.

    Returns {old_id: new_id} so the client can relabel what's already on
    screen.
    """
    merged = {}
    changed = True

    while changed and len(SPEAKERS) > 1:
        changed = False
        for i, first in enumerate(SPEAKERS):
            for second in SPEAKERS[i + 1:]:
                # Two independent pieces of evidence, both required.
                #
                # The single closest pair of samples is not enough: one
                # outlier clip in either profile — a laugh, a moment of
                # crosstalk — is sometimes similar to almost anyone, and
                # merging on that alone welds two real people together.
                # That's the worst failure available here, because their
                # words then get attributed to each other and there's no
                # way to unpick it. So require the profiles to agree
                # broadly (centroids) AND at their closest (top pairs).
                pairs = sorted(
                    (
                        float(np.dot(a, b))
                        for a in first["embeddings"]
                        for b in second["embeddings"]
                    ),
                    reverse=True,
                )
                top = pairs[: min(3, len(pairs))]
                cross = sum(top) / len(top)
                centroid_cross = float(np.dot(first["centroid"], second["centroid"]))

                if cross < SPEAKER_MERGE_THRESHOLD or centroid_cross < SPEAKER_THRESHOLD:
                    continue

                # Keep the lower id: it's the one that's been on screen
                # longer and more likely to already carry a name.
                keeper, absorbed = (
                    (first, second) if first["id"] < second["id"] else (second, first)
                )
                keeper["embeddings"].extend(absorbed["embeddings"])
                del keeper["embeddings"][:-SPEAKER_HISTORY]
                centroid = np.mean(keeper["embeddings"], axis=0)
                keeper["centroid"] = centroid / np.linalg.norm(centroid)

                SPEAKERS.remove(absorbed)
                merged[absorbed["id"]] = keeper["id"]
                print(
                    f"  merged speaker {absorbed['id']} into {keeper['id']} "
                    f"(similarity {cross:.2f})"
                )
                changed = True
                break
            if changed:
                break

    return merged


def identify_speaker(wav, duration):
    """
    Returns {"id", "similarity", "new"} for the voice in `wav`, or None when
    labelling is unavailable or the clip is too short to judge.
    """
    global SPEAKER_FAILURES

    if not SPEAKER_ENABLED or duration < MIN_MATCH_SECONDS:
        return None
    if SPEAKER_FAILURES >= 3:
        return None

    try:
        embedding = embed_voice(wav)
    except Exception as e:
        # A failure here is nearly always systemic — a missing CUDA build, a
        # broken dependency — not something specific to this clip. Printing
        # it once per clip buries the actual transcriptions in a wall of
        # identical errors, so give up after a few and say so once.
        SPEAKER_FAILURES += 1
        if SPEAKER_FAILURES <= 3:
            print(f"  speaker embedding failed: {e!r}")
        if SPEAKER_FAILURES == 3:
            print("  Speaker labelling disabled for this session after repeated "
                  "failures. Transcription continues normally.")
        return None

    with SPEAKER_LOCK:
        best, best_similarity = None, -1.0
        runner_up = -1.0
        for speaker in SPEAKERS:
            # Compare against the average voice AND the closest single
            # sample, taking whichever is higher.
            #
            # The centroid alone is what made one person keep splitting into
            # several labels. Someone who is sometimes near and sometimes
            # far, or shouts then mutters, has an average that sits between
            # those modes and resembles none of them especially well — so
            # every clip looked like a stranger. Keeping the individual
            # samples means a quiet clip can match their earlier quiet
            # clips directly, without the loud ones dragging the comparison
            # down.
            similarity = score_against(embedding, speaker)
            if similarity > best_similarity:
                runner_up = best_similarity
                best, best_similarity = speaker, similarity
            elif similarity > runner_up:
                runner_up = similarity

        # Close call between two known voices: say nothing rather than pick
        # one. Being told "unidentified" costs a label; being told the wrong
        # name puts words in someone's mouth.
        if (
            best is not None
            and best_similarity >= SPEAKER_THRESHOLD
            and len(SPEAKERS) > 1
            and runner_up >= 0
            and best_similarity - runner_up < SPEAKER_MARGIN
        ):
            return {
                "id": None,
                "similarity": round(best_similarity, 3),
                "new": False,
                "ambiguous": True,
            }

        if best is not None and best_similarity >= SPEAKER_THRESHOLD:
            # Only well-sized clips are folded into the profile. A short
            # clip is good enough to recognise someone by, but adding its
            # rougher embedding would gradually blur the profile it just
            # matched against.
            merged = {}
            if duration >= MIN_ENROLL_SECONDS:
                best["embeddings"].append(embedding)
                del best["embeddings"][:-SPEAKER_HISTORY]
                centroid = np.mean(best["embeddings"], axis=0)
                best["centroid"] = centroid / np.linalg.norm(centroid)
                # A new sample can reveal that two profiles were the same
                # person all along.
                merged = merge_speakers()
            return {
                "id": merged.get(best["id"], best["id"]),
                "similarity": round(best_similarity, 3),
                "new": False,
                "merged": merged or None,
            }

        # Matched nobody, and too short to safely become a new profile.
        #
        # Returning nothing here was the single biggest source of blank
        # labels: conversation is full of one-second lines, and early on
        # there is nothing for them to match against yet. Attributing to the
        # closest known voice — when there is one, and it isn't a wild guess
        # — is usually right, because the person who just spoke is very
        # often the person still speaking. Nothing is learned from it either
        # way, so a wrong guess costs one label and corrupts nothing.
        if duration < MIN_ENROLL_SECONDS:
            if best is not None and best_similarity >= SPEAKER_THRESHOLD - SPEAKER_BLEND_BAND:
                return {
                    "id": best["id"],
                    "similarity": round(best_similarity, 3),
                    "new": False,
                    "uncertain": True,
                    "short": True,
                }
            return None

        # About to invent a speaker — the one decision worth being slow and
        # careful about. Two independent checks that this is really a new
        # person rather than a mixture of two known ones.
        #
        # First: sitting moderately close to TWO known speakers without
        # matching either. A genuine newcomer resembles nobody; a blend of
        # two people you already know resembles both, partially. This is the
        # signature of simultaneous speech, which the halves test below
        # cannot see — when both people talk throughout, both halves are
        # equally blended and look perfectly consistent.
        # Needs at least two known voices to be a blend OF, and both have to
        # be genuinely close. With one speaker known, or a clip that
        # resembles nothing, this can't apply.
        blend_floor = SPEAKER_THRESHOLD - SPEAKER_BLEND_BAND
        looks_blended = (
            len(SPEAKERS) >= 2
            and best_similarity >= blend_floor
            and runner_up >= blend_floor
        )
        consistency = None

        # Second: does one voice run through the whole clip? Done here
        # rather than for every clip because it costs two extra embeddings,
        # and a clip that already matched someone doesn't need it.
        if not looks_blended:
            consistent, consistency = voice_is_consistent(wav)
            looks_blended = not consistent

        if looks_blended:
            # Blended audio is never learned from — that's what created
            # phantom speakers. But refusing to label it at all was worse:
            # in a busy room most clips catch some crosstalk, so nearly
            # everything came back unattributed, and with nothing being
            # enrolled there was soon nothing left to match against either.
            # A best guess at the dominant voice, marked uncertain, is far
            # more useful than a wall of question marks.
            if best is not None and best_similarity >= blend_floor:
                return {
                    "id": best["id"],
                    "similarity": round(best_similarity, 3),
                    "new": False,
                    "uncertain": True,
                    "overlapping": True,
                    "consistency": round(consistency, 3) if consistency is not None else None,
                }
            return {
                "id": None,
                "similarity": round(best_similarity, 3) if best is not None else None,
                "new": False,
                "ambiguous": True,
                "overlapping": True,
                "consistency": round(consistency, 3) if consistency is not None else None,
            }

        speaker = {
            "id": len(SPEAKERS) + 1,
            "embeddings": [embedding],
            "centroid": embedding,
        }
        SPEAKERS.append(speaker)
        return {
            "id": speaker["id"],
            "similarity": round(best_similarity, 3) if best is not None else None,
            "new": True,
        }


AVAILABLE_MODELS = [
    {"id": "tiny", "vram": "~0.4GB", "note": "Fastest, least accurate. Testing only."},
    {"id": "base", "vram": "~0.6GB", "note": "Still weak. Use small or better for real speech."},
    {"id": "small", "vram": "~1GB", "note": "Fast. Choose this if Ollama shares the card."},
    {"id": "medium", "vram": "~3GB", "note": "Good balance of speed and accuracy."},
    {"id": "large-v3", "vram": "~5GB", "note": "Most accurate. Won't share 8GB with Ollama."},
]

# Guards the model swap. A transcription in flight holds this for its
# duration, so a model change waits for it to finish rather than pulling the
# model out from under a running request.
MODEL_LOCK = threading.Lock()


def load_model(name):
    """
    Swaps in a different Whisper model at runtime.

    Releases the current model before loading the replacement. Loading first
    and swapping after would be safer against a failed load, but it means
    both models are resident at once — which on an 8GB card is how switching
    from medium to large-v3 turns into an out-of-memory error instead of an
    upgrade.
    """
    global model, MODEL_NAME

    with MODEL_LOCK:
        previous = MODEL_NAME
        print(f"Switching model: {previous} -> {name}")

        model = None
        gc.collect()

        started = time.perf_counter()
        try:
            model = WhisperModel(name, device=DEVICE, compute_type=COMPUTE_TYPE)
        except Exception:
            # Put the old one back so the server stays usable.
            print(f"  failed to load '{name}' — restoring '{previous}'")
            model = WhisperModel(previous, device=DEVICE, compute_type=COMPUTE_TYPE)
            raise

        MODEL_NAME = name
        elapsed = time.perf_counter() - started
        print(f"  '{name}' ready in {elapsed:.1f}s")
        return elapsed


_load_start = time.perf_counter()
try:
    model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
except Exception as e:
    message = str(e)
    if "cublas" in message.lower() or "cudnn" in message.lower():
        # Auto mode is meant to be a reliable setup, not an all-or-nothing
        # GPU promise. If the detected GPU lacks a compatible CUDA runtime,
        # retain a working local service on CPU. An explicit cuda request
        # still fails clearly, because the caller deliberately declined that
        # fallback.
        if os.environ.get("WHISPER_DEVICE", "auto").lower() == "auto":
            print("CUDA libraries are unavailable; continuing on CPU instead.")
            DEVICE = "cpu"
            DEVICE_REASON = "CUDA runtime unavailable; automatic CPU fallback"
            COMPUTE_TYPE = "int8"
            model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
        else:
            print()
            print("!" * 70)
            print("The CUDA libraries couldn't be loaded:")
            print(f"  {e}")
            print()
            print("Install them into this Python and they'll be picked up automatically:")
            print(f'  "{sys.executable}" -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12')
            print()
            print("Or run on CPU instead — slower, but needs nothing else:")
            print("  set WHISPER_DEVICE=cpu")
            print("!" * 70)
            sys.exit(1)
    else:
        raise
print(f"  Ready in {time.perf_counter() - _load_start:.1f}s")

app = Flask(__name__)


@app.route("/")
def status_page():
    gpu = DEVICE == "cuda"
    device_label = "GPU (CUDA)" if gpu else "CPU"
    model_label = MODEL_NAME[:1].upper() + MODEL_NAME[1:]
    address = f"http://{request.host}"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="20">
  <title>Evora</title>
  <style>
    :root {{ color-scheme: dark; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; min-height: 100vh; display: grid; place-items: center;
      font-family: "Segoe UI", system-ui, sans-serif; color: #f8f7ff;
      background: radial-gradient(circle at 18% 0%, #44206f 0, transparent 34%),
                  radial-gradient(circle at 90% 100%, #25114a 0, transparent 40%), #090b13;
    }}
    main {{ width: min(760px, calc(100% - 32px)); padding: 38px 0; }}
    .brand {{ display: flex; align-items: center; gap: 16px; margin: 0 8px 28px; }}
    .mark {{
      width: 52px; height: 52px; border-radius: 16px; display: grid; place-items: center;
      font-weight: 800; font-size: 25px; color: white; background: linear-gradient(135deg, #c05cff, #7836e9);
      box-shadow: 0 12px 30px #7c3aed55, inset 0 1px #ffffff55;
    }}
    h1 {{ font-size: 30px; line-height: 1; margin: 0 0 7px; letter-spacing: -0.7px; }}
    .subtitle {{ color: #b8acce; font-size: 14px; }}
    .card {{ background: #151a25; border: 1px solid #242b39; border-radius: 17px; padding: 25px; box-shadow: 0 18px 46px #00000033; }}
    .status {{ display: flex; align-items: center; gap: 10px; font-size: 18px; font-weight: 700; }}
    .dot {{ width: 10px; height: 10px; border-radius: 50%; background: #4ee69a; box-shadow: 0 0 13px #4ee69aaa; }}
    .status-note {{ margin: 7px 0 0 20px; color: #b8acce; font-size: 14px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 22px; }}
    .stat {{ background: #0f131d; border: 1px solid #222a38; border-radius: 12px; padding: 16px; }}
    .label {{ color: #9185aa; font-size: 11px; font-weight: 700; letter-spacing: .7px; }}
    .value {{ margin-top: 7px; font-size: 18px; font-weight: 650; }}
    .gpu {{ color: #d4a7ff; }}
    .connection {{ margin-top: 16px; padding: 17px; border-radius: 12px; background: #201733; border: 1px solid #44305f; }}
    .connection .label {{ color: #cab7e5; }}
    code {{ display: block; margin-top: 7px; color: #fff; font: 600 16px ui-monospace, "Cascadia Code", Consolas, monospace; overflow-wrap: anywhere; }}
    .detail {{ margin: 16px 2px 0; color: #a89bbd; font-size: 13px; line-height: 1.55; overflow-wrap: anywhere; }}
    footer {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 17px 8px 0; color: #8e839d; font-size: 12px; }}
    footer span {{ padding: 5px 9px; border: 1px solid #242b39; border-radius: 7px; background: #11151e; font-family: ui-monospace, monospace; }}
    @media (max-width: 520px) {{ main {{ padding: 24px 0; }} .grid {{ grid-template-columns: 1fr; }} .card {{ padding: 20px; }} }}
  </style>
</head>
<body>
  <main>
    <header class="brand">
      <div class="mark">E</div>
      <div><h1>Evora</h1><div class="subtitle">Local transcription service for Frivo</div></div>
    </header>
    <section class="card">
      <div class="status"><span class="dot"></span>Running</div>
      <p class="status-note">Evora is ready to accept private, local audio transcription.</p>
      <div class="grid">
        <div class="stat"><div class="label">ACTIVE MODEL</div><div class="value">{model_label}</div></div>
        <div class="stat"><div class="label">PROCESSING</div><div class="value {'gpu' if gpu else ''}">{device_label}</div></div>
        <div class="stat"><div class="label">PRECISION</div><div class="value">{COMPUTE_TYPE}</div></div>
        <div class="stat"><div class="label">SPEAKER LABELLING</div><div class="value">{'Available' if SPEAKER_ENABLED else 'Not available'}</div></div>
      </div>
      <div class="connection"><div class="label">USE THIS ADDRESS IN FRIVO</div><code>{address}</code></div>
      <p class="detail">{DEVICE_REASON}</p>
    </section>
    <footer><span>GET /health</span><span>POST /v1/audio/transcriptions</span><span>Refreshes every 20 seconds</span></footer>
  </main>
</body>
</html>"""


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "model": MODEL_NAME,
        "device": DEVICE,
        "compute": COMPUTE_TYPE,
        "available_models": AVAILABLE_MODELS,
        "speaker_labelling": SPEAKER_ENABLED,
        "speaker_backend": SPEAKER_BACKEND,
        "speaker_threshold": SPEAKER_THRESHOLD,
    })


@app.route("/speakers/threshold", methods=["POST"])
def set_speaker_threshold():
    """
    Tunes voice matching without a restart.

    Worth having as a live control because the right value is entirely
    situational — it depends on how alike the specific people you talk to
    sound, and the only way to find it is to watch the labels while a real
    conversation happens.
    """
    global SPEAKER_THRESHOLD

    data = request.get_json(force=True) or {}
    try:
        value = float(data.get("threshold"))
    except (TypeError, ValueError):
        return jsonify({"error": "threshold must be a number"}), 400

    if not 0.3 <= value <= 0.95:
        return jsonify({"error": "threshold must be between 0.3 and 0.95"}), 400

    SPEAKER_THRESHOLD = value
    print(f"Speaker match threshold set to {value:.2f}")
    return jsonify({"ok": True, "threshold": SPEAKER_THRESHOLD})


@app.route("/speakers", methods=["GET", "DELETE"])
def speakers():
    """
    Lists or clears the voices learned this session.

    Clearing matters more than it sounds: the fingerprints are session
    state, so joining a different group of people means the old profiles
    are just noise that new voices might match against.
    """
    if request.method == "DELETE":
        with SPEAKER_LOCK:
            count = len(SPEAKERS)
            SPEAKERS.clear()
        print(f"Cleared {count} speaker profile(s)")
        return jsonify({"ok": True, "cleared": count})

    with SPEAKER_LOCK:
        return jsonify({
            "enabled": SPEAKER_ENABLED,
            "backend": SPEAKER_BACKEND,
            "margin": SPEAKER_MARGIN,
            "error": SPEAKER_IMPORT_ERROR,
            "threshold": SPEAKER_THRESHOLD,
            "speakers": [{"id": s["id"], "clips": len(s["embeddings"])} for s in SPEAKERS],
        })


@app.route("/model", methods=["POST"])
def set_model():
    """
    Switches model without restarting. Makes comparing accuracy practical —
    otherwise every A/B means stopping the service, changing an environment
    variable and starting again, which nobody does more than twice.
    """
    data = request.get_json(force=True) or {}
    name = (data.get("model") or "").strip()

    known = {m["id"] for m in AVAILABLE_MODELS}
    if name not in known:
        return jsonify({"error": f"Unknown model '{name}'. Known: {', '.join(sorted(known))}"}), 400

    if name == MODEL_NAME:
        return jsonify({"ok": True, "model": MODEL_NAME, "message": "Already loaded."})

    try:
        elapsed = load_model(name)
    except Exception as e:
        return jsonify({
            "error": f"Couldn't load '{name}': {e}. Still running '{MODEL_NAME}'."
        }), 500

    return jsonify({
        "ok": True,
        "model": MODEL_NAME,
        "message": f"Loaded '{MODEL_NAME}' in {elapsed:.1f}s.",
    })


@app.route("/v1/audio/transcriptions", methods=["POST"])
def transcribe():
    """
    Deliberately mirrors OpenAI's transcription endpoint — same multipart
    "file" field, same {"text": ...} response — so the main app can treat
    this and OpenAI as interchangeable, and so any other tool that speaks
    the OpenAI API can point at this too.
    """
    audio = request.files.get("file") or request.files.get("audio")
    if not audio:
        return jsonify({"error": "No audio uploaded."}), 400

    # Whisper needs a real file path; the upload only exists in memory.
    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        audio.save(tmp.name)
        tmp.close()

        # Blank means auto-detect, which is one of the main advantages of
        # running Whisper over the browser's recogniser.
        language = (request.form.get("language") or "").strip() or None

        started = time.perf_counter()
        # Held for the duration so a model swap can't replace the model
        # mid-transcription.
        with MODEL_LOCK:
            active = model
        segments, info = active.transcribe(
            tmp.name,
            language=language,
            beam_size=5,
            # Each clip here is an independent utterance, not a continuation
            # of the last one. Left on (the default), Whisper feeds the
            # previous transcript in as context and will happily invent text
            # that follows on from it — so a short or unclear clip comes back
            # as a fluent sentence that was never said. That is the classic
            # Whisper hallucination, and on a stream of short dictations it
            # is the single biggest source of confidently wrong output.
            condition_on_previous_text=False,
            # If greedy decoding produces a low-confidence or repetitive
            # result, retry with progressively more randomness rather than
            # returning the first bad answer.
            temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
            # Discard segments the model itself considers probably silence,
            # instead of transcribing room tone into words.
            no_speech_threshold=0.6,
            # Drops silence before it reaches the model. On a stream that's
            # mostly quiet — which is what listening to a conversation looks
            # like — this is a large speedup.
            vad_filter=USE_VAD,
            vad_parameters={"min_silence_duration_ms": 500} if USE_VAD else None,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        elapsed = time.perf_counter() - started

        # Only fingerprint clips that actually contained speech — running it
        # on silence just invents a speaker. Callers can also opt out, which
        # the listening panel does for its live in-progress passes: a
        # half-finished sentence produces a weaker fingerprint, and the
        # final pass over the complete utterance is going to identify the
        # speaker properly a moment later anyway.
        want_speaker = request.form.get("speaker", "1") not in {"0", "false", "no"}
        speaker = None
        if text and SPEAKER_ENABLED and want_speaker:
            try:
                # faster-whisper's own decoder gives 16kHz mono float32 via
                # PyAV, which is exactly what the encoder wants — and avoids
                # needing ffmpeg or librosa on the machine just to read the
                # WebM/Opus the browser sends.
                from faster_whisper.audio import decode_audio  # type: ignore

                wav = decode_audio(tmp.name, sampling_rate=16000)
                speaker = identify_speaker(wav, info.duration)
            except Exception as e:
                print(f"  speaker step skipped: {e!r}")

        # Says explicitly whether the language was dictated by the caller or
        # guessed here. A wrong guess is one of the main ways transcription
        # comes back as confident nonsense, and "forced" vs "detected" is
        # the difference between that being the client's fault or this
        # server's.
        how = "forced by client" if language else f"detected {info.language_probability:.0%}"
        who = ""
        if speaker and speaker.get("overlapping") and speaker.get("id"):
            who = (f" probably speaker {speaker['id']} ({speaker['similarity']}) — "
                   "overlapping voices, not learned")
        elif speaker and speaker.get("overlapping"):
            detail = (f"halves match {speaker['consistency']}"
                      if speaker.get("consistency") is not None
                      else f"resembles two speakers, best {speaker['similarity']}")
            who = f" two voices in one clip ({detail}) — not learned"
        elif speaker and speaker.get("short"):
            who = f" probably speaker {speaker['id']} ({speaker['similarity']}) — short clip"
        elif speaker and speaker.get("ambiguous"):
            sim = speaker.get("similarity")
            who = f" speaker ambiguous ({sim}, too close to call)" if sim is not None else " speaker ambiguous"
        elif speaker and speaker.get("id"):
            who = f" speaker {speaker['id']}"
            who += " (new)" if speaker["new"] else f" ({speaker['similarity']:.2f})"
        print(
            f"  transcribed {info.duration:.1f}s of audio in {elapsed:.2f}s "
            f"[{info.language} — {how}]{who}"
        )

        return jsonify({
            "text": text,
            "language": info.language,
            "duration": round(info.duration, 2),
            "processing_seconds": round(elapsed, 2),
            "speaker": speaker,
        })
    except Exception as e:
        print(f"  transcription failed: {e!r}")
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


def local_ips():
    import socket

    ips = {"127.0.0.1"}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    return sorted(ips)


if __name__ == "__main__":
    print()
    print("Point Voice Console's Transcription address at one of these:")
    for ip in local_ips():
        print(f"  http://{ip}:{PORT}")
    print()

    # waitress rather than Flask's dev server: this receives a file upload
    # every few seconds once the listening feature is running, and the dev
    # server handles sustained concurrent traffic poorly.
    try:
        from waitress import serve

        serve(app, host="0.0.0.0", port=PORT, threads=8)
    except ImportError:
        print("waitress isn't installed — falling back to the Flask dev server.")
        print(f'For better throughput:  "{sys.executable}" -m pip install waitress')
        app.run(host="0.0.0.0", port=PORT, threaded=True)
