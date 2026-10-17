"""Keep credentials on GitHub metadata and reject unverified release bytes."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile

import pytest

SPEC = importlib.util.spec_from_file_location("icn_evcc_installer", Path(__file__).parents[1] / "install_evcc.py")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)


@pytest.mark.parametrize("authenticated", [False, True])
def test_download_authentication_and_integrity(tmp_path, monkeypatch, authenticated):
    monkeypatch.setattr(INSTALLER.platform, "system", lambda: "Windows")
    monkeypatch.setattr(INSTALLER.platform, "machine", lambda: "AMD64")
    if authenticated:
        monkeypatch.setenv("GH_TOKEN", "test-job-token")
    else:
        monkeypatch.delenv("GH_TOKEN", raising=False)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("evcc.exe", b"verified-test-binary")
    archive_bytes = buffer.getvalue()
    checksum = hashlib.sha256(archive_bytes).hexdigest()
    name = f"evcc_{INSTALLER.VERSION}_windows-amd64.zip"
    base = f"https://github.com/evcc-io/evcc/releases/download/{INSTALLER.VERSION}/"
    api = f"https://api.github.com/repos/evcc-io/evcc/releases/tags/{INSTALLER.VERSION}"
    metadata = {"assets": [
        {"name": name, "browser_download_url": base + name, "digest": "sha256:" + checksum},
        {"name": "checksums.txt", "browser_download_url": base + "checksums.txt"},
    ]}
    downloads = {base + name: archive_bytes,
                 base + "checksums.txt": f"{checksum}  {name}\n".encode()}
    seen = []

    def download(request, **kwargs):
        url = request if isinstance(request, str) else request.full_url
        auth = None if isinstance(request, str) else request.get_header("Authorization")
        seen.append((url, auth))
        return io.BytesIO(json.dumps(metadata).encode() if url == api else downloads[url])

    monkeypatch.setattr(INSTALLER.urllib.request, "urlopen", download)
    target = INSTALLER.install(tmp_path)
    assert target.read_bytes() == b"verified-test-binary"
    assert seen == [(api, "Bearer test-job-token" if authenticated else None),
                    (base + "checksums.txt", None), (base + name, None)]
    downloads[base + name] = b"tampered-archive"
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        INSTALLER.install(tmp_path)
    assert target.read_bytes() == b"verified-test-binary"
