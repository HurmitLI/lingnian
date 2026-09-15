import hashlib
import importlib.util
import io
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/download_klein_authorized.py"
spec = importlib.util.spec_from_file_location("authorized_download", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(body))}


def test_only_exact_authorized_manifest(tmp_path):
    data = json.loads((SCRIPT.parents[1] / "examples/flux-klein-4b-model-candidate.json").read_text())
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data))
    module.check_manifest(path)
    for field, value in (("download_authorized", False), ("revision", "main"), ("total_bytes", 1)):
        changed = dict(data, **{field: value})
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError):
            module.check_manifest(path)


def test_real_file_digest_verified_and_reuse_without_network(tmp_path):
    body = b"fixture, not model data"
    path = tmp_path / "model.safetensors"
    sha = hashlib.sha256(body).hexdigest()
    events = []
    module.fetch_file("https://example.test", path, len(body), sha,
                      lambda *args: events.append(args), opener=lambda *a, **k: Response(body))
    assert path.read_bytes() == body
    assert events[-1] == ("verified", len(body))
    module.fetch_file("https://example.test", path, len(body), sha, lambda *a: None,
                      opener=lambda *a, **k: pytest.fail("must not download again"))


def test_resume_requires_exact_range_and_hash(tmp_path):
    path = tmp_path / "model"
    part = tmp_path / "model.lingnian.part"
    part.write_bytes(b"abc")
    def opener(request, **kwargs):
        assert request.get_header("Range") == "bytes=3-"
        return Response(b"def", 206, {"Content-Range": "bytes 3-5/6", "Content-Length": "3"})
    module.fetch_file("https://example.test", path, 6, hashlib.sha256(b"abcdef").hexdigest(),
                      lambda *a: None, opener=opener)
    assert path.read_bytes() == b"abcdef"
    assert not part.exists()


@pytest.mark.parametrize("status,headers", [(200, {}), (206, {"Content-Range": "bytes 0-5/6"})])
def test_bad_range_preserves_partial(tmp_path, status, headers):
    path = tmp_path / "model"
    part = tmp_path / "model.lingnian.part"
    part.write_bytes(b"abc")
    with pytest.raises(ValueError):
        module.fetch_file("https://example.test", path, 6, "x", lambda *a: None,
                          opener=lambda *a, **k: Response(b"abcdef", status, headers))
    assert part.read_bytes() == b"abc"
    assert not path.exists()


def test_bad_checksum_never_installed_and_existing_never_overwritten(tmp_path):
    path = tmp_path / "model"
    with pytest.raises(ValueError):
        module.fetch_file("https://example.test", path, 3, "0" * 64, lambda *a: None,
                          opener=lambda *a, **k: Response(b"abc"))
    assert not path.exists()
    path.write_bytes(b"previous user's file")
    with pytest.raises(ValueError):
        module.fetch_file("https://example.test", path, 3, "0" * 64, lambda *a: None)
    assert path.read_bytes() == b"previous user's file"


def test_symlink_cannot_overwrite_outside(tmp_path):
    original = tmp_path / "original"
    original.write_bytes(b"keep")
    path = tmp_path / "model"
    path.with_name("model.lingnian.part").symlink_to(original)
    with pytest.raises(ValueError):
        module.fetch_file("https://example.test", path, 10, "0" * 64, lambda *a: None)
    assert original.read_bytes() == b"keep"
