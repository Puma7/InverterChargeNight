"""Install a pinned official evcc release with its published SHA256 digest."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import platform
import tarfile
import urllib.request
import zipfile

VERSION = "0.317.1"
LAB = Path(__file__).resolve().parent


def install(root: Path | None = None) -> Path:
    root = root or LAB / "runtime" / "evcc"
    root.mkdir(parents=True, exist_ok=True)
    target = root / ("evcc.exe" if platform.system() == "Windows" else "evcc")
    arch = {"AMD64": "amd64", "x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]
    system = platform.system().lower()
    suffix = "zip" if system == "windows" else "tar.gz"
    name = f"evcc_{VERSION}_{system}-{arch}.{suffix}"
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ICN-lab-evcc-installer"}
    # CI runners share the unauthenticated API quota. Use the job's read-only
    # token for this fixed GitHub API endpoint only, never for asset/CDN URLs.
    if token := os.environ.get("GH_TOKEN"):
        headers["Authorization"] = "Bearer " + token
    metadata = urllib.request.Request(
        f"https://api.github.com/repos/evcc-io/evcc/releases/tags/{VERSION}", headers=headers)
    release = json.load(urllib.request.urlopen(metadata, timeout=30))
    asset = next(a for a in release["assets"] if a["name"] == name)
    checksum = next(a for a in release["assets"] if a["name"] == "checksums.txt")
    lines = urllib.request.urlopen(checksum["browser_download_url"], timeout=30).read().decode().splitlines()
    expected = next(line.split()[0] for line in lines if line.split()[-1] == name)
    data = urllib.request.urlopen(asset["browser_download_url"], timeout=120).read()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected or asset.get("digest", "sha256:" + actual) != "sha256:" + actual:
        raise RuntimeError("Official evcc release checksum mismatch")
    if suffix == "zip":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            binary = archive.read(next(n for n in archive.namelist() if Path(n).name == target.name))
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            member = next(m for m in archive.getmembers() if Path(m.name).name == target.name and m.isfile())
            binary = archive.extractfile(member).read()
    target.write_bytes(binary)
    target.chmod(0o755)
    (root / "source.json").write_text(json.dumps({"version": VERSION,
        "url": asset["browser_download_url"], "sha256": actual}, indent=2) + "\n")
    return target


if __name__ == "__main__":
    print(install())
