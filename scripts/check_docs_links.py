"""Check the links of the documentation: files exist, anchors match a heading.

    python scripts/check_docs_links.py

Covers README.md, README.de.md and docs/**/*.md. Absolute links into this repository
(github.com/Puma7/InverterChargeNight/blob/main/... and raw.githubusercontent.com/...)
are checked against the working tree; other web links are not fetched.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_PREFIXES = ("https://github.com/Puma7/InverterChargeNight/blob/main/",
                 "https://raw.githubusercontent.com/Puma7/InverterChargeNight/main/")
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)|!\[[^\]]*\]\(([^)\s]+)\)|(?:src|srcset)=\"([^\"]+)\"")


def anchors(path: Path) -> set[str]:
    found = set()
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
        if in_code or not line.startswith("#"):
            continue
        text = line.lstrip("#").strip().lower()
        slug = re.sub(r"[^\w\- ]", "", text).replace(" ", "-")
        found.add(slug)
    return found


def main() -> int:
    pages = [ROOT / "README.md", ROOT / "README.de.md", *sorted((ROOT / "docs").rglob("*.md"))]
    pages = [p for p in pages if "history" not in p.parts]
    errors = []
    for page in pages:
        text = page.read_text(encoding="utf-8")
        text = re.sub(r"```.*?```", "", text, flags=re.S)
        for match in LINK.finditer(text):
            target = next(group for group in match.groups() if group)
            base = page.parent
            for prefix in REPO_PREFIXES:
                if target.startswith(prefix):
                    target, base = target[len(prefix):], ROOT
            if re.match(r"^[a-z]+:", target):
                continue
            file_part, _, anchor = target.partition("#")
            dest = (base / file_part).resolve() if file_part else page
            if not dest.exists():
                errors.append(f"{page.relative_to(ROOT)}: missing {target}")
                continue
            if anchor and dest.suffix == ".md" and anchor not in anchors(dest):
                errors.append(f"{page.relative_to(ROOT)}: no heading for #{anchor} in {dest.relative_to(ROOT)}")
    for error in errors:
        print(error)
    print(f"{len(pages)} pages, {len(errors)} problems")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
