from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Build the terms at runtime so this file itself does not contain the private
# strings as plain literals.
FORBIDDEN = [
    "moon" + "web.lan",
    "192." + "168.",
    "fritz" + "8254",
    "direct" + "0r",
    "Server" + "raum",
    "Wohn" + "zimmer",
    "4. " + "OG",
    "AVM" + "1220E",
    "fritz" + "power",
]

TEXT_SUFFIXES = {".py", ".html", ".css", ".js", ".md", ".yml", ".yaml", ".toml", ".conf", ".sh", ".example"}


def _files_to_scan() -> list[Path]:
    files = [
        ROOT / "README.md",
        ROOT / ".env.example",
        ROOT / "Dockerfile",
        ROOT / "docker-compose.example.yml",
        ROOT / "coredns" / "Corefile.example",
        ROOT / "config" / "device-names.conf.example",
        ROOT / "pyproject.toml",
        ROOT / "requirements.txt",
        ROOT / "requirements-dev.txt",
        ROOT / "fetch.sh",
        ROOT / "web.sh",
    ]
    for directory in (ROOT / "fritz_lan_dns", ROOT / "tests"):
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if path.name == "test_privacy.py":
                continue
            if "__pycache__" in path.parts:
                continue
            if path.suffix in TEXT_SUFFIXES or path.name == ".env.example":
                files.append(path)
    return files


def test_no_private_strings_in_public_files() -> None:
    problems: list[str] = []
    for path in _files_to_scan():
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for term in FORBIDDEN:
            if term in text:
                problems.append(f"{path.relative_to(ROOT)} enthält {term!r}")
    assert not problems, "\n".join(problems)
