import io
import threading
from pathlib import Path

import numpy as np
import pytest
from flask import Flask

from evora_api import register_routes
from evora_speakers import RATE, SpeakerAnalyzer, SpeakerRegistry, StreamSession, partition_turns


def audio(seconds=3, voice=1):
    return np.full(int(seconds * RATE), voice, dtype=np.float32)


def embed(samples):
    return [1., 0.] if np.mean(samples) < 1.5 else [0., 1.]


class Models:
    state = "ready"
    error = ""
    settings = {"recover_overlap": False}

    def __init__(self, turns=None):
        self.detected = turns

    def turns(self, samples):
        return self.detected if self.detected is not None else [(0., len(samples) / RATE, "a")]

    def status(self):
        return {"state": self.state, "error": self.error}


def asr(samples, language):
    return [{"start": float(i), "end": i + 0.5, "text": f" word{i}"}
            for i in range(int(len(samples) / RATE))], language or "en"


def test_partition_preserves_overlap_and_silence():
    assert partition_turns([(0, 2, "a"), (1, 3, "b")], 4) == [
        (0, 1, ("a",)), (1, 2, ("a", "b")), (2, 3, ("b",)), (3, 4, ())]


def test_never_learn_from_mixed_or_uncertain_audio():
    registry = SpeakerRegistry(embed)
    assert registry.identify(audio(), clean=False, learn=True) is None
    assert not registry.voices
    first = registry.identify(audio(), clean=True, learn=True)
    assert not first["confirmed"]
    for _ in range(4):
        registry.identify(audio(), clean=False, learn=True)
    assert len(registry.voices[1]["samples"]) == 1
    assert registry.identify(audio(), clean=True, learn=True)["confirmed"]


def test_enrollment_persists_only_named_voices(tmp_path):
    path = tmp_path / "voices.json"
    registry = SpeakerRegistry(embed, path)
    saved = registry.enroll(audio(), "Alice")
    registry.identify(audio(3, 2), learn=True, clean=True)
    registry.save()
    loaded = SpeakerRegistry(embed, path)
    assert list(loaded.voices) == [saved]
    assert loaded.identify(audio())["name"] == "Alice"
    assert loaded.session_id != registry.session_id


def test_overlap_is_unassigned_and_never_enrolled():
    registry = SpeakerRegistry(embed)
    engine = SpeakerAnalyzer(Models([(0, 3, "a"), (0, 3, "b")]), registry, asr)
    result = engine.analyze(audio())
    assert all(s["overlapping"] and not s["accepted"] for s in result["segments"])
    assert result["speaker"] is None
    assert not registry.voices


def test_two_speakers_get_separate_words():
    registry = SpeakerRegistry(embed)
    registry.enroll(audio(), "Alice")
    registry.enroll(audio(3, 2), "Bob")
    engine = SpeakerAnalyzer(Models([(0, 3, "a"), (3, 6, "b")]), registry, asr)
    result = engine.analyze(np.concatenate([audio(), audio(3, 2)]))
    assert [s["speaker"]["name"] for s in result["segments"]] == ["Alice"] * 3 + ["Bob"] * 3
    assert result["speaker"] is None


def test_boundary_crossing_word_has_no_speaker():
    registry = SpeakerRegistry(embed)
    registry.enroll(audio(), "Alice")
    registry.enroll(audio(3, 2), "Bob")
    engine = SpeakerAnalyzer(Models([(0, 3, "a"), (3, 6, "b")]), registry,
                             lambda *_: ([{"start": 2.8, "end": 3.2, "text": "boundary"}], "en"))
    result = engine.analyze(np.concatenate([audio(), audio(3, 2)]))
    assert result["segments"][0]["speaker"] is None


def test_same_identity_for_two_local_speakers_is_rejected_without_learning():
    registry = SpeakerRegistry(embed)
    registry.enroll(audio(), "Alice")
    engine = SpeakerAnalyzer(Models([(0, 3, "a"), (3, 6, "b")]), registry, asr)
    result = engine.analyze(audio(6))
    assert all(s["speaker"] is None for s in result["segments"])
    assert len(registry.voices[1]["samples"]) == 1


def test_new_conflicting_local_speakers_roll_back_learning():
    registry = SpeakerRegistry(embed)
    engine = SpeakerAnalyzer(Models([(0, 3, "a"), (3, 6, "b")]), registry, asr)
    result = engine.analyze(audio(6))
    assert not registry.voices
    assert all(s["speaker"] is None for s in result["segments"])


def test_recovered_overlap_is_never_accepted_for_filtering():
    class Separation(Models):
        settings = {"recover_overlap": True}

        def separate(self, samples, count=2):
            return [audio(len(samples) / RATE), audio(len(samples) / RATE, 2)]

    registry = SpeakerRegistry(embed)
    registry.enroll(audio(), "Alice")
    registry.enroll(audio(3, 2), "Bob")
    models = Separation([(0, 4, "a"), (3, 7, "b")])
    signal = np.concatenate([audio(), audio(1, 1.5), audio(3, 2)])
    result = SpeakerAnalyzer(models, registry, asr).analyze(signal)
    recovered = [s for s in result["segments"] if s["source"] == "separated"]
    assert len(recovered) == 2
    assert all(not s["accepted"] and s["overlapping"] for s in recovered)


def test_stream_does_not_relearn_already_finalized_samples():
    registry = SpeakerRegistry(embed)
    registry.enroll(audio(), "Alice")
    stream = StreamSession(SpeakerAnalyzer(Models(), registry, asr))
    stream.push(audio(3), 0)
    count = len(registry.voices[1]["samples"])
    stream.push(audio(0.2), 1)
    assert len(registry.voices[1]["samples"]) == count


def test_unconfigured_model_returns_text_without_guessed_identity():
    models = Models()
    models.state = "not_configured"
    engine = SpeakerAnalyzer(models, SpeakerRegistry(embed), asr)
    result = engine.analyze(audio())
    assert result["text"]
    assert all(not s["accepted"] for s in result["segments"])


def test_stream_finalization_sequence_and_bounded_context():
    registry = SpeakerRegistry(embed)
    registry.enroll(audio(), "Alice")
    stream = StreamSession(SpeakerAnalyzer(Models(), registry, asr))
    committed = []
    for sequence in range(12):
        result = stream.push(audio(2), sequence, final=sequence == 11)
        committed.extend(s["start"] for s in result["segments"])
        assert len(stream.audio) <= 12 * RATE
    assert committed == sorted(set(committed))
    assert len(committed) == 24
    with pytest.raises(ValueError):
        stream.push(audio(2), 12)


def test_invalid_sequence_does_not_change_stream():
    stream = StreamSession(SpeakerAnalyzer(Models(), SpeakerRegistry(embed), asr))
    with pytest.raises(ValueError):
        stream.push(audio(), 4)
    assert stream.sequence == 0 and len(stream.audio) == 0


def test_silent_stream_does_not_grow_or_expire_early():
    stream = StreamSession(SpeakerAnalyzer(Models([]), SpeakerRegistry(embed), lambda *_: ([], "en")))
    for i in range(30):
        assert stream.push(audio(2), i)["segments"] == []
        assert len(stream.audio) <= 12 * RATE


def test_failed_stream_analysis_rolls_back_voice_learning():
    registry = SpeakerRegistry(embed)

    class Broken(SpeakerAnalyzer):
        def analyze(self, samples, *args, **kwargs):
            self.registry.identify(samples, clean=True, learn=True)
            raise RuntimeError("inference failed")

    stream = StreamSession(Broken(Models(), registry, asr))
    with pytest.raises(RuntimeError):
        stream.push(audio(), 0)
    assert not registry.voices
    assert stream.sequence == 0


@pytest.fixture
def client(tmp_path):
    app = Flask(__name__, template_folder=str(Path(__file__).parents[1] / "templates"))
    registry = SpeakerRegistry(embed, tmp_path / "voices.json")
    registry.enroll(audio(), "Alice")
    register_routes(app, Models(), registry, asr, lambda f, **_: np.load(f), threading.Lock())
    return app.test_client()


def upload(samples=None, **values):
    data = io.BytesIO()
    np.save(data, audio() if samples is None else samples)
    data.seek(0)
    return {"file": (data, "test.wav"), **values}


def test_legacy_response_and_speaker_filter(client):
    response = client.post("/v1/audio/transcriptions", data=upload())
    assert response.status_code == 200
    assert response.json["speaker"]["id"] == 1
    assert response.json["segments"]
    for selection in ("", "2"):
        response = client.post("/v1/audio/transcriptions", data=upload(allowed_speakers=selection))
        assert response.json["text"] == ""
        assert response.json["segments"] == []


def test_provisional_cannot_pass_speaker_filter(client):
    response = client.post("/v1/audio/transcriptions", data=upload(speaker="0", allowed_speakers="1"))
    assert response.json["text"] == ""


def test_settings_require_local_nonce(client):
    assert client.get("/speaker-settings").status_code == 200
    assert client.get("/speaker-settings", environ_base={"REMOTE_ADDR": "192.168.1.5"}).status_code == 403
    assert client.post("/speaker-settings", json={}).status_code == 403
    assert client.post("/speakers/enroll", data=upload(name="Eve")).status_code == 403


def test_invalid_selection_does_not_consume_stream_sequence(client):
    first = client.post("/v1/audio/stream", data=upload(sequence="0"))
    ident = first.json["session_id"]
    invalid = client.post("/v1/audio/stream", data=upload(session_id=ident, sequence="1", allowed_speakers="invalid"))
    assert invalid.status_code == 400
    valid = client.post("/v1/audio/stream", data=upload(session_id=ident, sequence="1"))
    assert valid.status_code == 200


def test_stream_retry_and_unknown_session(client):
    first = client.post("/v1/audio/stream", data=upload(sequence="0"))
    assert first.status_code == 200
    ident = first.json["session_id"]
    assert client.post("/v1/audio/stream", data=upload(session_id=ident, sequence="0")).status_code == 400
    second = client.post("/v1/audio/stream", data=upload(session_id=ident, sequence="1", final="1"))
    assert second.status_code == 200
    assert second.json["next_sequence"] == 2
    assert client.post("/v1/audio/stream", data=upload(session_id="missing")).status_code == 400


def test_listening_sessions_remember_randoms_without_leaking_between_sessions(client):
    first = client.post("/v1/listening-sessions").json["listening_session"]
    second = client.post("/v1/listening-sessions").json["listening_session"]
    def hear(session):
        return client.post("/v1/audio/transcriptions", data=upload(audio(3, 2), listening_session=session)).json
    initial = hear(first)["segments"][0]["speaker"]
    assert initial and not initial["confirmed"]
    # Polling setup must not clear active listening memory.
    assert client.get("/speakers").status_code == 200
    repeated = hear(first)["segments"][0]["speaker"]
    assert repeated["id"] == initial["id"] and repeated["confirmed"]
    assert not hear(second)["segments"][0]["speaker"]["confirmed"]
    client.delete("/v1/listening-sessions/" + first)
    assert client.post("/v1/audio/transcriptions", data=upload(listening_session=first)).status_code == 409
    assert hear(second)["segments"][0]["speaker"]["confirmed"]


def test_thirty_second_enrollment_keeps_multiple_samples(tmp_path):
    registry = SpeakerRegistry(embed, tmp_path / "voices.json")
    ident = registry.enroll(audio(30), "Alice")
    assert len(registry.voices[ident]["samples"]) == 6
    registry.identify(audio(3), clean=True, learn=True)
    assert len(registry.voices[ident]["samples"]) == 6  # saved identity cannot drift
    assert len(SpeakerRegistry(embed, registry.path).voices[ident]["samples"]) == 6


def test_short_random_utterances_accumulate_evidence():
    registry = SpeakerRegistry(embed)
    a = registry.identify(audio(1), learn=True, clean=True)
    b = registry.identify(audio(1), learn=True, clean=True)
    c = registry.identify(audio(1), learn=True, clean=True)
    assert a["id"] == b["id"] == c["id"]
    assert not a["confirmed"] and not b["confirmed"] and c["confirmed"]


def test_three_known_voices_can_be_matched_during_full_clip_overlap():
    class Three(Models):
        settings = {"recover_overlap": True}
        def separate(self, samples, count=2):
            assert count == 3
            return [audio(3, i) for i in (1, 2, 3)]
    registry = SpeakerRegistry(lambda samples: np.eye(3)[int(round(np.mean(samples))) - 1])
    for i, name in enumerate(("Alice", "Bob", "Charlie"), 1):
        registry.enroll(audio(3, i), name)
    result = SpeakerAnalyzer(Three([(0, 3, s) for s in "abc"]), registry, asr).analyze(audio(3))
    assert {s["speaker"]["id"] for s in result["segments"]} == {1, 2, 3}
    assert all(s["source"] == "separated" and not s["accepted"] for s in result["segments"])


def test_unfamiliar_overlapping_tracks_stay_distinct_without_learning():
    class Two(Models):
        settings = {"recover_overlap": True}
        def separate(self, samples, count=2):
            return [audio(3, 1), audio(3, 2)]
    registry = SpeakerRegistry(embed)
    result = SpeakerAnalyzer(Two([(0, 3, "a"), (0, 3, "b")]), registry, asr).analyze(audio())
    assert len({s["track"] for s in result["segments"]}) == 2
    assert all(s["speaker"] is None and not s["accepted"] for s in result["segments"])
    assert not registry.voices


def test_separator_download_failure_preserves_unidentified_transcript():
    class Failed(Models):
        settings = {"recover_overlap": True}
        def separate(self, samples, count=2):
            raise OSError("Model download unavailable")
    result = SpeakerAnalyzer(Failed([(0, 3, "a"), (0, 3, "b")]), SpeakerRegistry(embed), asr).analyze(audio())
    assert result["text"]
    assert all(s["speaker"] is None and s["overlapping"] for s in result["segments"])
