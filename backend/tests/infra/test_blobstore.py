"""T-210: BlobStore contract. Every implementation must pass this suite unchanged."""

import pytest

from autora.infra.blobstore import BlobNotFound, InvalidBlobKey, LocalFSBlobStore, validate_key


@pytest.fixture(params=["localfs"])
def store(request, tmp_path):
    if request.param == "localfs":
        return LocalFSBlobStore(tmp_path / "blobs")
    raise AssertionError(request.param)


async def test_put_get_roundtrip_and_overwrite(store):
    key = "companies/c1/runs/r1/steps/00000.json"
    assert await store.put(key, b'{"prompt": "hi"}') == key
    assert await store.get(key) == b'{"prompt": "hi"}'
    await store.put(key, b"v2")
    assert await store.get(key) == b"v2"


async def test_exists_and_delete(store):
    key = "a/b.txt"
    assert not await store.exists(key)
    await store.put(key, b"x")
    assert await store.exists(key)
    await store.delete(key)
    assert not await store.exists(key)
    await store.delete(key)  # deleting a missing blob is a no-op


async def test_missing_blob_raises(store):
    with pytest.raises(BlobNotFound):
        await store.get("nope/missing.json")


async def test_binary_data_preserved(store):
    data = bytes(range(256)) * 100
    await store.put("bin/data.bin", data)
    assert await store.get("bin/data.bin") == data


@pytest.mark.parametrize(
    "key",
    ["", "/abs/path", "../escape", "a/../../escape", "a//b", "a/./b", "a\\b", "a/b c", ".hidden"],
)
async def test_unsafe_keys_rejected(store, key):
    with pytest.raises(InvalidBlobKey):
        await store.put(key, b"x")
    with pytest.raises(InvalidBlobKey):
        validate_key(key)


async def test_localfs_writes_atomically(tmp_path):
    store = LocalFSBlobStore(tmp_path)
    await store.put("dir/file.json", b"data")
    leftovers = [p.name for p in (tmp_path / "dir").iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []
