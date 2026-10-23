"""Private companion immutability and the actual shared auth writer lock."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from pipy_harness.native.auth_material import (
    RequestMaterialError,
    protect_request_material,
    read_request_material,
)
from pipy_harness.native.auth_store import AuthStore, read_credential_snapshot


def test_bound_random_refs_are_detached_immutable_and_private(tmp_path):
    path = tmp_path / "auth.json"
    binding = {"provider": "custom-name"}
    source = {"headers": {"Authorization": "fake-secret"}, "selector": "!fake-command"}
    ref = protect_request_material(path, binding, source)
    source["headers"]["Authorization"] = "changed"
    read = read_request_material(path, ref, binding)
    assert read["headers"]["Authorization"] == "fake-secret"
    read["headers"].clear()
    assert read_request_material(path, ref, binding)["headers"]
    assert "fake" not in json.dumps(ref.to_tree())
    assert (tmp_path / "auth.json.request-material").stat().st_mode & 0o777 == 0o600
    with pytest.raises(RequestMaterialError):
        read_request_material(path, ref, {"provider": "wrong"})
    with pytest.raises(RequestMaterialError):
        read_request_material(path, replace(ref, material_id="0" * 32), binding)
    with pytest.raises(RequestMaterialError):
        protect_request_material(path, binding, source, material_id=ref.material_id)
    assert not path.exists()  # No fake provider entries or changed auth schema.


def test_parallel_credential_rotation_and_material_capture_never_lose_either(tmp_path):
    path = tmp_path / "auth.json"

    def put(index):
        AuthStore(path).set(
            f"provider-{index}", {"type": "api_key", "key": f"fake-key-{index}"}
        )
        return protect_request_material(
            path, {"provider": index}, {"header": f"fake-header-{index}"}
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        refs = list(pool.map(put, range(24)))
    credentials = read_credential_snapshot(path)
    for index, ref in enumerate(refs):
        assert credentials.get(f"provider-{index}")["key"] == f"fake-key-{index}"
        assert read_request_material(path, ref, {"provider": index}) == {
            "header": f"fake-header-{index}"
        }
    assert len({ref.material_id for ref in refs}) == 24
    assert len({ref.store_id for ref in refs}) == 1


@pytest.mark.parametrize(
    "body",
    [
        "{bad-secret",
        '{"version":2,"store_id":"x","materials":{}}',
        '{"version":1,"version":1}',
    ],
)
def test_corrupt_newer_duplicate_private_document_never_becomes_empty(tmp_path, body):
    path = tmp_path / "auth.json"
    ref = protect_request_material(path, {}, {})
    (tmp_path / "auth.json.request-material").write_text(body)
    with pytest.raises(RequestMaterialError) as error:
        read_request_material(path, ref, {})
    assert "secret" not in str(error.value)
    with pytest.raises(RequestMaterialError):
        protect_request_material(path, {}, {})
    assert (tmp_path / "auth.json.request-material").read_text() == body


def test_immutable_private_numeric_material_does_not_use_python_numeric_equality(
    tmp_path,
):
    path = tmp_path / "auth.json"
    first = protect_request_material(path, {}, {"number": -0.0})
    with pytest.raises(RequestMaterialError):
        protect_request_material(
            path, {}, {"number": 0.0}, material_id=first.material_id
        )
    other = protect_request_material(path, {}, {"number": 1})
    with pytest.raises(RequestMaterialError):
        protect_request_material(
            path, {}, {"number": 1.0}, material_id=other.material_id
        )
