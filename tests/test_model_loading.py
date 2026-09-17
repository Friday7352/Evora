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
from evora_speakers import LocalModels, model_error_details


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
    assert set(torch.serialization.get_safe_globals()) == set(before)


def test_allowlist_is_removed_after_failed_load(monkeypatch, tmp_path):
    before = list(torch.serialization.get_safe_globals())
    def fail(*args, **kwargs):
        raise pickle.UnpicklingError("Unsupported checkpoint content")
    monkeypatch.setattr(Pipeline, "from_pretrained", fail)
    with pytest.raises(pickle.UnpicklingError):
        load_speaker_pipeline(tmp_path, "test-token")
    assert set(torch.serialization.get_safe_globals()) == set(before)


def test_import_error_reports_stage_and_redacts_credentials(monkeypatch, tmp_path):
    import evora_speakers
    def fail(*args, **kwargs):
        raise ImportError("cannot import name MissingComponent; hf_secret123; https://example.test/?token=private")
    monkeypatch.setattr(evora_speakers, "load_speaker_pipeline", fail)
    models = LocalModels(tmp_path, "cpu")
    models.settings["token"] = "hf_secret123"
    models._load()
    assert models.state == "error"
    assert "loading speaker detection" in models.error
    assert "MissingComponent" in models.error
    assert "hf_secret123" not in models.error and "private" not in models.error
    assert "dependency problem" in models.error


def test_non_hf_token_and_authorization_are_redacted():
    error = ImportError("custom-secret Authorization: another-secret Bearer other-secret")
    details = model_error_details(error, "custom-secret")
    assert all(secret not in details for secret in ("custom-secret", "another-secret", "other-secret"))
