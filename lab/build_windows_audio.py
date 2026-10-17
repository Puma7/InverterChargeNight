"""Build HA's official audio dependencies with short relative MSVC source paths.

The upstream setup.py uses absolute paths which produce excessively long
Windows object paths. Only that build-path expression is adjusted; Python/C++
implementation sources are unchanged. Requires Visual Studio C++ Build Tools.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

PACKAGES = {"pymicro-vad": "1.0.1", "pyspeex-noise": "1.0.2"}


def main():
    if sys.platform != "win32":
        raise SystemExit("This build-path workaround is only needed on Windows")
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is required")
    environment = dict(os.environ)
    environment.setdefault("UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "icn-audio-cache"))
    evidence = []
    with tempfile.TemporaryDirectory(prefix="icn-audio-") as temporary:
        root = Path(temporary)
        for package, version in PACKAGES.items():
            with urllib.request.urlopen(f"https://pypi.org/pypi/{package}/{version}/json", timeout=30) as response:
                metadata = json.load(response)
            source = next(item for item in metadata["urls"] if item["packagetype"] == "sdist")
            with urllib.request.urlopen(source["url"], timeout=60) as response:
                archive = response.read()
            digest = hashlib.sha256(archive).hexdigest()
            if digest != source["digests"]["sha256"]:
                raise RuntimeError(f"Source digest mismatch for {package}")
            destination = root / package
            destination.mkdir()
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
                tar.extractall(destination, filter="data")
            project = next(destination.glob("*/setup.py")).parent
            setup = project / "setup.py"
            original = setup.read_text(encoding="utf-8")
            expression = "_DIR = Path(__file__).parent"
            if original.count(expression) != 1:
                raise RuntimeError(f"Unexpected upstream build metadata for {package}")
            setup.write_text(original.replace(expression, '_DIR = Path(".")'), encoding="utf-8")
            print(f"Building {package} {version} from verified PyPI source", flush=True)
            subprocess.run([uv, "pip", "install", "--python", sys.executable, str(project)],
                           env=environment, check=True)
            evidence.append({"package": package, "version": version,
                             "source": source["url"], "source_sha256": digest,
                             "adjustment": "relative build paths in setup.py only"})
    reports = Path(__file__).resolve().parent / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "windows-audio-build.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
