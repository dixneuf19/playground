"""Recompute the integrity hashes of CDN scripts and stylesheets in the templates.

Renovate bumps the versions in those URLs (its html manager also fixes the
hashes of cdnjs ones; the jsDelivr ones need this). Run with --check in CI: it
fails if any hash doesn't match the file the CDN serves.

    uv run python scripts/sri.py [--check]
"""

import base64
import hashlib
import re
import sys
from pathlib import Path

import httpx2

TEMPLATES = Path(__file__).parent.parent / "karaoke" / "templates"
TAG = re.compile(r"<(?:script|link)\b[^>]*>", re.S)
URL = re.compile(r'\b(?:src|href)="(https://[^"]+)"')
INTEGRITY = re.compile(r'\bintegrity="([^"]*)"')


def sri(url: str, client: httpx2.Client) -> str:
    response = client.get(url, follow_redirects=True)
    response.raise_for_status()
    digest = hashlib.sha384(response.content).digest()
    return "sha384-" + base64.b64encode(digest).decode()


def fix_file(path: Path, client: httpx2.Client) -> tuple[str, int]:
    """The file's text with every integrity hash fixed, and how many were wrong."""
    wrong = 0

    def fix(tag: re.Match[str]) -> str:
        nonlocal wrong
        url, integrity = URL.search(tag[0]), INTEGRITY.search(tag[0])
        if url is None or integrity is None:
            return tag[0]
        expected = sri(url[1], client)
        if integrity[1] == expected:
            return tag[0]
        wrong += 1
        print(f"{path.name}: {url[1]}\n  has  {integrity[1]}\n  want {expected}")
        return tag[0].replace(integrity[0], f'integrity="{expected}"')

    return TAG.sub(fix, path.read_text()), wrong


def main(check: bool) -> int:
    wrong = 0
    with httpx2.Client(timeout=30) as client:
        for path in sorted(TEMPLATES.glob("*.html")):
            fixed, n = fix_file(path, client)
            wrong += n
            if n and not check:
                path.write_text(fixed)
    if wrong and check:
        print(f"{wrong} integrity hash(es) out of date: run `make sri`")
        return 1
    print(f"{wrong} integrity hash(es) updated" if wrong else "Integrity hashes OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(check="--check" in sys.argv[1:]))
