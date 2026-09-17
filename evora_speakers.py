"""Local, overlap-aware speaker analysis. Model imports are lazy for fast startup."""

import json
import copy
import os
import threading
import time
import uuid
import pickle
import re
import traceback
import sys
from pathlib import Path
from evora_joint import JOINT_MODEL, JointConversation, analyze_joint, load_joint_pipeline, unpack_joint_result

import numpy as np

RATE = 16000
MODEL = "pyannote/speaker-diarization-community-1"


def prepare_speechbrain_imports():
    """Work around SpeechBrain 1.1's POSIX-only inspect.py path guard."""
    import torch._dynamo  # initialize before SpeechBrain registers lazy modules
    from speechbrain.utils.importutils import LazyModule
    original = LazyModule.__getattr__
    if getattr(original, "_evora_inspect_guard", False):
        return

    def guarded_getattr(module, name):
        # Only introspection is ignored. Explicit access still loads the real
        # component and reports missing dependencies normally.
        caller = sys._getframe(1).f_code.co_filename.replace("\\", "/")
        if caller.rsplit("/", 1)[-1] == "inspect.py":
            raise AttributeError(name)
        return original(module, name)

    guarded_getattr._evora_inspect_guard = True
    LazyModule.__getattr__ = guarded_getattr


def model_error_details(exc, token=""):
    """Retain actionable dependency errors without publishing credentials."""
    details = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    if token:
        details = details.replace(token, "[redacted]")
    details = re.sub(r"hf_[A-Za-z0-9_-]+", "[redacted]", details)
    details = re.sub(r"https?://\S+", "[download address]", details)
    details = re.sub(r"(?i)(bearer\s+|(?:token|authorization|password)\s*[=:]\s*)[^\s,;]+", r"\1[redacted]", details)
    return details[:1600]


def load_speaker_pipeline(cache_dir, token):
    """Allow only the metadata types used by official pyannote checkpoints."""
    import torch
    prepare_speechbrain_imports()
    from pyannote.audio import Pipeline
    from pyannote.audio.core.task import Specifications, Problem, Resolution
    from torch.torch_version import TorchVersion
    # PyTorch 2.6+ defaults to weights-only loading. pyannote 4.0.1 checkpoints
    # also carry these metadata values. Keep weights-only protection enabled;
    # never globally disable it or allow arbitrary types named by a checkpoint.
    with torch.serialization.safe_globals([TorchVersion, Specifications, Problem, Resolution]):
        return Pipeline.from_pretrained(MODEL, token=token, cache_dir=str(cache_dir))


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


class SpeakerRegistry:
    """Never learn from mixtures, guesses, or separated audio. No automatic merges."""

    def __init__(self, embed, path=None, threshold=0.55, margin=0.10):
        self.embed = embed
        self.path = Path(path) if path else None
        self.threshold = threshold
        self.margin = margin
        self.voices = {}
        self.next_id = 1
        self.session_id = uuid.uuid4().hex
        if self.path and self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            for item in saved.get("speakers", []):
                self.voices[int(item["id"])] = {
                    "name": item["name"], "samples": [self.normalize(x) for x in item["samples"]],
                    "confirmed": True,
                }
            self.next_id = max(self.voices, default=0) + 1

    @staticmethod
    def normalize(value):
        value = np.asarray(value, dtype=np.float64).reshape(-1)
        norm = np.linalg.norm(value)
        if not np.isfinite(value).all() or norm < 1e-8:
            raise ValueError("Invalid voice embedding")
        return value / norm

    def identify(self, audio, *, learn=False, clean=False, include_pending=False):
        seconds = len(audio) / RATE
        if seconds < (1.5 if clean else 2.0):
            return None
        vector = self.normalize(self.embed(audio))
        # Reject inconsistent multi-second samples instead of averaging two
        # voices into a new identity when diarization misses a change.
        if seconds >= 4:
            middle = len(audio) // 2
            halves = [self.normalize(self.embed(part)) for part in (audio[:middle], audio[middle:])]
            if float(halves[0] @ halves[1]) < 0.80:
                return None
        threshold = max(self.threshold, 0.72 if clean else 0.82)
        margin = max(self.margin, 0.15 if clean else 0.20)
        scores = []
        for ident, voice in self.voices.items():
            # A centroid plus median reduces the effect of a single bad sample.
            samples = voice["samples"]
            centroid = self.normalize(np.mean(samples, axis=0))
            score = (float(vector @ centroid) + float(np.median([vector @ x for x in samples]))) / 2
            scores.append((score, ident))
        scores.sort(reverse=True)
        best = scores[0][0] if scores else -1
        gap = best - scores[1][0] if len(scores) > 1 else 2
        ident = None
        if best >= threshold and gap >= margin:
            ident = scores[0][1]
            voice = self.voices[ident]
            if clean and learn and seconds >= 2 and not voice["confirmed"]:
                if min(float(vector @ sample) for sample in voice["samples"]) < 0.80:
                    return None
                voice["samples"].append(vector)
                voice["samples"] = voice["samples"][-12:]
                voice["clean_seconds"] = voice.get("clean_seconds", 0) + len(audio) / RATE
                voice["confirmed"] = len(voice["samples"]) >= 3 and voice["clean_seconds"] >= 9
                if voice["confirmed"]:
                    del self.voices[ident]
                    ident = self.next_id
                    self.next_id += 1
                    self.voices[ident] = voice
        elif clean and learn and seconds >= 2 and best < 0.40:
            # Negative IDs are private candidates; public numbers are allocated
            # only on confirmation. Bound uncertain evidence in busy rooms.
            pending = [i for i, v in self.voices.items() if not v["confirmed"]]
            if len(pending) >= 32:
                del self.voices[pending[0]]
            ident = min([0, *self.voices]) - 1
            self.voices[ident] = {"name": "", "samples": [vector], "confirmed": False,
                                  "clean_seconds": len(audio) / RATE}
        if ident is None:
            return None
        voice = self.voices[ident]
        if not voice["confirmed"] and not include_pending:
            return None
        return {"id": ident, "name": voice["name"], "similarity": round(best, 3) if best >= 0 else None,
                "confirmed": voice["confirmed"], "uncertain": not voice["confirmed"], "new": best < self.threshold}

    def enroll(self, audio, name):
        if len(audio) < 3 * RATE:
            raise ValueError("Provide at least three seconds of clear speech from one person.")
        # Several independent samples capture natural variation without one
        # long averaged fingerprint washing it out.
        samples = [self.normalize(self.embed(audio[start:start + 5 * RATE]))
                   for start in range(0, len(audio), 5 * RATE)
                   if len(audio[start:start + 5 * RATE]) >= 3 * RATE]
        ident = next((i for i, voice in self.voices.items() if voice["name"].casefold() == name.casefold()), None)
        if ident is None:
            ident = self.next_id
            self.next_id += 1
        self.voices[ident] = {"name": name, "samples": samples, "confirmed": True}
        self.save()
        return ident

    def save(self):
        if self.path:
            atomic_json(self.path, {"speakers": [
                {"id": ident, "name": voice["name"], "samples": [x.tolist() for x in voice["samples"]]}
                for ident, voice in self.voices.items() if voice["name"] and voice["confirmed"]
            ]})

    def reset(self):
        self.voices = {i: v for i, v in self.voices.items() if v["name"]}
        self.session_id = uuid.uuid4().hex

    def summary(self):
        return [{"id": i, "name": v["name"], "clips": len(v["samples"]), "confirmed": v["confirmed"]}
                for i, v in self.voices.items() if v["confirmed"]]


def partition_turns(turns, duration):
    """Make disjoint spans, preserving all simultaneously active speaker labels."""
    valid = [(max(0., float(a)), min(duration, float(b)), str(s)) for a, b, s in turns
             if np.isfinite(a) and np.isfinite(b) and b > a and b > 0 and a < duration]
    edges = sorted({0., duration, *(a for a, _, _ in valid), *(b for _, b, _ in valid)})
    result = []
    for a, b in zip(edges, edges[1:]):
        labels = tuple(sorted({s for x, y, s in valid if x < b and y > a}))
        if result and result[-1][2] == labels:
            result[-1] = (result[-1][0], b, labels)
        else:
            result.append((a, b, labels))
    return result


class LocalModels:
    """One inference lock protects GPU use and model settings changes."""

    def __init__(self, root, device, warmup=None):
        self.root = Path(root)
        self.device = device
        self.warmup = warmup
        self.lock = threading.RLock()
        self.pipeline = None
        self.separator = None
        self.separator_count = 2
        self.state = "not_configured"
        self.error = ""
        self.stage = "idle"
        self.settings_path = self.root / "speaker_settings.json"
        self.settings = {"enabled": True, "recover_overlap": True, "track_overlap": False, "token": "", "engine": "community"}
        if self.settings_path.exists():
            self.settings.update(json.loads(self.settings_path.read_text(encoding="utf-8")))
        self.settings["token"] = os.environ.get("HF_TOKEN", self.settings["token"])
        os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "0")
        self.notice = ""

    def status(self):
        return {"state": self.state, "error": self.error, "model": JOINT_MODEL if self.settings["engine"] == "pixit" else MODEL, "device": self.device,
                "engine": self.settings["engine"], "notice": self.notice,
                "stage": self.stage,
                "enabled": self.settings["enabled"], "recover_overlap": self.settings["recover_overlap"],
                "track_overlap": self.settings["track_overlap"],
                "token_set": bool(self.settings["token"])}

    def configure(self, values):
        with self.lock:
            updated = dict(self.settings)
            if "engine" in values:
                if values["engine"] not in ("community", "pixit"):
                    raise ValueError("Choose a supported speaker mode")
                updated["engine"] = values["engine"]
            for key in ("enabled", "recover_overlap", "track_overlap"):
                if key in values:
                    if not isinstance(values[key], bool):
                        raise ValueError(f"{key} must be true or false")
                    updated[key] = values[key]
            if values.get("token"):
                if not isinstance(values["token"], str) or len(values["token"]) > 512:
                    raise ValueError("Enter a valid read token")
                updated["token"] = values["token"].strip()
            atomic_json(self.settings_path, updated)
            self.settings = updated
            self.pipeline = None
            self.separator = None
            self.error = ""
            self.notice = ""
            self.state = "not_configured"
        self.prepare()

    def prepare(self):
        if not self.settings["enabled"]:
            self.state = "disabled"
            return
        if not self.settings["token"]:
            self.state = "not_configured"
            return
        if self.state in {"loading", "ready"}:
            return
        self.state = "loading"
        threading.Thread(target=self._load, daemon=True, name="speaker-model-loader").start()

    def _load(self):
        with self.lock:
            try:
                self.stage = "loading speaker detection"
                import torch
                if self.device == "cuda":
                    torch.cuda.empty_cache()
                if self.settings["engine"] == "pixit":
                    self.stage = "downloading joint voice separation"
                    pipeline = load_joint_pipeline(self.root / "model_cache", self.settings["token"])
                else:
                    pipeline = load_speaker_pipeline(self.root / "model_cache", self.settings["token"])
                self.stage = "moving speaker detection to " + self.device
                pipeline.to(torch.device(self.device))
                self.pipeline = pipeline
                if self.settings["recover_overlap"] and self.settings["engine"] != "pixit":
                    self.stage = "loading overlap recovery"
                    from speechbrain.inference.separation import SepformerSeparation
                    from speechbrain.utils.fetching import LocalStrategy
                    self.separator = SepformerSeparation.from_hparams(
                        source="speechbrain/sepformer-whamr", savedir=str(self.root / "separation_model"),
                        run_opts={"device": self.device}, local_strategy=LocalStrategy.COPY)
                    self.separator_count = 2
                if self.warmup:
                    self.stage = "loading voice recognition"
                    self.warmup()
                if self.settings["engine"] == "pixit":
                    self.stage = "checking separation on this computer"
                    # A forward pass detects missing runtime components, shape
                    # incompatibilities and GPU allocation failures before Ready.
                    with torch.inference_mode():
                        probe = {"waveform": torch.zeros(1, 5 * RATE), "sample_rate": RATE}
                        unpack_joint_result(pipeline(probe), 5 * RATE)
                    self.notice = "Joint separation is a preview. Test a recording below before using it live. Up to three voices per model window; crowded speech can remain mixed."
                self.stage = "ready"
                self.state = "ready"
            except Exception as exc:
                self.pipeline = None
                self.separator = None
                self.state = "error"
                details = model_error_details(exc, self.settings["token"])
                print(f"Speaker setup failed while {self.stage}: {details}", flush=True)
                # Do not expose upstream errors which may contain download credentials.
                if isinstance(exc, ImportError):
                    self.error = (f"A required software component could not load while {self.stage}. "
                                  f"{details} This is a dependency problem; changing your Hugging Face token will not fix it.")
                elif isinstance(exc, pickle.UnpicklingError):
                    self.error = ("Speaker model file could not be read (UnpicklingError). "
                                  "This indicates model compatibility or a damaged download, not necessarily a token problem. "
                                  f"Details while {self.stage}: {details}")
                elif self.settings["engine"] == "pixit":
                    self.error = (f"Joint separation could not finish {self.stage}. {details} "
                                  "If access was denied, accept both model links below and give your read token access to them. "
                                  "You can switch back to Standard at any time.")
                else:
                    self.error = (f"Speaker model could not load ({type(exc).__name__}). Accept the model access "
                                  "conditions, check your read token and internet connection, then save settings to retry.")

    def turns(self, audio):
        import torch
        with self.lock, torch.inference_mode():
            if self.state != "ready":
                raise RuntimeError("Speaker model is not ready")
            result = self.pipeline({"waveform": torch.from_numpy(audio.copy()).float().unsqueeze(0),
                                    "sample_rate": RATE})
            if self.settings["engine"] == "pixit":
                return unpack_joint_result(result, len(audio))[0]
            return [(turn.start, turn.end, speaker)
                    for turn, _, speaker in result.speaker_diarization.itertracks(yield_label=True)]

    def joint(self, audio):
        import torch
        with self.lock, torch.inference_mode():
            if self.state != "ready" or self.settings["engine"] != "pixit":
                raise RuntimeError("Prepare joint separation first")
            result = self.pipeline({"waveform": torch.from_numpy(audio.copy()).float().unsqueeze(0),
                                    "sample_rate": RATE})
            return unpack_joint_result(result, len(audio))

    def separate(self, audio, count=2):
        import torch
        import torchaudio.functional as audio_ops
        with self.lock, torch.inference_mode():
            if count not in (2, 3):
                return []
            if self.separator is None or self.separator_count != count:
                from speechbrain.inference.separation import SepformerSeparation
                from speechbrain.utils.fetching import LocalStrategy
                # Keep only one separator resident on an 8 GB GPU.
                self.separator = None
                if self.device == "cuda":
                    torch.cuda.empty_cache()
                model_id = "sepformer-whamr" if count == 2 else "sepformer-wsj03mix"
                self.separator = SepformerSeparation.from_hparams(
                    source="speechbrain/" + model_id, savedir=str(self.root / "separation_model" / model_id),
                    run_opts={"device": self.device}, local_strategy=LocalStrategy.COPY)
                self.separator_count = count
            samples = torch.from_numpy(audio.copy()).float().unsqueeze(0).to(self.device)
            samples = audio_ops.resample(samples, RATE, 8000)
            sources = self.separator.separate_batch(samples)[0].transpose(0, 1)
            sources = audio_ops.resample(sources, 8000, RATE).cpu().numpy()
            return [x[:len(audio)] for x in sources]


class OverlapTrackMemory:
    """Experimental source continuity, separate from trusted speaker identity.

    Never enrolls a speaker, passes a filter, or changes a saved voice. A
    public track needs agreement across three distinct clips. Ambiguous and
    duplicated simultaneous sources contribute no evidence.
    """
    def __init__(self, embed):
        self.embed = embed
        self.tracks = []
        self.next_id = 1
        self.session = uuid.uuid4().hex[:12]

    def observe(self, sources, tick, learn=True):
        if learn:
            self.tracks = [t for t in self.tracks if t['id'] is not None or tick - (t['tick'] or 0) <= 20]
        vectors = []
        for source in sources:
            vector = None
            if len(source) >= 2 * RATE and np.isfinite(source).all() and np.mean(source ** 2) > 1e-8:
                middle = len(source) // 2
                a = SpeakerRegistry.normalize(self.embed(source[:middle]))
                b = SpeakerRegistry.normalize(self.embed(source[middle:]))
                if float(a @ b) >= 0.80:
                    vector = SpeakerRegistry.normalize(a + b)
            vectors.append(vector)
        invalid = set()
        for i, a in enumerate(vectors):
            for j in range(i):
                b = vectors[j]
                if a is not None and b is not None and float(a @ b) >= 0.70:
                    invalid.update((i, j))
        proposals = []
        for i, vector in enumerate(vectors):
            if vector is None or i in invalid:
                proposals.append((None, False))
                continue
            ranked = sorted([(float(vector @ t['anchor']), n) for n, t in enumerate(self.tracks)], reverse=True)
            if ranked and ranked[0][0] >= 0.82 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.15):
                proposals.append((ranked[0][1], False))
            else:
                proposals.append((None, not ranked or ranked[0][0] < 0.40))
        used = [n for n, _ in proposals if n is not None]
        result = []
        for i, (n, novel) in enumerate(proposals):
            if n is not None and used.count(n) > 1:
                result.append(None)
                continue
            if n is None and novel and learn and len(self.tracks) < 64:
                n = len(self.tracks)
                self.tracks.append({'anchor': vectors[i], 'clips': 0, 'seconds': 0., 'tick': None, 'id': None})
            if n is None:
                result.append(None)
                continue
            track = self.tracks[n]
            if learn and track['tick'] != tick:
                track['clips'] += 1
                track['seconds'] += min(len(sources[i]) / RATE, 4.)
                track['tick'] = tick
                if track['id'] is None and track['clips'] >= 3 and track['seconds'] >= 6:
                    track['id'] = self.next_id
                    self.next_id += 1
            result.append(None if track['id'] is None else {
                'id': f"{self.session}-{track['id']}", 'label': f"Voice track {track['id']}",
                'verified': False})
        return result


class SpeakerAnalyzer:
    def __init__(self, models, registry, transcribe):
        self.models = models
        self.registry = registry
        self.transcribe = transcribe
        self.overlap_memory = OverlapTrackMemory(registry.embed)
        self.clip_tick = 0
        self.joint_conversation = JointConversation()

    def analyze(self, audio, language=None, *, learn=True, learn_after=0., learn_before=None):
        """Transcribe once normally; extra ASR is restricted to recoverable overlap."""
        duration = len(audio) / RATE
        if self.models.state == "ready" and self.models.settings.get("engine") == "pixit":
            if learn_after != 0 or learn_before is not None:
                raise ValueError("Joint separation uses listening sessions. Use /v1/audio/transcriptions with listening_session.")
            try:
                return analyze_joint(self, audio, language)
            except Exception as exc:
                self.models.notice = "Voice separation failed for this clip; showing the mixed transcript without guessed speakers. " + model_error_details(exc, self.models.settings.get("token", ""))
                words, detected = self.transcribe(audio, language)
                return self.result([self.segment(w["start"],w["end"],w["text"],None,True,"overlap") for w in words],detected)
        self.clip_tick += 1
        words, detected = self.transcribe(audio, language)
        if self.models.state != "ready":
            return self.result([self.segment(w["start"], w["end"], w["text"], None, False, "unavailable")
                                for w in words], detected)
        spans = partition_turns(self.models.turns(audio), duration)
        labels = sorted({label for _, _, active in spans for label in active})
        matches = {}
        clean_audio = {}
        # Embed only exclusive speech, never a mixture. Keep turns separate to
        # avoid timestamp distortion, joining only for voice identity extraction.
        for label in labels:
            pieces = [audio[int(a * RATE):int(b * RATE)] for a, b, active in spans if active == (label,)]
            matches[label] = self.registry.identify(np.concatenate(pieces), learn=False, clean=True) if pieces else None
            upper = duration if learn_before is None else learn_before
            eligible = [audio[int(max(a, learn_after) * RATE):int(min(b, upper) * RATE)]
                        for a, b, active in spans
                        if active == (label,) and min(b, upper) - max(a, learn_after) >= 2.0
                        and sum(1 for w in words if w["text"].strip()
                                and w["start"] >= max(a, learn_after)
                                and w["end"] <= min(b, upper)) >= 3]
            # Use the longest uninterrupted solo stretch for learning; scattered
            # syllables between other voices are not enrollment evidence.
            clean_audio[label] = max(eligible, key=len) if eligible else np.zeros(0, dtype=np.float32)
        # Distinct local speakers must not map to the same persistent identity.
        ids = [m["id"] for m in matches.values() if m]
        duplicated = {i for i in ids if ids.count(i) > 1}
        matches = {s: (None if m and m["id"] in duplicated else m) for s, m in matches.items()}
        previous_voices = copy.deepcopy(self.registry.voices) if learn else None
        previous_next = self.registry.next_id
        if learn:
            for label in labels:
                if duplicated:
                    continue
                learned = self.registry.identify(clean_audio[label], learn=True, clean=True, include_pending=True)
                if learned:
                    matches[label] = learned
        ids = [m["id"] for m in matches.values() if m]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates and learn:
            self.registry.voices = previous_voices
            self.registry.next_id = previous_next
        matches = {s: (None if m and m["id"] in duplicates else m) for s, m in matches.items()}
        segments = []
        recovered = []
        if self.models.settings["recover_overlap"]:
            for a, b, active in spans:
                if len(active) not in (2, 3) or b - a < 0.7:
                    continue
                try:
                    sources = self.models.separate(audio[int(a * RATE):int(b * RATE)], count=len(active))
                except Exception:
                    # A separator failure must not erase the original transcript.
                    sources = []
                candidates = []
                for source in sources:
                    match = self.registry.identify(source, learn=False, clean=False)
                    # A familiar voice can overlap for this entire clip. Its
                    # clean reference may have come from an earlier clip.
                    allowed = {i for i, v in self.registry.voices.items() if v["confirmed"]}
                    if not match or not match["confirmed"] or match["id"] not in allowed:
                        match = None
                    # Different conversations may use different languages.
                    recovered_words, source_language = self.transcribe(source, language)
                    if not recovered_words:
                        break
                    candidates.append((match, recovered_words, source_language))
                if len(candidates) == len(active):
                    identities = [m["id"] for m, _, _ in candidates if m]
                    duplicates = {i for i in identities if identities.count(i) > 1}
                    try:
                        tracks = (self.overlap_memory.observe(sources, self.clip_tick,
                            learn=learn and learn_after == 0 and learn_before is None)
                            if self.models.settings.get("track_overlap", False) else [None] * len(sources))
                    except Exception:
                        tracks = [None] * len(sources)
                    recovered.append((a, b))
                    for track, (match, entries, source_language) in enumerate(candidates):
                        if match and match["id"] in duplicates:
                            match = None
                        for word in entries:
                            item = self.segment(a + word["start"], min(b, a + word["end"]),
                                                word["text"], match, True, "separated")
                            item["track"] = f"overlap-{a:.3f}-{track}"
                            item["voice_track"] = tracks[track] if match is None else None
                            item["language"] = source_language
                            segments.append(item)
        for word in words:
            a, b = word["start"], word["end"]
            if any(x <= a and b <= y for x, y in recovered):
                continue
            relevant = [(x, y, active) for x, y, active in spans if x < b and y > a]
            active = {s for _, _, speakers in relevant for s in speakers}
            overlapping = any(len(speakers) > 1 for _, _, speakers in relevant)
            # Words crossing a speaker boundary stay unassigned, not majority-voted.
            match = matches.get(next(iter(active))) if len(active) == 1 and not overlapping else None
            coverage = sum(max(0., min(b, y) - max(a, x)) for x, y, speakers in relevant if speakers)
            if coverage < (b - a) * 0.8:
                match = None
            segments.append(self.segment(a, b, word["text"], match, overlapping,
                                         "overlap" if overlapping else "clean" if match else "unknown"))
        return self.result(sorted(segments, key=lambda s: (s["start"], s["end"])), detected)

    @staticmethod
    def segment(start, end, text, speaker, overlapping, source):
        if speaker and not speaker["confirmed"]:
            speaker = None
            if source == "clean":
                source = "unknown"
        return {"start": round(start, 3), "end": round(end, 3), "text": text,
                "speaker": speaker, "overlapping": overlapping, "source": source,
                "accepted": bool(speaker and speaker["confirmed"] and source == "clean")}

    def result(self, segments, language):
        speakers = {s["speaker"]["id"] for s in segments if s["speaker"]}
        single = (next((s["speaker"] for s in segments if s["speaker"]), None)
                  if len(speakers) == 1 and all(s["accepted"] for s in segments) else None)
        return {"text": "".join(s["text"] for s in segments).strip(), "language": language,
                "speaker": single, "segments": segments, "speaker_session": self.registry.session_id,
                "speaker_status": self.models.status()}


class StreamSession:
    """Bounded rolling context; callers send consecutive chunks and monotonically increasing sequence IDs."""

    def __init__(self, analyzer):
        self.analyzer = analyzer
        self.audio = np.zeros(0, dtype=np.float32)
        self.offset = 0.
        self.committed = 0.
        self.learned_until = 0.
        self.sequence = 0
        self.touched = time.monotonic()
        self.closed = False

    def push(self, audio, sequence, language=None, final=False):
        if self.closed or sequence != self.sequence:
            raise ValueError(f"Expected sequence {self.sequence} for an open stream")
        if not len(audio) or len(audio) > 4 * RATE:
            raise ValueError("Each stream chunk must contain between 0 and 4 seconds of audio")
        combined = np.concatenate((self.audio, audio))
        # No state mutation until analysis succeeds: a failed request is retryable.
        limit = self.offset + len(combined) / RATE - (0 if final else 1.2)
        previous_voices = copy.deepcopy(self.analyzer.registry.voices)
        previous_next = self.analyzer.registry.next_id
        try:
            result = self.analyzer.analyze(combined, language, learn=True,
                                           learn_after=self.learned_until - self.offset, learn_before=limit - self.offset)
        except Exception:
            self.analyzer.registry.voices = previous_voices
            self.analyzer.registry.next_id = previous_next
            raise
        finalized, provisional = [], []
        for segment in result["segments"]:
            segment = dict(segment, start=round(segment["start"] + self.offset, 3),
                           end=round(segment["end"] + self.offset, 3))
            if segment["start"] < self.committed - 0.001:
                continue
            (finalized if segment["end"] <= limit else provisional).append(segment)
        if finalized:
            self.committed = max(s["end"] for s in finalized)
        # Silence can be finalized too. Keep any boundary-crossing provisional
        # word, rather than letting silent streams grow without bound.
        safe_cut = min([limit] + [s["start"] for s in provisional])
        self.committed = max(self.committed, safe_cut)
        self.learned_until = max(self.learned_until, limit)
        self.audio = combined
        self.sequence += 1
        self.touched = time.monotonic()
        self.closed = final
        # Keep twelve seconds of context, but never discard uncommitted audio.
        trim = min(max(0, len(self.audio) - 12 * RATE), max(0, int((self.committed - self.offset) * RATE)))
        if trim:
            self.audio = self.audio[trim:]
            self.offset += trim / RATE
        if len(self.audio) > 20 * RATE:
            self.closed = True
            raise ValueError("Stream could not finalize speech; start a new session")
        return dict(result, segments=finalized, provisional_segments=provisional,
                    text="".join(s["text"] for s in finalized).strip(), speaker=None,
                    next_sequence=self.sequence, final=final)
