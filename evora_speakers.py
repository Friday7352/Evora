"""Local, overlap-aware speaker analysis. Model imports are lazy for fast startup."""

import json
import copy
import os
import threading
import time
import uuid
from pathlib import Path

import numpy as np

RATE = 16000
MODEL = "pyannote/speaker-diarization-community-1"


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

    def identify(self, audio, *, learn=False, clean=False):
        if len(audio) / RATE < 0.7:
            return None
        vector = self.normalize(self.embed(audio))
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
        if best >= self.threshold and gap >= self.margin:
            ident = scores[0][1]
            voice = self.voices[ident]
            if clean and learn and not voice["name"]:
                voice["samples"].append(vector)
                voice["samples"] = voice["samples"][-12:]
                voice["clean_seconds"] = voice.get("clean_seconds", 0) + len(audio) / RATE
                voice["confirmed"] = len(voice["samples"]) >= 2 and voice["clean_seconds"] >= 3
        elif clean and learn and len(audio) >= RATE and best < self.threshold - self.margin:
            ident = self.next_id
            self.next_id += 1
            self.voices[ident] = {"name": "", "samples": [vector], "confirmed": False,
                                  "clean_seconds": len(audio) / RATE}
        if ident is None:
            return None
        voice = self.voices[ident]
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
                for i, v in self.voices.items()]


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
        self.settings_path = self.root / "speaker_settings.json"
        self.settings = {"enabled": True, "recover_overlap": True, "token": ""}
        if self.settings_path.exists():
            self.settings.update(json.loads(self.settings_path.read_text(encoding="utf-8")))
        self.settings["token"] = os.environ.get("HF_TOKEN", self.settings["token"])
        os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "0")

    def status(self):
        return {"state": self.state, "error": self.error, "model": MODEL, "device": self.device,
                "enabled": self.settings["enabled"], "recover_overlap": self.settings["recover_overlap"],
                "token_set": bool(self.settings["token"])}

    def configure(self, values):
        with self.lock:
            updated = dict(self.settings)
            for key in ("enabled", "recover_overlap"):
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
                import torch
                from pyannote.audio import Pipeline
                pipeline = Pipeline.from_pretrained(MODEL, token=self.settings["token"],
                                                    cache_dir=str(self.root / "model_cache"))
                pipeline.to(torch.device(self.device))
                self.pipeline = pipeline
                if self.settings["recover_overlap"]:
                    from speechbrain.inference.separation import SepformerSeparation
                    from speechbrain.utils.fetching import LocalStrategy
                    self.separator = SepformerSeparation.from_hparams(
                        source="speechbrain/sepformer-whamr", savedir=str(self.root / "separation_model"),
                        run_opts={"device": self.device}, local_strategy=LocalStrategy.COPY)
                    self.separator_count = 2
                if self.warmup:
                    self.warmup()
                self.state = "ready"
            except Exception as exc:
                self.pipeline = None
                self.separator = None
                self.state = "error"
                # Do not expose upstream errors which may contain download credentials.
                self.error = (f"Speaker model could not load ({type(exc).__name__}). Accept the model access "
                              "conditions, check your read token and internet connection, then save settings to retry.")

    def turns(self, audio):
        import torch
        with self.lock, torch.inference_mode():
            if self.state != "ready":
                raise RuntimeError("Speaker model is not ready")
            result = self.pipeline({"waveform": torch.from_numpy(audio.copy()).float().unsqueeze(0),
                                    "sample_rate": RATE})
            return [(turn.start, turn.end, speaker)
                    for turn, _, speaker in result.speaker_diarization.itertracks(yield_label=True)]

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


class SpeakerAnalyzer:
    def __init__(self, models, registry, transcribe):
        self.models = models
        self.registry = registry
        self.transcribe = transcribe

    def analyze(self, audio, language=None, *, learn=True, learn_after=0., learn_before=None):
        """Transcribe once normally; extra ASR is restricted to recoverable overlap."""
        duration = len(audio) / RATE
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
                        if active == (label,) and min(b, upper) > max(a, learn_after)]
            clean_audio[label] = np.concatenate(eligible) if eligible else np.zeros(0, dtype=np.float32)
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
                learned = self.registry.identify(clean_audio[label], learn=True, clean=True)
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
                    recovered_words, _ = self.transcribe(source, language or detected)
                    if not recovered_words:
                        break
                    candidates.append((match, recovered_words))
                if len(candidates) == len(active):
                    identities = [m["id"] for m, _ in candidates if m]
                    duplicates = {i for i in identities if identities.count(i) > 1}
                    recovered.append((a, b))
                    for track, (match, entries) in enumerate(candidates):
                        if match and match["id"] in duplicates:
                            match = None
                        for word in entries:
                            item = self.segment(a + word["start"], min(b, a + word["end"]),
                                                word["text"], match, True, "separated")
                            item["track"] = f"overlap-{a:.3f}-{track}"
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
