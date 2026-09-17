"""Joint separation adapter and session continuity for the optional PixIT engine.

Session tracks are estimates, not enrolled identities. In particular, separation
outputs must never update the trusted voice registry or pass its speaker filter.
"""
import copy
import importlib
import time
import uuid

import numpy as np

RATE = 16000
JOINT_MODEL = "pyannote/speech-separation-ami-1.0"
ACCESS_MODELS = (JOINT_MODEL, "pyannote/separation-ami-1.0")


def prepare_joint_imports():
    # pyannote's ToTaToNet only needs these two Asteroid inference components.
    # Do not install the full training toolkit (it requires C++ metrics builds).
    from vendor.asteroid_inference.masknn import DPRNN
    from vendor.asteroid_inference.utils.torch_utils import pad_x_to_y
    module = importlib.import_module("pyannote.audio.models.separation.ToTaToNet")
    module.DPRNN = DPRNN
    module.pad_x_to_y = pad_x_to_y
    module.ASTEROID_IS_AVAILABLE = True
    if not module.TRANSFORMERS_IS_AVAILABLE:
        raise ImportError("The separation components are missing. Run the new Evora installer and choose Update.")
    # pyannote 4.0.1 still calls SpeechBrain's pre-1.1 download arguments.
    # Adapt only its factory, including the later .to(device) reload.
    from speechbrain.inference.speaker import EncoderClassifier
    from speechbrain.utils.fetching import FetchConfig, LocalStrategy
    verification = importlib.import_module("pyannote.audio.pipelines.speaker_verification")
    class CompatibleEncoder:
        @staticmethod
        def from_hparams(source, **kwargs):
            kwargs["fetch_config"] = FetchConfig(
                token=kwargs.pop("use_auth_token", False),
                revision=kwargs.pop("revision", None),
                huggingface_cache_dir=kwargs.pop("huggingface_cache_dir", None))
            kwargs.setdefault("local_strategy", LocalStrategy.COPY)
            return EncoderClassifier.from_hparams(source, **kwargs)
    verification.SpeechBrain_EncoderClassifier = CompatibleEncoder


def load_joint_pipeline(cache_dir, token):
    import torch
    from huggingface_hub import hf_hub_download
    import yaml
    from evora_speakers import prepare_speechbrain_imports
    prepare_speechbrain_imports()
    prepare_joint_imports()
    from pyannote.audio import Pipeline
    from pyannote.audio.core.task import Specifications, Problem, Resolution
    from torch.torch_version import TorchVersion
    # Fetch the tiny config first so denied access gives an actionable error.
    path = hf_hub_download(JOINT_MODEL, "config.yaml", token=token, cache_dir=str(cache_dir))
    with open(path, encoding="utf-8") as source:
        config = yaml.safe_load(source)
    params = config["pipeline"].setdefault("params", {})
    params.update(segmentation_batch_size=1, embedding_batch_size=1)
    # pyannote 4 accepts legacy configs but moved model revisions to a keyword.
    segmentation = params.get("segmentation")
    if isinstance(segmentation, str) and "@" in segmentation:
        model, revision = segmentation.rsplit("@", 1)
        params["segmentation"] = {"checkpoint": model, "revision": revision}
    with torch.serialization.safe_globals([TorchVersion, Specifications, Problem, Resolution]):
        pipeline = Pipeline.from_pretrained(config, token=token, cache_dir=str(cache_dir))
    if pipeline is None:
        raise RuntimeError("The joint separation model could not be loaded.")
    return pipeline


def unpack_joint_result(result, length):
    """Keep the model's source-to-label mapping and waveform timing intact."""
    diarization, sources = result[:2]
    turns = [(float(t.start), float(t.end), str(s))
             for t, _, s in diarization.itertracks(yield_label=True)]
    if sources is None:
        return turns, {}
    data = np.asarray(sources.data, dtype=np.float32)
    labels = list(diarization.labels())
    if data.ndim != 2 or data.shape[1] != len(labels) or not np.isfinite(data).all():
        raise ValueError("The separation model returned invalid audio channels.")
    # The SlidingWindow describes sample centers. Do not silently shift speech.
    frame = sources.sliding_window
    if abs(frame.step - 1 / RATE) > 1e-6:
        raise ValueError("Unexpected separated audio sample rate.")
    offset = int(round((frame.start + frame.duration / 2 - 0.5 / RATE) * RATE))
    output = {}
    for column, label in enumerate(labels):
        audio = np.zeros(length, dtype=np.float32)
        start, source_start = max(0, offset), max(0, -offset)
        count = min(length - start, len(data) - source_start)
        if count > 0:
            audio[start:start + count] = data[source_start:source_start + count, column]
        output[str(label)] = audio
    return turns, output


def intersection(a, b):
    return max(0., min(a[1], b[1]) - max(a[0], b[0]))


class JointConversation:
    """Stitch diarization labels using shared audio, never the local label number.

The model reanalyzes the last ten seconds along with each new clip. Assignment
uses reciprocal temporal overlap in that shared context. When correspondence
is ambiguous it is withheld rather than inventing a new confirmed identity.
"""
    def __init__(self):
        self.audio = np.zeros(0, dtype=np.float32)
        self.turns = []
        self.tracks = {}
        self.next_id = 1
        self.session = uuid.uuid4().hex[:12]
        self.tick = 0
        self.touched = time.monotonic()

    def analyze(self, engine, audio, embed=None):
        # A pause or stopped session must not splice unrelated conversations.
        if time.monotonic() - self.touched > 120:
            self.__init__()
        prefix = len(self.audio) / RATE
        combined = np.concatenate((self.audio, audio))
        turns, sources = engine(combined)
        vectors = {}
        if embed:
            for label, source in sources.items():
                pieces = [source[int(max(0,a)*RATE):int(min(len(combined)/RATE,b)*RATE)]
                          for a,b,s in turns if s == label]
                evidence = np.concatenate(pieces) if pieces else np.zeros(0)
                if len(evidence) >= 1.5*RATE and np.mean(evidence**2) > 1e-9:
                    value = np.asarray(embed(evidence), dtype=np.float64).reshape(-1)
                    norm = np.linalg.norm(value)
                    if np.isfinite(value).all() and norm > 1e-8:
                        vectors[label] = value/norm
        mapping = self.link(turns, prefix, len(combined) / RATE, vectors)
        self.tick += 1
        self.touched = time.monotonic()
        cut = max(0, len(combined) - 10 * RATE)
        self.audio = combined[cut:].copy()
        shift = cut / RATE
        self.turns = [(max(a, shift) - shift, b - shift, mapping[s])
                      for a, b, s in turns if b > shift and s in mapping]
        current_turns = [(max(0., a - prefix), min(len(audio) / RATE, b - prefix), s)
                         for a, b, s in turns if b > prefix and a < prefix + len(audio) / RATE]
        current_sources = {s: samples[len(combined) - len(audio):] for s, samples in sources.items()}
        return current_turns, current_sources, {s: self.public(i) for s, i in mapping.items()}

    def link(self, turns, prefix, duration, vectors=None):
        vectors = vectors or {}
        labels = sorted({s for _, _, s in turns})
        mapping, proposed = {}, {}
        # Duplicate separated channels are not independent people.
        invalid = {a for a in vectors for b in vectors if a != b and float(vectors[a] @ vectors[b]) > .85}
        for label in labels:
            if label in invalid:
                continue
            shared = [(max(0., a), min(prefix, b)) for a, b, s in turns if s == label and a < prefix]
            scores = {}
            for ident, track in self.tracks.items():
                previous = [(a, b) for a, b, i in self.turns if i == ident]
                common = sum(intersection(a, b) for a in shared for b in previous)
                union = sum(b - a for a, b in shared) + sum(b - a for a, b in previous) - common
                anchor = track.get("anchor")
                if label in vectors and anchor is not None:
                    similarity = float(vectors[label] @ anchor)
                    if similarity >= .55:
                        temporal = common/union if common >= .5 and union > 0 else 0.
                        scores[ident] = .7*similarity + .3*temporal
                elif common >= .5 and union > 0:
                    scores[ident] = common / union
            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
            if ranked and ranked[0][1] >= .49 and (len(ranked) == 1 or ranked[0][1] - ranked[1][1] >= .1):
                proposed[label] = ranked[0][0]
            elif not ranked and (not shared or sum(max(0., b-a) for a,b in shared) < .25):
                # A genuinely new local speaker starts a private candidate.
                # An ambiguous re-label of existing audio does not.
                if len(self.tracks) < 64:
                    ident = self.next_id
                    self.next_id += 1
                    self.tracks[ident] = {"seconds": 0., "observations": 0, "anchor": vectors.get(label)}
                    mapping[label] = ident
        for label, ident in proposed.items():
            if list(proposed.values()).count(ident) == 1:
                mapping[label] = ident
        for label, ident in mapping.items():
            fresh = sum(max(0., min(b, duration) - max(a, prefix)) for a,b,s in turns if s == label)
            if fresh >= .5:
                self.tracks[ident]["seconds"] += fresh
                self.tracks[ident]["observations"] += 1
        # Bound private candidates abandoned by the rolling context.
        active = set(mapping.values())
        self.tracks = {i:t for i,t in self.tracks.items() if i in active or self.public(i)}
        return mapping

    def public(self, ident):
        track = self.tracks[ident]
        if track["seconds"] < 3 or track["observations"] < 2:
            return None
        return {"id": f"{self.session}-{ident}", "label": f"Voice {ident}",
                "verified": False, "method": "joint_context"}


def analyze_joint(analyzer, audio, language):
    """Transcribe separated speakers without turning estimates into enrollment."""
    from evora_speakers import partition_turns
    before = copy.deepcopy(analyzer.joint_conversation)
    try:
        analyzer.last_joint_sources = ({}, {})
        turns, sources, tracks = analyzer.joint_conversation.analyze(analyzer.models.joint, audio, analyzer.registry.embed)
        if getattr(analyzer, "capture_sources", False):
            analyzer.last_joint_sources = (sources, tracks)
        duration = len(audio) / RATE
        spans = partition_turns(turns, duration)
        segments, matches = [], {}
        for label, source in sources.items():
            active = [(a,b) for a,b,s in turns if s == label]
            if not active:
                continue
            # Matching named voices is read-only. Separated speech never trains
            # trusted profiles, even if the separator sounds convincing.
            pieces = [source[int(a*RATE):int(b*RATE)] for a,b in active]
            match = analyzer.registry.identify(np.concatenate(pieces), learn=False, clean=False)
            matches[label] = match
        ids = [m["id"] for m in matches.values() if m]
        duplicated = {i for i in ids if ids.count(i) > 1}
        detected = language
        for label, source in sources.items():
            active = [(a,b) for a,b,s in turns if s == label]
            if not active or np.mean(source ** 2) < 1e-9:
                continue
            # Keep original timing; don't concatenate words across silent gaps.
            words, source_language = analyzer.transcribe(source, language)
            detected = detected or source_language
            match = matches.get(label)
            if match and match["id"] in duplicated:
                match = None
            for word in words:
                a, b = max(0., word["start"]), min(duration, word["end"])
                if b <= a or sum(intersection((a,b), span) for span in active) < (b-a)*.8:
                    continue
                overlapping = any(len(s) > 1 and intersection((a,b),(x,y)) > 0 for x,y,s in spans)
                item = analyzer.segment(a,b,word["text"],match,overlapping,"separated")
                item.update(track=f"joint-{label}", voice_track=tracks.get(label) if not match else None,
                            language=source_language)
                segments.append(item)
        if not segments:
            words, detected = analyzer.transcribe(audio, language)
            segments = [analyzer.segment(w["start"], w["end"], w["text"], None, True, "overlap") for w in words]
        if getattr(analyzer.models, "notice", "").startswith("Voice separation failed"):
            analyzer.models.notice = "Joint separation preview: labels are estimates. Check accuracy using a recording test."
        return analyzer.result(sorted(segments,key=lambda s:(s["start"],s["end"])),detected)
    except Exception:
        analyzer.joint_conversation = before
        raise
