"""Exercise real PyTorch deserialization without downloading gated weights."""
import io
import pickle

import pytest
import torch
import torch._dynamo
from pyannote.audio import Pipeline
from pyannote.audio.core.task import Specifications, Problem, Resolution
from torch.torch_version import TorchVersion

from evora_speakers import load_speaker_pipeline, MODEL


def test_pyannote_metadata_loads_with_scoped_allowlist(monkeypatch, tmp_path):
    checkpoint = io.BytesIO()
    torch.save({"version": TorchVersion("2.8.0"), "specifications": Specifications(
        problem=Problem.MONO_LABEL_CLASSIFICATION, resolution=Resolution.FRAME,
        duration=10.0), "weights": torch.ones(2)}, checkpoint)
    checkpoint.seek(0)
    with pytest.raises(pickle.UnpicklingError):
        torch.load(checkpoint, weights_only=True)
    before = list(torch.serialization.get_safe_globals())
    def load(model, **kwargs):
        assert model == MODEL
        assert kwargs == {"token": "test-token", "cache_dir": str(tmp_path)}
        checkpoint.seek(0)
        return torch.load(checkpoint, weights_only=True)
    monkeypatch.setattr(Pipeline, "from_pretrained", load)
    loaded = load_speaker_pipeline(tmp_path, "test-token")
    assert loaded["specifications"].resolution == Resolution.FRAME
    assert torch.equal(loaded["weights"], torch.ones(2))
    assert torch.serialization.get_safe_globals() == before


def test_allowlist_is_removed_after_failed_load(monkeypatch, tmp_path):
    before = list(torch.serialization.get_safe_globals())
    def fail(*args, **kwargs):
        raise pickle.UnpicklingError("Unsupported checkpoint content")
    monkeypatch.setattr(Pipeline, "from_pretrained", fail)
    with pytest.raises(pickle.UnpicklingError):
        load_speaker_pipeline(tmp_path, "test-token")
    assert torch.serialization.get_safe_globals() == before
