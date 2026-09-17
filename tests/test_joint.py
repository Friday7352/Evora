import copy
import io
import re
import time
from types import SimpleNamespace

import numpy as np
import pytest

from evora_joint import JointConversation, unpack_joint_result, analyze_joint, RATE
from evora_speakers import SpeakerAnalyzer, SpeakerRegistry


def test_overlapping_voices_keep_identity_when_model_swaps_channel_order():
    memory = JointConversation()
    first = memory.link([(0,3,"a"),(0,3,"b")],0,3,{"a":np.array([1.,0]),"b":np.array([0.,1])})
    assert memory.public(first['a']) is None
    memory.turns = [(0,3,first['a']),(0,3,first['b'])]
    second = memory.link([(0,6,"b"),(0,6,"a")],3,6,{"b":np.array([1.,0]),"a":np.array([0.,1])})
    assert first['a'] == second['b'] and first['b'] == second['a']
    assert memory.public(second['b'])['verified'] is False


def test_one_word_does_not_make_a_public_voice_and_context_is_not_recounted():
    m = JointConversation()
    ids = m.link([(0,.6,'a')],0,.6)
    m.turns = [(0,.6,ids['a'])]
    for _ in range(5):
        m.link([(0,.6,'a')],.6,.6)
    assert m.public(ids['a']) is None
    assert m.tracks[ids['a']]['seconds'] == pytest.approx(.6)


def test_duplicate_sources_are_not_two_people():
    m = JointConversation()
    assert m.link([(0,3,'a'),(0,3,'b')],0,3,{'a':np.array([1.,0]),'b':np.array([1.,0])}) == {}


def test_ambiguous_shared_timing_does_not_merge_two_speakers():
    m = JointConversation()
    ids = m.link([(0,3,'a'),(0,3,'b')],0,3)
    m.turns = [(0,3,ids['a']),(0,3,ids['b'])]
    assert m.link([(0,6,'x'),(0,6,'y')],3,6) == {}


def test_returning_voice_matches_session_anchor_without_shared_timing():
    m = JointConversation()
    ids = m.link([(0,3,'a')],0,3,{'a':np.array([1.,0])})
    returned = m.link([(0,3,'z')],0,3,{'z':np.array([1.,0])})
    assert returned['z'] == ids['a']


def test_joint_failed_inference_does_not_consume_context():
    m = JointConversation()
    before = copy.deepcopy(m.__dict__)
    def fail(_):
        raise RuntimeError('failed')
    with pytest.raises(RuntimeError):
        m.analyze(fail,np.ones(RATE))
    assert m.tick == before['tick'] and len(m.audio) == 0


def test_separated_waveforms_use_model_label_order_and_sample_offset():
    from pyannote.core import Annotation, Segment, SlidingWindow, SlidingWindowFeature
    annotation = Annotation()
    annotation[Segment(0,1)] = 'z'
    annotation[Segment(1,2)] = 'a'
    data = np.array([[1.,2.],[3.,4.]],dtype=np.float32)
    source = SlidingWindowFeature(data,SlidingWindow(start=1/RATE,duration=1/RATE,step=1/RATE))
    _, waves = unpack_joint_result((annotation,source),4)
    np.testing.assert_array_equal(waves['a'],[0,1,3,0])
    np.testing.assert_array_equal(waves['z'],[0,2,4,0])
    with pytest.raises(ValueError):
        unpack_joint_result((annotation,SimpleNamespace(data=np.ones((2,1)))),4)


def test_joint_sources_never_enroll_or_pass_strict_filters():
    class Models:
        state='ready'
        settings={'engine':'pixit'}
        def joint(self,audio):
            duration=len(audio)/RATE
            return [(0,duration,'a')],{'a':audio}
        def status(self): return {'state':'ready'}
    registry=SpeakerRegistry(lambda _:np.array([1.,0]))
    registry.enroll(np.ones(3*RATE),'Alice')
    engine=SpeakerAnalyzer(Models(),registry,lambda a,l:([{'start':0,'end':1,'text':' Hello'}],'en'))
    result=engine.analyze(np.ones(3*RATE))
    assert result['segments'][0]['speaker']['name']=='Alice'
    assert not result['segments'][0]['accepted']
    assert len(registry.voices[1]['samples'])==1


def test_joint_model_failure_keeps_mixed_text_and_reports_fallback():
    class Models:
        state='ready'
        settings={'engine':'pixit'}
        def joint(self,audio): raise RuntimeError('out of memory')
        def status(self): return {'state':'ready','notice':getattr(self,'notice','')}
    engine=SpeakerAnalyzer(Models(),SpeakerRegistry(lambda _: [1,0]),lambda a,l:([{'start':0,'end':1,'text':'hello'}],'en'))
    result=engine.analyze(np.ones(3*RATE))
    assert result['text']=='hello' and result['speaker'] is None
    assert 'failed' in result['speaker_status']['notice']


def test_bundled_totatonet_runs_without_training_dependencies(monkeypatch):
    import torch
    from evora_speakers import prepare_speechbrain_imports
    prepare_speechbrain_imports()
    from evora_joint import prepare_joint_imports
    prepare_joint_imports()
    from pyannote.audio.models.separation.ToTaToNet import ToTaToNet
    from pyannote.audio.core.task import Specifications,Problem,Resolution
    model=ToTaToNet(use_wavlm=False,dprnn={'n_repeats':1,'hid_size':8,'bn_chan':8})
    model.specifications=(Specifications(problem=Problem.MULTI_LABEL_CLASSIFICATION,resolution=Resolution.FRAME,duration=5,classes=['a','b','c']),)
    model.build()
    model.eval()
    with torch.inference_mode():
        activity,sources=model(torch.randn(1,1,RATE))
    assert activity.shape[-1]==3 and sources.shape==(1,RATE,3)
    assert torch.isfinite(sources).all()


def test_recording_check_is_private_and_does_not_modify_saved_voices(tmp_path):
    from flask import Flask
    from pathlib import Path
    from threading import Lock
    from evora_api import register_routes
    class Models:
        state='ready'
        settings={'recover_overlap':False}
        def turns(self,a):return [(0,len(a)/RATE,'a')]
        def status(self):return {'state':'ready'}
    app=Flask(__name__,template_folder=str(Path(__file__).parents[1]/'templates'))
    registry=SpeakerRegistry(lambda _: [1,0],tmp_path/'voices.json')
    register_routes(app,Models(),registry,lambda a,l:([{'start':0,'end':1,'text':'hello'}],'en'),lambda f,**kw:np.load(f),Lock())
    c=app.test_client()
    assert c.get('/speaker-check').status_code==403
    nonce=re.search(r'const nonce="([^"]+)"',c.get('/speaker-settings').text).group(1)
    headers={'X-Evora-Settings':nonce}
    data=io.BytesIO();np.save(data,np.ones(5*RATE));data.seek(0)
    assert c.post('/speaker-check',data={'file':(data,'audio.wav')},headers=headers).status_code==202
    for _ in range(100):
        result=c.get('/speaker-check',headers=headers).json
        if result['state']!='running':break
        time.sleep(.01)
    assert result['state']=='complete' and result['segments'][0]['text']=='hello'
    assert not registry.voices and not registry.path.exists()
