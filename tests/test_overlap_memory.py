import numpy as np
from evora_speakers import OverlapTrackMemory, RATE, SpeakerAnalyzer, SpeakerRegistry


def audio(voice):
    return np.full(2 * RATE, voice, dtype=np.float32)


def memory():
    return OverlapTrackMemory(lambda x: np.eye(3)[int(round(np.mean(x))) - 1])


def test_tracks_survive_output_permutation_without_solo_speech():
    tracker = memory()
    assert tracker.observe([audio(1), audio(2)], 1) == [None, None]
    assert tracker.observe([audio(2), audio(1)], 2) == [None, None]
    a, b = tracker.observe([audio(1), audio(2)], 3)
    assert a['id'] != b['id'] and not a['verified']
    assert tracker.observe([audio(2), audio(1)], 4) == [b, a]
    assert len(tracker.tracks) == 2


def test_duplicate_sources_and_mixed_halves_never_create_tracks():
    tracker = memory()
    for tick in range(5):
        assert tracker.observe([audio(1), audio(1)], tick) == [None, None]
    assert tracker.tracks == []
    mixed = np.concatenate([audio(1)[:RATE], audio(2)[:RATE]])
    assert tracker.observe([mixed], 6) == [None]
    assert tracker.tracks == []


def test_same_clip_does_not_count_as_independent_evidence():
    tracker = memory()
    for _ in range(10):
        assert tracker.observe([audio(1)], 1) == [None]
    assert tracker.tracks[0]['clips'] == 1
    assert tracker.observe([audio(1)], 2, learn=False) == [None]
    assert tracker.tracks[0]['clips'] == 1


def test_distinct_sessions_and_candidate_expiration():
    a, b = memory(), memory()
    for tick in range(3):
        ta = a.observe([audio(1)], tick)[0]
        tb = b.observe([audio(1)], tick)[0]
    assert ta['id'] != tb['id']
    c = memory()
    c.observe([audio(1)], 0)
    c.observe([audio(2)], 30)
    assert len(c.tracks) == 1


def test_joint_assignment_rejects_two_sources_claiming_one_track():
    tracker = memory()
    for tick in range(3):
        tracker.observe([audio(1)], tick)
    assert tracker.observe([audio(1), audio(1)], 4) == [None, None]


def test_analyzer_tracks_never_become_confirmed_speakers_and_detect_own_language():
    class Models:
        state = 'ready'
        settings = {'recover_overlap': True, 'track_overlap': True}
        def turns(self, samples): return [(0, 2, 'a'), (0, 2, 'b')]
        def separate(self, samples, count=2): return [audio(1), audio(2)]
        def status(self): return {'state':'ready'}
    calls = []
    def transcribe(samples, language):
        calls.append(language)
        return [{'start':0,'end':1,'text':'hello'}], ('en' if samples.mean() == 1 else 'fr')
    registry = SpeakerRegistry(lambda x: np.eye(3)[int(round(np.mean(x))) - 1])
    engine = SpeakerAnalyzer(Models(), registry, transcribe)
    for _ in range(3): result = engine.analyze(audio(1))
    assert all(s['voice_track'] and s['speaker'] is None and not s['accepted'] for s in result['segments'])
    assert {s['language'] for s in result['segments']} == {'en', 'fr'}
    assert all(language is None for language in calls)
    assert not registry.voices
    assert result['speaker'] is None
