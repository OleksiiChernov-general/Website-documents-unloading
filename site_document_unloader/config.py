from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_DOCUMENT_KEYWORDS = (
    "download",
    "downloads",
    "export",
    "file",
    "files",
    "pdf",
    "excel",
    "csv",
    "report",
    "reports",
    "brochure",
    "brochures",
    "datasheet",
    "catalog",
    "catalogue",
    "certificate",
    "certificates",
    "warranty",
    "manual",
    "manuals",
    "technical document",
    "specification",
    "documentation",
    "indir",
    "indirin",
    "yukle",
    "yükle",
    "dosya",
    "dosyalar",
    "rapor",
    "raporlar",
    "brosur",
    "broşür",
    "e-brosur",
    "e-broşür",
    "katalog",
    "kataloglar",
    "belge",
    "belgeler",
    "belgeleri",
    "kalite belgesi",
    "kalite belgeleri",
    "garanti",
    "garanti belgesi",
    "garanti belgeleri",
    "kullanim kilavuzu",
    "kullanım kılavuzu",
    "teknik dokuman",
    "teknik doküman",
    "sertifika",
    "скачать",
    "загрузить",
    "файл",
    "файлы",
    "документ",
    "документы",
    "брошюра",
    "каталог",
    "сертификат",
    "гарантия",
    "руководство",
    "инструкция",
    "техническая документация",
)

DEFAULT_SECTION_KEYWORDS = (
    "documents",
    "downloads",
    "resources",
    "publications",
    "library",
    "support",
    "brochures",
    "datasheets",
    "catalogs",
    "catalogues",
    "certificates",
    "manuals",
    "specifications",
    "documentation",
    "dokumanlar",
    "dokümanlar",
    "dosyalar",
    "kaynaklar",
    "kataloglar",
    "belgeler",
    "belgeleri",
    "sertifikalar",
    "kalite belgeleri",
    "garanti belgeleri",
    "документы",
    "ресурсы",
    "загрузки",
    "каталоги",
    "сертификаты",
    "инструкции",
    "техническая документация",
)

DEFAULT_POSITIVE_URL_PATTERNS = (
    "product",
    "products",
    "catalog",
    "catalogue",
    "document",
    "documents",
    "download",
    "downloads",
    "resource",
    "resources",
    "publication",
    "publications",
    "brochure",
    "datasheet",
    "certificate",
    "manual",
    "specification",
    "support",
    "library",
    "urun",
    "ürün",
    "urunler",
    "ürünler",
    "kategori",
    "katalog",
    "kataloglar",
    "dokuman",
    "doküman",
    "belge",
    "belgeler",
    "sertifika",
    "garanti",
    "документ",
    "документы",
    "каталог",
    "сертификат",
    "руководство",
    "инструкция",
)

DEFAULT_NEGATIVE_URL_PATTERNS = (
    "login",
    "logout",
    "signin",
    "sign-in",
    "register",
    "signup",
    "sign-up",
    "account",
    "profile",
    "privacy",
    "terms",
    "policy",
    "cookie",
    "basket",
    "cart",
    "checkout",
    "wishlist",
    "compare",
    "search",
    "/search",
    "/contact",
    "/news",
    "/blog",
    "/tags/",
    "/producttags/",
    "mailto:",
    "tel:",
    "javascript:",
    "facebook.com",
    "instagram.com",
    "linkedin.com",
    "youtube.com",
    "twitter.com",
    "x.com",
    "t.me",
    "wa.me",
    "whatsapp",
)

DEFAULT_BLOCKED_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".svg",
    ".ico",
    ".bmp",
    ".js",
    ".css",
    ".map",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".mp3",
    ".wav",
    ".ogg",
    ".m4a",
    ".mp4",
    ".webm",
    ".avi",
    ".mov",
    ".m4v",
)

DEFAULT_BLOCKED_CONTENT_TYPES = (
    "text/html",
    "image/",
    "text/css",
    "application/javascript",
    "text/javascript",
    "application/x-javascript",
    "font/",
    "application/font",
    "application/vnd.ms-fontobject",
    "audio/",
    "video/",
)

DEFAULT_LANGUAGE_SWITCHER_HINTS = (
    "lang",
    "language",
    "languages",
    "locale",
    "hreflang",
    "dil",
    "english",
    "turkish",
    "turkce",
    "türkçe",
    "russian",
    "russkiy",
    "русский",
    "english version",
    "turkce versiyon",
    "русская версия",
)

DEFAULT_PRODUCT_PAGE_HINTS = (
    "product",
    "products",
    "catalog",
    "catalogue",
    "model",
    "series",
    "sku",
    "part",
    "machine",
    "equipment",
    "device",
    "item",
    "urun",
    "ürün",
    "modeli",
    "seri",
    "cihaz",
    "ekipman",
    "товар",
    "модель",
    "серия",
    "оборудование",
    "изделие",
)


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
    documents_only: bool = True
    max_file_size_mb: int = 100
    group_by_domain: bool = True
    save_state_every_n_files: int | None = 20
    max_clicks_per_page: int = 25
    max_document_controls_per_page: int = 25
    max_language_variants_per_page: int = 2
    document_page_bonus_clicks: int = 8
    document_page_bonus_depth: int = 1
    max_links_enqueued_per_page: int = 20
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
    blocked_extensions: tuple[str, ...] = DEFAULT_BLOCKED_EXTENSIONS
    blocked_content_types: tuple[str, ...] = DEFAULT_BLOCKED_CONTENT_TYPES
    document_keywords: tuple[str, ...] = DEFAULT_DOCUMENT_KEYWORDS
    section_keywords: tuple[str, ...] = DEFAULT_SECTION_KEYWORDS
    positive_url_patterns: tuple[str, ...] = DEFAULT_POSITIVE_URL_PATTERNS
    negative_url_patterns: tuple[str, ...] = DEFAULT_NEGATIVE_URL_PATTERNS
    language_switcher_hints: tuple[str, ...] = DEFAULT_LANGUAGE_SWITCHER_HINTS
    product_page_hints: tuple[str, ...] = DEFAULT_PRODUCT_PAGE_HINTS


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

    max_clicks_per_page = int(raw.get("max_clicks_per_page", 25))

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
        documents_only=bool(raw.get("documents_only", True)),
        max_file_size_mb=int(raw.get("max_file_size_mb", 100)),
        group_by_domain=bool(raw.get("group_by_domain", True)),
        save_state_every_n_files=_optional_int(raw.get("save_state_every_n_files", 20)),
        max_clicks_per_page=max_clicks_per_page,
        max_document_controls_per_page=int(raw.get("max_document_controls_per_page", max_clicks_per_page)),
        max_language_variants_per_page=int(raw.get("max_language_variants_per_page", 2)),
        document_page_bonus_clicks=int(raw.get("document_page_bonus_clicks", 8)),
        document_page_bonus_depth=int(raw.get("document_page_bonus_depth", 1)),
        max_links_enqueued_per_page=int(raw.get("max_links_enqueued_per_page", 20)),
        network_capture_enabled=bool(raw.get("network_capture_enabled", True)),
        post_click_rescan=bool(raw.get("post_click_rescan", True)),
        allowed_extensions=allowed_extensions,
        blocked_extensions=tuple(
            _normalize_extension(item) for item in raw.get("blocked_extensions", DEFAULT_BLOCKED_EXTENSIONS)
        ),
        blocked_content_types=_tuple_from_config(raw.get("blocked_content_types"), DEFAULT_BLOCKED_CONTENT_TYPES),
        document_keywords=_tuple_from_config(raw.get("document_keywords"), DEFAULT_DOCUMENT_KEYWORDS),
        section_keywords=_tuple_from_config(raw.get("section_keywords"), DEFAULT_SECTION_KEYWORDS),
        positive_url_patterns=_tuple_from_config(raw.get("positive_url_patterns"), DEFAULT_POSITIVE_URL_PATTERNS),
        negative_url_patterns=_tuple_from_config(raw.get("negative_url_patterns"), DEFAULT_NEGATIVE_URL_PATTERNS),
        language_switcher_hints=_tuple_from_config(
            raw.get("language_switcher_hints"),
            DEFAULT_LANGUAGE_SWITCHER_HINTS,
        ),
        product_page_hints=_tuple_from_config(raw.get("product_page_hints"), DEFAULT_PRODUCT_PAGE_HINTS),
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


def _tuple_from_config(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list):
        raise ValueError("Keyword and pattern config values must be lists.")
    normalized = tuple(str(item).strip() for item in value if str(item).strip())
    if not normalized:
        raise ValueError("Configured keyword and pattern lists must not be empty.")
    return normalized
