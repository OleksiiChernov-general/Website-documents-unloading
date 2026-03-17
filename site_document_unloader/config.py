from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(slots=True)
class AppConfig:
    urls: list[str]
    download_directory: Path
    state_file: Path
    log_file: Path
    headless: bool = True
    timeout_ms: int = 30000
    wait_until: str = "networkidle"
    max_depth: int = 2
    max_pages_per_domain: int = 50
    follow_subdomains: bool = False
    max_file_size_mb: int = 100
    group_by_domain: bool = True
    save_state_every_n_files: int | None = 20
    max_clicks_per_page: int = 25
    network_capture_enabled: bool = True
    post_click_rescan: bool = True
    allowed_extensions: tuple[str, ...] = (
        ".pdf",
        ".xls",
        ".xlsx",
        ".csv",
        ".doc",
        ".docx",
        ".zip",
    )


def load_config(config_path: str | Path) -> AppConfig:
    path = Path(config_path).expanduser().resolve()
    with path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file) or {}

    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a mapping.")

    urls = [str(item).strip() for item in raw.get("urls", []) if str(item).strip()]
    if not urls:
        raise ValueError("Config must contain at least one URL in 'urls'.")

    download_directory = _resolve_from_config(path, raw.get("download_directory", "./downloads"))
    state_file = _resolve_from_config(path, raw.get("state_file", "./state/download-state.json"))
    log_file = _resolve_from_config(path, raw.get("log_file", "./logs/downloader.log"))

    allowed_extensions = tuple(
        _normalize_extension(item) for item in raw.get("allowed_extensions", AppConfig.allowed_extensions)
    )

    return AppConfig(
        urls=urls,
        download_directory=download_directory,
        state_file=state_file,
        log_file=log_file,
        headless=bool(raw.get("headless", True)),
        timeout_ms=int(raw.get("timeout_ms", 30000)),
        wait_until=str(raw.get("wait_until", "networkidle")),
        max_depth=int(raw.get("max_depth", 2)),
        max_pages_per_domain=int(raw.get("max_pages_per_domain", 50)),
        follow_subdomains=bool(raw.get("follow_subdomains", False)),
        max_file_size_mb=int(raw.get("max_file_size_mb", 100)),
        group_by_domain=bool(raw.get("group_by_domain", True)),
        save_state_every_n_files=_optional_int(raw.get("save_state_every_n_files", 20)),
        max_clicks_per_page=int(raw.get("max_clicks_per_page", 25)),
        network_capture_enabled=bool(raw.get("network_capture_enabled", True)),
        post_click_rescan=bool(raw.get("post_click_rescan", True)),
        allowed_extensions=allowed_extensions,
    )


def _resolve_from_config(config_path: Path, value: Any) -> Path:
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    return (config_path.parent / path).resolve()


def _normalize_extension(value: Any) -> str:
    text = str(value).strip().lower()
    if not text:
        raise ValueError("Allowed extensions must not be empty.")
    return text if text.startswith(".") else f".{text}"


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None

    text = str(value).strip().lower()
    if text in {"", "none", "null"}:
        return None

    return int(value)
