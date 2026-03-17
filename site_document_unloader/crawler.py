from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import posixpath
import re
from collections import Counter, deque
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlsplit, urlunsplit

from playwright.sync_api import BrowserContext, Error, Page, Response, TimeoutError, sync_playwright

from .config import AppConfig
from .state import DownloadState

LOGGER = logging.getLogger(__name__)
DOWNLOAD_TEXT_RE = re.compile(
    r"(download|export|xlsx|excel|csv|pdf|docx|zip|report|brochure|datasheet|\u0441\u043a\u0430\u0447\u0430\u0442\u044c|\u044d\u043a\u0441\u043f\u043e\u0440\u0442)",
    re.IGNORECASE,
)
AVOID_CLICK_TEXT_RE = re.compile(
    r"(delete|remove|cancel|logout|sign out|unsubscribe|\u0443\u0434\u0430\u043b\u0438\u0442\u044c|\u043e\u0442\u043c\u0435\u043d\u0430)",
    re.IGNORECASE,
)
HTML_SNIPPET_RE = re.compile(rb"<!doctype html|<html\b|<body\b|<head\b", re.IGNORECASE)
DOCUMENT_CONTENT_TYPE_RE = re.compile(
    r"^(application/pdf|application/vnd\.ms-excel|application/vnd\.openxmlformats-officedocument\."
    r"(spreadsheetml\.sheet|wordprocessingml\.document)|text/csv|application/msword|application/zip)$",
    re.IGNORECASE,
)


class DocumentCrawler:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.state = DownloadState.load(config.state_file)
        self.config.download_directory.mkdir(parents=True, exist_ok=True)
        self._max_file_size_bytes = max(1, self.config.max_file_size_mb) * 1024 * 1024

    def run(self) -> None:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.config.headless)
            context = browser.new_context(accept_downloads=True)
            try:
                for root_url in self.config.urls:
                    self._crawl_domain(context, root_url)
                    self.state.save()
            finally:
                self.state.save()
                context.close()
                browser.close()

    def _crawl_domain(self, context: BrowserContext, root_url: str) -> None:
        queue: deque[tuple[str, int]] = deque([(root_url, 0)])
        queued: set[str] = {_normalize_url(root_url)}
        visited: set[str] = set()
        page_counts: Counter[str] = Counter()
        base_netloc = urlparse(root_url).netloc.lower()

        while queue:
            current_url, depth = queue.popleft()
            normalized_url = _normalize_url(current_url)

            if normalized_url in visited or depth > self.config.max_depth:
                continue

            if not self._is_allowed_domain(base_netloc, normalized_url):
                continue

            if page_counts[base_netloc] >= self.config.max_pages_per_domain:
                LOGGER.info("Page limit reached for domain: %s", base_netloc)
                break

            visited.add(normalized_url)
            page_counts[base_netloc] += 1
            LOGGER.info("page_open page_url=%s depth=%s", normalized_url, depth)

            page = context.new_page()
            page_network_seen: set[str] = set()
            try:
                page.set_default_timeout(self.config.timeout_ms)
                if self.config.network_capture_enabled:
                    page.on(
                        "response",
                        lambda response: self._handle_network_response(
                            response,
                            normalized_url,
                            page_network_seen,
                        ),
                    )

                page.goto(normalized_url, wait_until=self.config.wait_until)
                page.wait_for_load_state("domcontentloaded")

                self._download_discovered_links(context, page, normalized_url, "dom_link")
                discovered_links = set(self._extract_internal_links(page, normalized_url, base_netloc))
                clicked_links = self._try_click_download_controls(
                    context,
                    page,
                    normalized_url,
                    base_netloc,
                )

                for next_url in discovered_links | clicked_links:
                    if next_url not in visited and next_url not in queued:
                        queue.append((next_url, depth + 1))
                        queued.add(next_url)
            except TimeoutError:
                LOGGER.exception("Timeout while opening page: %s", normalized_url)
            except Error:
                LOGGER.exception("Playwright failed for page: %s", normalized_url)
            except Exception:
                LOGGER.exception("Unexpected failure for page: %s", normalized_url)
            finally:
                self.state.flush_if_needed(self.config.save_state_every_n_files)
                self.state.save()
                page.close()

        self.state.save()

    def _download_discovered_links(
        self,
        context: BrowserContext,
        page: Page,
        page_url: str,
        discovery_method: str,
    ) -> None:
        for document_url in self._extract_document_links(page, page_url):
            self._download_url(context, document_url, page_url, discovery_method)

    def _extract_document_links(self, page: Page, page_url: str) -> list[str]:
        script = """
() => {
  const values = new Set();
  const push = (value) => {
    if (typeof value !== "string") return;
    const trimmed = value.trim();
    if (trimmed) values.add(trimmed);
  };

  document.querySelectorAll("[href],[src]").forEach((node) => {
    push(node.getAttribute("href"));
    push(node.getAttribute("src"));
  });

  document.querySelectorAll("*").forEach((node) => {
    for (const attr of node.getAttributeNames()) {
      if (attr.startsWith("data-") || attr.startsWith("on")) {
        push(node.getAttribute(attr));
      }
    }
  });

  return Array.from(values);
}
"""
        raw_values = page.evaluate(script)
        links: list[str] = []

        for raw in raw_values:
            for candidate in _extract_urls_from_text(str(raw), page_url):
                if self._is_document_url(candidate):
                    links.append(candidate)

        return list(dict.fromkeys(links))

    def _extract_internal_links(self, page: Page, page_url: str, base_netloc: str) -> Iterable[str]:
        hrefs = page.eval_on_selector_all(
            "a[href]",
            """nodes => nodes.map(node => node.href).filter(Boolean)""",
        )
        for href in hrefs:
            url = _normalize_url(urljoin(page_url, href))
            if self._is_allowed_domain(base_netloc, url) and not self._is_document_url(url):
                yield url

    def _try_click_download_controls(
        self,
        context: BrowserContext,
        page: Page,
        page_url: str,
        base_netloc: str,
    ) -> set[str]:
        selectors = ["a", "button", "[role='button']", "input[type='button']", "input[type='submit']"]
        clicked_keys: set[str] = set()
        next_links: set[str] = set()

        for selector, _score, label, element in self._iter_click_candidates(page, selectors):
            if len(clicked_keys) >= self.config.max_clicks_per_page:
                LOGGER.info("click_limit_reached page_url=%s limit=%s", page_url, self.config.max_clicks_per_page)
                break

            element_key = self._build_element_key(element, selector, label)
            if element_key in clicked_keys:
                continue

            clicked_keys.add(element_key)
            LOGGER.info("click_attempt page_url=%s discovery_method=click_download label=%s", page_url, _shorten(label))

            try:
                with page.expect_download(timeout=3000) as download_info:
                    element.click(timeout=2000, force=True)
                download = download_info.value
                self._save_download(download.url, download.suggested_filename, download.path(), page_url, "click_download")
            except TimeoutError:
                pass
            except Error:
                LOGGER.debug("click_failed page_url=%s label=%s", page_url, _shorten(label), exc_info=True)
                continue

            self._settle_after_click(page)

            if self.config.post_click_rescan:
                self._download_discovered_links(context, page, page_url, "dom_link")
                for next_url in self._extract_internal_links(page, page_url, base_netloc):
                    next_links.add(next_url)

        return next_links

    def _iter_click_candidates(self, page: Page, selectors: list[str]) -> Iterable[tuple[str, int, str, object]]:
        candidates: list[tuple[str, int, str, object]] = []

        for selector in selectors:
            elements = page.locator(selector)
            count = min(elements.count(), 75)
            for index in range(count):
                element = elements.nth(index)
                try:
                    label = self._extract_element_label(element)
                except Error:
                    continue

                if not label or not DOWNLOAD_TEXT_RE.search(label):
                    continue

                if AVOID_CLICK_TEXT_RE.search(label):
                    continue

                score = _click_priority(label)
                candidates.append((selector, score, label, element))

        candidates.sort(key=lambda item: item[1], reverse=True)
        for selector, score, label, element in candidates[: self.config.max_clicks_per_page]:
            yield selector, score, label, element

    def _extract_element_label(self, element: object) -> str:
        locator = element
        parts = [
            locator.inner_text(timeout=500).strip() if locator.is_visible() else "",
            locator.get_attribute("aria-label") or "",
            locator.get_attribute("title") or "",
            locator.get_attribute("value") or "",
            locator.get_attribute("href") or "",
        ]
        return " ".join(part for part in parts if part).strip()

    def _build_element_key(self, element: object, selector: str, label: str) -> str:
        locator = element
        try:
            href = locator.get_attribute("href") or ""
            element_id = locator.get_attribute("id") or ""
            name = locator.get_attribute("name") or ""
            data_test = locator.get_attribute("data-testid") or ""
        except Error:
            href = ""
            element_id = ""
            name = ""
            data_test = ""

        return "|".join([selector, element_id, name, data_test, href, label[:200]])

    def _settle_after_click(self, page: Page) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=min(self.config.timeout_ms, 3000))
        except TimeoutError:
            pass

        try:
            page.wait_for_timeout(750)
        except Error:
            pass

    def _handle_network_response(
        self,
        response: Response,
        page_url: str,
        page_network_seen: set[str],
    ) -> None:
        try:
            document_url = _normalize_url(response.url)
            if document_url in page_network_seen:
                return

            headers = _response_headers(response)
            if not self._is_document_response_candidate(document_url, headers):
                return

            page_network_seen.add(document_url)
            self._save_response_body(response, page_url, "network_response")
        except Error:
            LOGGER.debug("network_response_failed page_url=%s response_url=%s", page_url, response.url, exc_info=True)
        except Exception:
            LOGGER.exception("Unexpected failure while processing network response page_url=%s", page_url)

    def _download_url(
        self,
        context: BrowserContext,
        url: str,
        page_url: str,
        discovery_method: str,
    ) -> None:
        normalized_url = _normalize_url(url)
        if self.state.has_url(normalized_url):
            self._log_document_event(
                page_url=page_url,
                document_url=normalized_url,
                discovery_method=discovery_method,
                result="skipped",
                reason="duplicate_url",
            )
            return

        LOGGER.info("download_attempt page_url=%s document_url=%s discovery_method=%s", page_url, normalized_url, discovery_method)
        try:
            response = context.request.get(normalized_url, timeout=self.config.timeout_ms)
            headers = _response_headers(response)
            content_length = _parse_content_length(headers)
            if content_length is not None and content_length > self._max_file_size_bytes:
                self._log_document_event(
                    page_url=page_url,
                    document_url=normalized_url,
                    discovery_method=discovery_method,
                    result="rejected",
                    reason="rejected_size_limit",
                    content_type=_content_type(headers),
                    size=content_length,
                )
                return

            if not response.ok:
                self._log_document_event(
                    page_url=page_url,
                    document_url=normalized_url,
                    discovery_method=discovery_method,
                    result="rejected",
                    reason=f"http_status_{response.status}",
                    content_type=_content_type(headers),
                )
                return

            body = response.body()
            filename = _resolve_filename(normalized_url, headers)
            self._save_bytes(
                url=normalized_url,
                filename=filename,
                body=body,
                page_url=page_url,
                discovery_method=discovery_method,
                headers=headers,
            )
        except Error:
            LOGGER.exception("Playwright request failed for url: %s", normalized_url)
        except Exception:
            LOGGER.exception("Unexpected download failure for url: %s", normalized_url)

    def _save_response_body(self, response: Response, page_url: str, discovery_method: str) -> None:
        document_url = _normalize_url(response.url)
        if self.state.has_url(document_url):
            self._log_document_event(
                page_url=page_url,
                document_url=document_url,
                discovery_method=discovery_method,
                result="skipped",
                reason="duplicate_url",
            )
            return

        headers = _response_headers(response)
        content_length = _parse_content_length(headers)
        if content_length is not None and content_length > self._max_file_size_bytes:
            self._log_document_event(
                page_url=page_url,
                document_url=document_url,
                discovery_method=discovery_method,
                result="rejected",
                reason="rejected_size_limit",
                content_type=_content_type(headers),
                size=content_length,
            )
            return

        if not response.ok:
            self._log_document_event(
                page_url=page_url,
                document_url=document_url,
                discovery_method=discovery_method,
                result="rejected",
                reason=f"http_status_{response.status}",
                content_type=_content_type(headers),
            )
            return

        body = response.body()
        filename = _resolve_filename(document_url, headers)
        self._save_bytes(
            url=document_url,
            filename=filename,
            body=body,
            page_url=page_url,
            discovery_method=discovery_method,
            headers=headers,
        )

    def _save_download(
        self,
        url: str,
        filename: str,
        temp_path: str | None,
        page_url: str,
        discovery_method: str,
    ) -> None:
        if not temp_path:
            self._log_document_event(
                page_url=page_url,
                document_url=_normalize_url(url),
                filename=filename,
                discovery_method=discovery_method,
                result="rejected",
                reason="missing_temp_download",
            )
            return

        body = Path(temp_path).read_bytes()
        self._save_bytes(
            url=_normalize_url(url),
            filename=filename,
            body=body,
            page_url=page_url,
            discovery_method=discovery_method,
            headers={},
            storage_url=page_url,
        )

    def _save_bytes(
        self,
        url: str,
        filename: str,
        body: bytes,
        page_url: str,
        discovery_method: str,
        headers: dict[str, str],
        storage_url: str | None = None,
    ) -> None:
        normalized_url = _normalize_url(url)
        content_type = _content_type(headers)
        rejection_reason = self._validate_document_body(normalized_url, filename, body, headers, content_type)
        if rejection_reason:
            self._log_document_event(
                page_url=page_url,
                document_url=normalized_url,
                filename=filename,
                discovery_method=discovery_method,
                result="rejected",
                reason=rejection_reason,
                content_type=content_type,
                size=len(body),
            )
            return

        checksum = hashlib.sha256(body).hexdigest()
        sanitized_name = _sanitize_filename(filename)

        if self.state.has_checksum(checksum):
            self.state.register(normalized_url, checksum, sanitized_name)
            self.state.flush_if_needed(self.config.save_state_every_n_files)
            self._log_document_event(
                page_url=page_url,
                document_url=normalized_url,
                filename=sanitized_name,
                discovery_method=discovery_method,
                result="skipped",
                reason="duplicate_checksum",
                content_type=content_type,
                size=len(body),
            )
            return

        target_dir = self._target_directory_for_url(storage_url or normalized_url)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = _build_unique_path(target_dir, sanitized_name, self.state)
        target_path.write_bytes(body)
        self.state.register(normalized_url, checksum, target_path.name)
        self.state.flush_if_needed(self.config.save_state_every_n_files)
        self._log_document_event(
            page_url=page_url,
            document_url=normalized_url,
            filename=target_path.name,
            discovery_method=discovery_method,
            result="saved",
            reason="saved",
            content_type=content_type,
            size=len(body),
        )

    def _target_directory_for_url(self, url: str) -> Path:
        if not self.config.group_by_domain:
            return self.config.download_directory

        netloc = urlparse(url).netloc or "unknown-domain"
        return self.config.download_directory / _sanitize_path_segment(netloc)

    def _validate_document_body(
        self,
        url: str,
        filename: str,
        body: bytes,
        headers: dict[str, str],
        content_type: str,
    ) -> str | None:
        if not body:
            return "rejected_empty_body"

        if len(body) > self._max_file_size_bytes:
            return "rejected_size_limit"

        if _looks_like_html(body):
            return "rejected_html_response"

        if content_type.startswith("text/html"):
            return "rejected_content_type"

        if content_type and not self._is_allowed_content_type(url, filename, headers, content_type):
            return "rejected_content_type"

        return None

    def _is_allowed_content_type(
        self,
        url: str,
        filename: str,
        headers: dict[str, str],
        content_type: str,
    ) -> bool:
        normalized = content_type.split(";", 1)[0].strip().lower()
        content_disposition = (headers.get("content-disposition") or "").lower()
        filename_looks_document = self._looks_like_document_name(url, filename)
        if not normalized:
            return self._is_document_url(url) or filename_looks_document or "attachment" in content_disposition
        if DOCUMENT_CONTENT_TYPE_RE.match(normalized):
            return True
        if normalized == "application/octet-stream":
            return self._is_document_url(url) or filename_looks_document or "attachment" in content_disposition
        return False

    def _is_document_response_candidate(self, url: str, headers: dict[str, str]) -> bool:
        content_disposition = (headers.get("content-disposition") or "").lower()
        content_type = _content_type(headers).split(";", 1)[0].strip().lower()
        filename = _resolve_filename(url, headers)

        if "attachment" in content_disposition:
            return True
        if DOCUMENT_CONTENT_TYPE_RE.match(content_type):
            return True
        if content_type == "application/octet-stream" and self._looks_like_document_name(url, filename):
            return True
        return self._is_document_url(url)

    def _looks_like_document_name(self, url: str, filename: str) -> bool:
        text = f"{url} {filename}".lower()
        return any(ext in text for ext in self.config.allowed_extensions)

    def _log_document_event(
        self,
        *,
        page_url: str,
        document_url: str,
        discovery_method: str,
        result: str,
        reason: str,
        filename: str | None = None,
        content_type: str | None = None,
        size: int | None = None,
    ) -> None:
        payload = {
            "page_url": page_url,
            "document_url": document_url,
            "filename": filename or "",
            "discovery_method": discovery_method,
            "result": result,
            "reason": reason,
            "content_type": content_type or "",
            "size": size if size is not None else "",
        }
        LOGGER.info("document_event %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _is_document_url(self, url: str) -> bool:
        parsed = urlsplit(url)
        path = parsed.path.lower()
        if any(path.endswith(ext) for ext in self.config.allowed_extensions):
            return True

        for values in parse_qs(parsed.query).values():
            for value in values:
                text = value.lower()
                if any(text.endswith(ext) for ext in self.config.allowed_extensions):
                    return True

        return False

    def _is_allowed_domain(self, root_netloc: str, url: str) -> bool:
        netloc = urlparse(url).netloc.lower()
        if netloc == root_netloc:
            return True
        if self.config.follow_subdomains and netloc.endswith(f".{root_netloc}"):
            return True
        return False


def _extract_urls_from_text(text: str, page_url: str) -> list[str]:
    matches = re.findall(
        r"""https?://[^\s'"]+|/[^\s'"]+|[\w./?=&%-]+\.(?:pdf|xls|xlsx|csv|doc|docx|zip)""",
        text,
        re.IGNORECASE,
    )
    resolved = []
    for match in matches:
        if match.lower().startswith("javascript:"):
            continue
        resolved.append(_normalize_url(urljoin(page_url, match)))
    return resolved


def _resolve_filename(url: str, headers: dict[str, str]) -> str:
    content_disposition = headers.get("content-disposition", "")
    filename_match = re.search(r"""filename\*?=(?:UTF-8'')?"?([^";]+)"?""", content_disposition, re.IGNORECASE)
    if filename_match:
        return _sanitize_filename(unquote(filename_match.group(1)))

    parsed = urlsplit(url)
    candidate = Path(unquote(parsed.path)).name
    if candidate and "." in candidate:
        return _sanitize_filename(candidate)

    for values in parse_qs(parsed.query).values():
        for value in values:
            if "." in value:
                return _sanitize_filename(Path(unquote(value)).name)

    content_type = _content_type(headers)
    extension = mimetypes.guess_extension(content_type) or ".bin"
    return f"download{extension}"


def _sanitize_filename(filename: str) -> str:
    safe = re.sub(r'[<>:"/\\|?*]+', "_", filename).strip(" .")
    return safe or "download.bin"


def _sanitize_path_segment(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", value.strip().lower()).strip("._")
    return safe or "unknown-domain"


def _build_unique_path(directory: Path, filename: str, state: DownloadState) -> Path:
    candidate = directory / filename
    stem = candidate.stem
    suffix = candidate.suffix
    index = 1

    while candidate.exists() or state.has_filename(candidate.name):
        candidate = directory / f"{stem}_{index}{suffix}"
        index += 1

    return candidate


def _normalize_url(url: str) -> str:
    raw = url.strip()
    parsed = urlsplit(raw)
    if not parsed.scheme:
        return raw

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if parsed.port and not _is_default_port(scheme, parsed.port):
        netloc = f"{hostname}:{parsed.port}"
    else:
        netloc = hostname

    if parsed.username:
        auth = parsed.username
        if parsed.password:
            auth = f"{auth}:{parsed.password}"
        netloc = f"{auth}@{netloc}"

    path = parsed.path or "/"
    if path.startswith("/"):
        normalized_path = posixpath.normpath(path)
        if not normalized_path.startswith("/"):
            normalized_path = f"/{normalized_path}"
    else:
        normalized_path = path

    if path.endswith("/") and not normalized_path.endswith("/"):
        normalized_path = f"{normalized_path}/"

    return urlunsplit((scheme, netloc, normalized_path or "/", parsed.query, ""))


def _is_default_port(scheme: str, port: int) -> bool:
    return (scheme == "http" and port == 80) or (scheme == "https" and port == 443)


def _parse_content_length(headers: dict[str, str]) -> int | None:
    value = headers.get("content-length")
    if not value:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _content_type(headers: dict[str, str]) -> str:
    return (headers.get("content-type") or "").split(";", 1)[0].strip().lower()


def _response_headers(response: object) -> dict[str, str]:
    all_headers = getattr(response, "all_headers", None)
    if callable(all_headers):
        return _normalize_headers(all_headers())

    headers_attr = getattr(response, "headers", None)
    if callable(headers_attr):
        return _normalize_headers(headers_attr())
    if headers_attr is not None:
        return _normalize_headers(headers_attr)

    return {}


def _normalize_headers(headers: object) -> dict[str, str]:
    if not isinstance(headers, dict):
        return {}
    return {str(key).lower(): str(value) for key, value in headers.items()}


def _looks_like_html(body: bytes) -> bool:
    snippet = body[:2048].lstrip()
    return bool(HTML_SNIPPET_RE.search(snippet))


def _click_priority(label: str) -> int:
    score = 0
    patterns = [
        ("download", 5),
        ("export", 5),
        ("pdf", 4),
        ("excel", 4),
        ("xlsx", 4),
        ("csv", 4),
        ("report", 3),
        ("brochure", 3),
        ("datasheet", 3),
        ("\u0441\u043a\u0430\u0447\u0430\u0442\u044c", 5),
        ("\u044d\u043a\u0441\u043f\u043e\u0440\u0442", 5),
    ]
    lowered = label.lower()
    for token, weight in patterns:
        if token in lowered:
            score += weight
    return score


def _shorten(value: str, limit: int = 120) -> str:
    return value if len(value) <= limit else f"{value[:limit]}..."
