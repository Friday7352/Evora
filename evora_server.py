"""Evora's local OpenAI-compatible transcription service."""

import gc
import os
import socket
import sys
import tempfile
import threading
import time

os.environ.setdefault("MPLCONFIGDIR", os.path.join(os.path.dirname(__file__), "model_cache", "matplotlib"))
os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")

# Keep startup and error output visible in the service log.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


CUDA_DLL_HANDLES = []
CUDA_DLL_DIRECTORIES = []


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
                    # Keep the handle alive for the lifetime of the process.
                    # Releasing it removes the directory from Windows' DLL
                    # search path again.
                    CUDA_DLL_HANDLES.append(os.add_dll_directory(bin_dir))
                except Exception:
                    pass
                CUDA_DLL_DIRECTORIES.append(bin_dir)
                os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")


register_cuda_dlls()

from flask import Flask, jsonify, request, send_file
from pathlib import Path
from evora_speakers import LocalModels, SpeakerRegistry
from evora_api import register_routes
import evora_gpu   # NEW (1.5.0): graphics-card memory for Frivo's GPU graph
from evora_idle import IdleKeeper   # NEW (1.5.0): frees the graphics card when idle
from faster_whisper.audio import decode_audio

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
# On GPU, quantized large-v3-turbo leaves more room for speaker analysis.
# Actual speed and available VRAM depend on other applications sharing it.
#
# On CPU: 'small' at int8, because 'medium' on CPU is too slow to be part of
# a conversation.
if DEVICE == "cuda":
    DEFAULT_MODEL, DEFAULT_COMPUTE = "large-v3-turbo", "int8_float16"
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
# Community-1 locates individual and overlapping voices. ECAPA fingerprints
# only the exclusive speech intervals, never the entire mixed clip.
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
    # Torch's first dynamo import inspects loaded modules. Initialize it before
    # SpeechBrain installs lazy optional modules (notably unavailable Windows k2).
    import torch._dynamo  # type: ignore
    from evora_speakers import prepare_speechbrain_imports
    prepare_speechbrain_imports()

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
        SPEAKER_IMPORT_ERROR = (
            f"ECAPA: {type(_ecapa_error).__name__}: {_ecapa_error}; "
            f"fallback: {type(_speaker_import_error).__name__}: {_speaker_import_error}"
        )

# Conservative defaults; similarities are not calibrated probabilities.
SPEAKER_THRESHOLD = float(os.environ.get("WHISPER_SPEAKER_THRESHOLD", "0.55"))
SPEAKER_MARGIN = float(os.environ.get("WHISPER_SPEAKER_MARGIN", "0.10"))
_speaker_encoder = None


print(f"Loading Whisper '{MODEL_NAME}' on {DEVICE} ({COMPUTE_TYPE})…")
print(f"  Device chosen because: {DEVICE_REASON}")
if SPEAKER_ENABLED:
    print(f"  Voice encoder: {SPEAKER_BACKEND}; speaker pipeline status is available in settings")
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
            from speechbrain.utils.fetching import LocalStrategy
            _speaker_encoder = EncoderClassifier.from_hparams(
                source="speechbrain/spkrec-ecapa-voxceleb",
                savedir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "ecapa_model"),
                run_opts={"device": device},
                local_strategy=LocalStrategy.COPY,
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
    if "idle" in globals():
        idle.touch("listen")   # NEW (1.5.0): enrolling a voice counts as use
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


AVAILABLE_MODELS = [
    {"id": "large-v3-turbo", "vram": "Hardware dependent", "note": "GPU default: fast transcription with speaker analysis."},
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

# The GPU model handles one clip at a time. Reject overlap immediately rather
# than letting requests occupy every Waitress worker while they wait; health
# and the status page must remain responsive even when a client sends live
# partials faster than the model can process them.
TRANSCRIBE_LOCK = threading.Lock()


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
ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
speaker_models = LocalModels(ROOT, speaker_encoder_device(), warmup=speaker_encoder)
speaker_registry = SpeakerRegistry(embed_voice, ROOT / "known_speakers.json",
                                   threshold=SPEAKER_THRESHOLD, margin=SPEAKER_MARGIN)


def transcribe_words(audio, language=None):
    with MODEL_LOCK:
        ensure_whisper()   # NEW (1.5.0): back from an idle unload
        idle.touch("speech")
        segments, info = model.transcribe(
            audio, language=language, beam_size=3, condition_on_previous_text=False,
            temperature=0.0, no_speech_threshold=0.6, word_timestamps=True,
            vad_filter=USE_VAD, vad_parameters={"min_silence_duration_ms": 300} if USE_VAD else None,
        )
        words = [{"start": word.start, "end": word.end, "text": word.word}
                 for segment in segments for word in (segment.words or [])]
    return words, info.language


# ---------------------------------------------------------------------------
# NEW (1.5.0): freeing the graphics card when idle (see evora_idle.py)
# ---------------------------------------------------------------------------

def ensure_whisper():
    """Loads Whisper again if it was freed while idle. Call with MODEL_LOCK held."""
    global model
    if model is None:
        started = time.perf_counter()
        print(f"Idle: loading Whisper '{MODEL_NAME}' again…", flush=True)
        model = WhisperModel(MODEL_NAME, device=DEVICE, compute_type=COMPUTE_TYPE)
        print(f"  ready in {time.perf_counter() - started:.1f}s", flush=True)


def unload_whisper():
    global model
    # Waits for a clip in progress; never pulls the model out from under it.
    with TRANSCRIBE_LOCK, MODEL_LOCK:
        model = None
        gc.collect()


def unload_speaker_encoder():
    global _speaker_encoder
    _speaker_encoder = None


def unload_listening():
    with TRANSCRIBE_LOCK:
        speaker_models.sleep()


speaker_models.on_sleep = unload_speaker_encoder
idle = IdleKeeper(ROOT, unload_listen=unload_listening, unload_speech=unload_whisper,
                  is_listen_loaded=lambda: speaker_models.is_loaded() or _speaker_encoder is not None,
                  is_speech_loaded=lambda: model is not None)
speaker_models.on_use = lambda: idle.touch("listen")
idle.start()

register_routes(app, speaker_models, speaker_registry, transcribe_words, decode_audio, TRANSCRIBE_LOCK,
                idle=idle)   # CHANGED (1.5.0): listening sessions tell the idle timer
speaker_models.prepare()



@app.route("/evora-icon.png")
def evora_icon():
    return send_file(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "Evora.png"),
        mimetype="image/png",
        max_age=86400,
    )


def get_lan_address():
    """Return this PC's preferred private-network IPv4 address, if known."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        address = probe.getsockname()[0]
        return address if not address.startswith("127.") else None
    except OSError:
        try:
            addresses = socket.gethostbyname_ex(socket.gethostname())[2]
            return next((item for item in addresses if not item.startswith("127.")), None)
        except OSError:
            return None
    finally:
        probe.close()


@app.route("/")
def status_page():
    gpu = DEVICE == "cuda"
    device_label = "GPU (CUDA)" if gpu else "CPU"
    model_label = MODEL_NAME[:1].upper() + MODEL_NAME[1:]
    local_address = "http://evora.local:9000"
    lan_ip = get_lan_address()
    lan_address = f"http://{lan_ip}:9000" if lan_ip else "Not available"
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
    .mark {{ width: 52px; height: 52px; border-radius: 16px; overflow: hidden; box-shadow: 0 12px 30px #7c3aed55; }}
    .mark img {{ display: block; width: 100%; height: 100%; object-fit: cover; }}
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
    .connections {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin-top: 16px; }}
    .connection {{ padding: 17px; border-radius: 12px; background: #201733; border: 1px solid #44305f; }}
    .connection .label {{ color: #cab7e5; }}
    code {{ display: block; margin-top: 7px; color: #fff; font: 600 16px ui-monospace, "Cascadia Code", Consolas, monospace; overflow-wrap: anywhere; }}
    .detail {{ margin: 16px 2px 0; color: #a89bbd; font-size: 13px; line-height: 1.55; overflow-wrap: anywhere; }}
    footer {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 17px 8px 0; color: #8e839d; font-size: 12px; }}
    footer span {{ padding: 5px 9px; border: 1px solid #242b39; border-radius: 7px; background: #11151e; font-family: ui-monospace, monospace; }}
    @media (max-width: 620px) {{ main {{ padding: 24px 0; }} .grid, .connections {{ grid-template-columns: 1fr; }} .card {{ padding: 20px; }} }}
  </style>
</head>
<body>
  <main>
    <header class="brand">
      <div class="mark"><img src="/evora-icon.png" alt="Evora"></div>
      <div><h1>Evora</h1><div class="subtitle">Local transcription service for Frivo</div></div>
    </header>
    <section class="card">
      <div class="status"><span class="dot"></span>Running</div>
      <p class="status-note">Evora is ready to accept private, local audio transcription.</p>
      <div class="grid">
        <p><a href="/speaker-settings">Speaker recognition setup</a></p>
        <div class="stat"><div class="label">ACTIVE MODEL</div><div class="value">{model_label}</div></div>
        <div class="stat"><div class="label">PROCESSING</div><div class="value {'gpu' if gpu else ''}">{device_label}</div></div>
        <div class="stat"><div class="label">PRECISION</div><div class="value">{COMPUTE_TYPE}</div></div>
        <div class="stat"><div class="label">SPEAKER LABELLING</div><div class="value">{speaker_models.state.replace('_', ' ')}</div></div>
        <div class="stat"><div class="label">GRAPHICS CARD</div><div class="value">{"Speech-to-text loaded" if model is not None else "Freed while idle"}</div></div>
      </div>
      <div class="connections">
        <div class="connection"><div class="label">ON THIS PC</div><code>{local_address}</code></div>
        <div class="connection"><div class="label">OTHER DEVICES ON YOUR NETWORK</div><code>{lan_address}</code></div>
      </div>
      <p class="detail">The network address requires private-network access to be enabled in Evora settings.</p>
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
        "version": VERSION,
        "speaker_labelling": speaker_models.state == "ready",
        "speaker_pipeline": speaker_models.status(),
        "capabilities": ["speaker_segments", "speaker_enrollment", "speaker_filter", "rolling_stream",
                         "gpu", "idle"],   # CHANGED (1.5.0): GET /gpu, GET/POST /idle
        "speaker_backend": speaker_models.status()["model"] + " + " + SPEAKER_BACKEND,
        "speaker_threshold": speaker_registry.threshold,
    })


# NEW (1.5.0): how much of the graphics card's memory is in use, and by
# whom (Ollama, Evora, everything else). Frivo graphs it. Cheap: cached for
# a second and read from the driver and Windows, not from the models.
@app.route("/gpu")
def gpu_memory():
    data = dict(evora_gpu.snapshot(DEVICE))
    data["idle"] = idle.status()   # NEW (1.5.0): the idle countdowns, for Frivo's graph
    return jsonify(data)


# NEW (1.5.0): the idle timers. GET says what's loaded and when it'll be
# freed; POST {"listen_minutes": 5|15|60|0, "speech_minutes": 15|60|240|0}
# sets them (0 = never). Frivo sets these from its own Settings.
@app.route("/idle", methods=["GET", "POST"])
def idle_timers():
    if request.method == "POST":
        try:
            return jsonify(idle.configure(request.get_json(force=True) or {}))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
    return jsonify(idle.status())


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
