"""Fresh-process checks reproduce the Windows lazy-import failure."""
import subprocess
import sys
from pathlib import Path


def test_windows_inspection_does_not_load_optional_k2():
    code = r'''
from evora_speakers import prepare_speechbrain_imports
prepare_speechbrain_imports()
prepare_speechbrain_imports()  # repeated setup remains safe
import inspect
import sys
from speechbrain.utils.importutils import LazyModule
lazy = LazyModule('missing', 'evora_deliberately_missing_dependency', None)
scope = {'lazy': lazy}
exec(compile("result = hasattr(lazy, '__file__')", r'C:\Python311\Lib\inspect.py', 'exec'), scope)
assert scope['result'] is False
assert lazy.lazy_module is None
try:
    lazy.explicit_access
except ImportError:
    pass
else:
    raise AssertionError('Explicit missing dependency must still fail')
# The real inspect scan failed on Windows before the fix.
inspect.getmodule(compile('pass', 'evora_unique_inspection_test.py', 'exec'))
assert 'speechbrain.integrations.k2_fsa' not in sys.modules
from speechbrain.inference.speaker import EncoderClassifier
from speechbrain.inference.separation import SepformerSeparation
from pyannote.audio import Pipeline
print('Inspection and model imports passed')
'''
    result = subprocess.run([sys.executable, '-c', code], cwd=Path(__file__).parents[1],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Inspection and model imports passed' in result.stdout
