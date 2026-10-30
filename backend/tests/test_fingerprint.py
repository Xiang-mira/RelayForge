from relayforge.workspaces.fingerprint import fingerprint, manifest


def test_input_fingerprint_is_content_based_and_excludes_runtime_artifacts(tmp_path):
    source = tmp_path / "calculator.py"
    source.write_bytes(b"original\n")
    tests = tmp_path / "test_calculator.py"
    tests.write_bytes(b"trusted tests\n")
    original = fingerprint(manifest(tmp_path))
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__/cached.pyc").write_bytes(b"cache")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/worker.log").write_bytes(b"log")
    assert fingerprint(manifest(tmp_path)) == original
    source.touch()
    assert fingerprint(manifest(tmp_path)) == original
    source.write_bytes(b"uncommitted edit\n")
    assert fingerprint(manifest(tmp_path)) != original
    source.write_bytes(b"original\n")
    tests.write_bytes(b"changed tests\n")
    assert fingerprint(manifest(tmp_path)) != original
    tests.write_bytes(b"trusted tests\n")
    (tmp_path / "helper.py").write_bytes(b"new imported code\n")
    assert fingerprint(manifest(tmp_path)) != original
