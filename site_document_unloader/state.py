from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DownloadState:
    path: Path
    downloaded_urls: set[str] = field(default_factory=set)
    checksums: set[str] = field(default_factory=set)
    filenames: set[str] = field(default_factory=set)
    dirty: bool = False
    pending_changes: int = 0

    @classmethod
    def load(cls, path: Path) -> "DownloadState":
        if not path.exists():
            return cls(path=path)

        with path.open("r", encoding="utf-8") as file:
            payload = json.load(file) or {}

        return cls(
            path=path,
            downloaded_urls=set(payload.get("downloaded_urls", [])),
            checksums=set(payload.get("checksums", [])),
            filenames=set(payload.get("filenames", [])),
        )

    def has_url(self, url: str) -> bool:
        return url in self.downloaded_urls

    def has_checksum(self, checksum: str) -> bool:
        return checksum in self.checksums

    def has_filename(self, filename: str) -> bool:
        return filename.lower() in self.filenames

    def register(self, url: str, checksum: str, filename: str) -> None:
        self.downloaded_urls.add(url)
        self.checksums.add(checksum)
        self.filenames.add(filename.lower())
        self.dirty = True
        self.pending_changes += 1

    def mark_dirty(self) -> None:
        self.dirty = True
        self.pending_changes += 1

    def flush_if_needed(self, threshold: int | None = None) -> None:
        if not self.dirty:
            return
        if threshold is None or threshold <= 0 or self.pending_changes >= threshold:
            self.save()

    def save(self) -> None:
        if not self.dirty and self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "downloaded_urls": sorted(self.downloaded_urls),
            "checksums": sorted(self.checksums),
            "filenames": sorted(self.filenames),
        }
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        self.dirty = False
        self.pending_changes = 0
