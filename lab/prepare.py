"""Prepare a private HA config. Uses only the standard Python library."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile

LAB = Path(__file__).resolve().parent
REPO = LAB.parent


def copy_component(source: Path, destination: Path) -> None:
    """Replace only the exact managed component directory, never .storage."""
    managed_root = (LAB / "runtime" / "custom_components").resolve()
    target = destination.resolve()
    if target.parent != managed_root or destination.is_symlink():
        raise ValueError(f"Unsafe component destination: {destination}")
    if not source.is_dir():
        raise FileNotFoundError(source)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def revision(path: Path) -> str:
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()


def install_hacs(target: Path) -> dict:
    """Download an official release; no shell installer and no GitHub credentials."""
    request = urllib.request.Request("https://api.github.com/repos/hacs/integration/releases/latest",
                                     headers={"User-Agent": "InverterChargeNight-Lab"})
    with urllib.request.urlopen(request, timeout=30) as response:
        release = json.load(response)
    asset = next(asset for asset in release["assets"] if asset["name"] == "hacs.zip")
    with urllib.request.urlopen(asset["browser_download_url"], timeout=60) as response:
        archive = response.read()
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        for entry in zipped.infolist():
            resolved = (target / entry.filename).resolve()
            if not resolved.is_relative_to(target.resolve()):
                raise ValueError("Unsafe HACS archive path")
        zipped.extractall(target)
    return {"version": release["tag_name"], "sha256": hashlib.sha256(archive).hexdigest()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kostal-repo", type=Path, default=REPO.parent / "KostalKore")
    parser.add_argument("--hacs", action="store_true", help="Download official HACS release files")
    parser.add_argument("--refresh-config", action="store_true", help="Replace YAML from lab templates")
    args = parser.parse_args()
    runtime = LAB / "runtime"
    components = runtime / "custom_components"
    components.mkdir(parents=True, exist_ok=True)
    copy_component(REPO / "custom_components/inverter_charge_night", components / "inverter_charge_night")
    copy_component(LAB / "custom_components/icn_simulator", components / "icn_simulator")
    sources = {"InverterChargeNight": revision(REPO)}
    if args.kostal_repo.is_dir():
        copy_component(args.kostal_repo / "custom_components/kostal_kore", components / "kostal_kore")
        sources["KostalKore"] = revision(args.kostal_repo)
    for filename in ("configuration.yaml", "dashboard.yaml"):
        if args.refresh_config or not (runtime / filename).exists():
            shutil.copy2(LAB / filename, runtime / filename)
    if args.hacs:
        sources["HACS"] = install_hacs(components / "hacs")
    else:
        hacs_manifest = components / "hacs/manifest.json"
        prior_sources = runtime / "lab-sources.json"
        if hacs_manifest.exists():
            hacs_version = json.loads(hacs_manifest.read_text(encoding="utf-8"))["version"]
            previous = json.loads(prior_sources.read_text(encoding="utf-8")) if prior_sources.exists() else {}
            recorded_hacs = previous.get("HACS", {})
            sources["HACS"] = (recorded_hacs if recorded_hacs.get("version", "").lstrip("v") == hacs_version.lstrip("v")
                               else {"version": hacs_version})
    (runtime / "lab-sources.json").write_text(json.dumps(sources, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {runtime}")
    print(json.dumps(sources, indent=2))


if __name__ == "__main__":
    main()
