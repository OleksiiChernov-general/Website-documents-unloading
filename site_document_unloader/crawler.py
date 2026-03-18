from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import posixpath
import re
import unicodedata
from dataclasses import dataclass, field
from heapq import heappop, heappush
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlsplit, urlunsplit

from playwright.sync_api import BrowserContext, Error, Page, Response, TimeoutError, sync_playwright

from .config import AppConfig
from .state import DownloadState

LOGGER = logging.getLogger(__name__)
HTML_SNIPPET_RE = re.compile(rb"<!doctype html|<html\b|<body\b|<head\b", re.IGNORECASE)
DOCUMENT_CONTENT_TYPE_RE = re.compile(
    r"^(application/pdf|application/vnd\.ms-excel|application/vnd\.openxmlformats-officedocument\."
    r"(spreadsheetml\.sheet|wordprocessingml\.document)|text/csv|application/msword|application/zip)$",
    re.IGNORECASE,
)
TEXTUAL_REJECTION_CONTENT_TYPE_RE = re.compile(
    r"^(text/html|application/json|text/plain|text/xml|application/xml|application/xhtml\+xml)$",
    re.IGNORECASE,
)
GENERIC_BINARY_CONTENT_TYPE_RE = re.compile(
    r"^(application/octet-stream|binary/octet-stream|application/download|application/x-download|application/force-download)$",
    re.IGNORECASE,
)
NEGATIVE_CONTROL_TEXT_RE = re.compile(
    r"(delete|remove|cancel|logout|sign out|unsubscribe|close|share|follow|login|register|"
    r"удалить|отмена|войти|регистрация)",
    re.IGNORECASE,
)
CONTROL_SELECTORS = [
    "a",
    "button",
    "[role='button']",
    "[role='tab']",
    "[aria-expanded]",
    "summary",
    "details",
    "input[type='button']",
    "input[type='submit']",
    "div[role='button']",
    "span[role='button']",
    "div[onclick]",
    "span[onclick]",
    "div[class*='tab' i]",
    "div[class*='accordion' i]",
    "div[class*='collapse' i]",
    "span[class*='tab' i]",
]
LANGUAGE_CODES = {
    "en": "en",
    "eng": "en",
    "english": "en",
    "tr": "tr",
    "tur": "tr",
    "turkish": "tr",
    "turkce": "tr",
    "türkçe": "tr",
    "ru": "ru",
    "rus": "ru",
    "russian": "ru",
    "русский": "ru",
}


@dataclass(slots=True)
class CrawlTarget:
    url: str
    depth: int
    score: int
    language_context: str
    reason: str
    anchor_text: str = ""


@dataclass(slots=True)
class DocumentCandidate:
    url: str
    discovery_method: str
    label: str = ""
    section_context: str = ""
    source_id: str = ""
    in_document_context: bool = False


@dataclass(slots=True)
class LinkCandidate:
    url: str
    label: str
    reason: str
    score: int
    language_context: str = ""


@dataclass(slots=True)
class ControlCandidate:
    selector: str
    index: int
    label: str
    fingerprint: str
    score: int


@dataclass(slots=True)
class PageSignals:
    title: str
    body_text: str
    headings: list[str]
    document_links: list[DocumentCandidate]
    internal_links: list[LinkCandidate]
    language_variants: list[LinkCandidate]
    control_candidates: list[ControlCandidate]
    page_score: int
    document_rich: bool
    language_context: str


@dataclass(slots=True)
class PageRuntimeState:
    page_url: str
    language_context: str
    page_score: int
    document_rich: bool = False
    recent_document_interaction: bool = False
    page_network_seen: set[str] = field(default_factory=set)
    document_context_urls: set[str] = field(default_factory=set)


class DocumentCrawler:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.state = DownloadState.load(config.state_file)
        self.config.download_directory.mkdir(parents=True, exist_ok=True)
        self._max_file_size_bytes = max(1, self.config.max_file_size_mb) * 1024 * 1024
        self._document_keywords = tuple(_normalize_match_text(value) for value in config.document_keywords)
        self._section_keywords = tuple(_normalize_match_text(value) for value in config.section_keywords)
        self._positive_url_patterns = tuple(_normalize_match_text(value) for value in config.positive_url_patterns)
        self._negative_url_patterns = tuple(_normalize_match_text(value) for value in config.negative_url_patterns)
        self._blocked_extensions = tuple(_normalize_match_text(value) for value in config.blocked_extensions)
        self._blocked_content_types = tuple(_normalize_match_text(value) for value in config.blocked_content_types)
        self._language_switcher_hints = tuple(_normalize_match_text(value) for value in config.language_switcher_hints)
        self._product_page_hints = tuple(_normalize_match_text(value) for value in config.product_page_hints)
        self._queue_counter = 0

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
        base_url = _normalize_url(root_url)
        base_netloc = urlparse(base_url).netloc.lower()
        visited: set[str] = set()
        queued: set[str] = set()
        page_counts = 0
        queue: list[tuple[int, int, CrawlTarget]] = []
        self._enqueue_target(
            queue=queue,
            queued=queued,
            target=CrawlTarget(
                url=base_url,
                depth=0,
                score=100,
                language_context=_detect_language_context(base_url, ""),
                reason="root_url",
            ),
            base_netloc=base_netloc,
            source_page="",
        )

        while queue:
            _priority, _order, target = heappop(queue)
            normalized_url = _normalize_url(target.url)
            if normalized_url in visited:
                continue
            if page_counts >= self.config.max_pages_per_domain:
                self._log_queue_event(
                    source_page="",
                    target_url=normalized_url,
                    score=target.score,
                    depth=target.depth,
                    language_context=target.language_context,
                    result="rejected",
                    reason="blocked_by_page_limit",
                )
                break

            visited.add(normalized_url)
            page_counts += 1
            LOGGER.info(
                "page_open %s",
                json.dumps(
                    {
                        "page_url": normalized_url,
                        "depth": target.depth,
                        "page_score": target.score,
                        "language_context": target.language_context,
                        "reason": target.reason,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )

            page = context.new_page()
            runtime = PageRuntimeState(
                page_url=normalized_url,
                language_context=target.language_context or _detect_language_context(normalized_url, ""),
                page_score=target.score,
            )
            try:
                page.set_default_timeout(self.config.timeout_ms)
                if self.config.network_capture_enabled:
                    page.on("response", lambda response: self._handle_network_response(response, runtime))

                page.goto(normalized_url, wait_until=self.config.wait_until)
                page.wait_for_load_state("domcontentloaded")

                signals = self._inspect_page(page, normalized_url, base_netloc, runtime.language_context)
                runtime.page_score = signals.page_score
                runtime.document_rich = signals.document_rich
                runtime.language_context = signals.language_context

                for candidate in signals.document_links:
                    runtime.document_context_urls.add(_normalize_url(candidate.url))
                    self._download_document_candidate(
                        context=context,
                        current_page=page,
                        page_url=normalized_url,
                        language_context=runtime.language_context,
                        candidate=candidate,
                        runtime=runtime,
                    )

                dynamic_links, dynamic_variants = self._explore_document_controls(
                    context=context,
                    page=page,
                    page_url=normalized_url,
                    base_netloc=base_netloc,
                    runtime=runtime,
                )

                all_internal_links = self._merge_link_candidates(signals.internal_links, dynamic_links)
                allowed_depth = self.config.max_depth + (
                    self.config.document_page_bonus_depth if runtime.document_rich else 0
                )
                for link in all_internal_links[: self.config.max_links_enqueued_per_page]:
                    self._enqueue_target(
                        queue=queue,
                        queued=queued,
                        target=CrawlTarget(
                            url=link.url,
                            depth=target.depth + 1,
                            score=link.score,
                            language_context=link.language_context or runtime.language_context,
                            reason=link.reason,
                            anchor_text=link.label,
                        ),
                        base_netloc=base_netloc,
                        source_page=normalized_url,
                        max_depth_override=allowed_depth,
                    )

                if runtime.document_rich:
                    variants = self._merge_link_candidates(signals.language_variants, dynamic_variants)
                    for variant in variants[: self.config.max_language_variants_per_page]:
                        self._enqueue_target(
                            queue=queue,
                            queued=queued,
                            target=CrawlTarget(
                                url=variant.url,
                                depth=min(target.depth + 1, allowed_depth),
                                score=max(variant.score, runtime.page_score + 5),
                                language_context=variant.language_context or runtime.language_context,
                                reason=variant.reason,
                                anchor_text=variant.label,
                            ),
                            base_netloc=base_netloc,
                            source_page=normalized_url,
                            max_depth_override=allowed_depth,
                        )
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

    def _inspect_page(
        self,
        page: Page,
        page_url: str,
        base_netloc: str,
        inherited_language: str,
    ) -> PageSignals:
        title = ""
        try:
            title = page.title() or ""
        except Error:
            pass
        body_text = self._safe_page_text(page)
        headings = self._extract_heading_texts(page)
        language_context = inherited_language or _detect_language_context(page_url, f"{title} {' '.join(headings)}")
        document_links = self._extract_document_links(page, page_url, language_context)
        control_candidates = self._collect_document_control_candidates(page)
        internal_links = self._extract_internal_links(page, page_url, base_netloc)
        language_variants = self._extract_language_variants(page, page_url, base_netloc)
        page_score = self._score_page(
            page_url=page_url,
            title=title,
            body_text=body_text,
            headings=headings,
            document_links=document_links,
            control_candidates=control_candidates,
        )
        document_rich = self._is_document_rich(
            page_score=page_score,
            body_text=body_text,
            headings=headings,
            document_links=document_links,
            control_candidates=control_candidates,
        )
        LOGGER.info(
            "page_analysis %s",
            json.dumps(
                {
                    "page_url": page_url,
                    "page_score": page_score,
                    "document_rich": document_rich,
                    "document_links": len(document_links),
                    "control_candidates": len(control_candidates),
                    "internal_links": len(internal_links),
                    "language_variants": len(language_variants),
                    "language_context": language_context,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        return PageSignals(
            title=title,
            body_text=body_text,
            headings=headings,
            document_links=document_links,
            internal_links=internal_links,
            language_variants=language_variants,
            control_candidates=control_candidates,
            page_score=page_score,
            document_rich=document_rich,
            language_context=language_context,
        )

    def _safe_page_text(self, page: Page) -> str:
        try:
            return page.evaluate("() => (document.body?.innerText || '').slice(0, 6000)")
        except Error:
            return ""

    def _extract_heading_texts(self, page: Page) -> list[str]:
        try:
            values = page.eval_on_selector_all(
                "h1,h2,h3,h4,h5,h6,summary,[role='tab'],[aria-expanded]",
                "nodes => nodes.map(node => (node.innerText || '').trim()).filter(Boolean).slice(0, 40)",
            )
            return [str(value) for value in values]
        except Error:
            return []

    def _extract_document_links(self, page: Page, page_url: str, language_context: str) -> list[DocumentCandidate]:
        script = """
() => {
  const items = [];
  const push = (value, node, sourceKind) => {
    if (typeof value !== "string") return;
    const trimmed = value.trim();
    if (!trimmed) return;
    const sectionNode = node?.closest("section,article,details,div,li,table,tr,ul,ol");
    const sectionText = ((sectionNode?.innerText || "")).slice(0, 220);
    const label = [
      node?.innerText || "",
      node?.getAttribute?.("title") || "",
      node?.getAttribute?.("aria-label") || "",
      node?.getAttribute?.("alt") || "",
      node?.getAttribute?.("value") || ""
    ].join(" ").trim();
    const sourceId = [
      sourceKind,
      node?.tagName || "",
      node?.id || "",
      node?.getAttribute?.("name") || "",
      node?.getAttribute?.("data-testid") || "",
      node?.getAttribute?.("class") || ""
    ].join("|");
    items.push({ value: trimmed, label, section_text: sectionText, source_id: sourceId });
  };
  document.querySelectorAll("[href],[src],iframe[src],embed[src],object[data]").forEach((node) => {
    if (node.hasAttribute("href")) push(node.getAttribute("href"), node, "href");
    if (node.hasAttribute("src")) push(node.getAttribute("src"), node, "src");
    if (node.hasAttribute("data")) push(node.getAttribute("data"), node, "data");
  });
  document.querySelectorAll("*").forEach((node) => {
    for (const attr of node.getAttributeNames()) {
      if (attr.startsWith("data-") || attr.startsWith("on")) {
        push(node.getAttribute(attr), node, attr);
      }
    }
  });
  return items.slice(0, 1200);
}
"""
        try:
            raw_values = page.evaluate(script)
        except Error:
            return []

        candidates: dict[str, DocumentCandidate] = {}
        for raw in raw_values:
            if not isinstance(raw, dict):
                continue
            label = str(raw.get("label", ""))
            section_text = str(raw.get("section_text", ""))
            value = str(raw.get("value", ""))
            source_id = str(raw.get("source_id", ""))
            in_document_context = self._looks_document_related_text(f"{label} {section_text}")
            for candidate_url in _extract_urls_from_text(value, page_url):
                normalized = _normalize_url(candidate_url)
                blocked_reason = self._blocked_extension_reason(normalized)
                if blocked_reason:
                    self._log_document_event(
                        page_url=page_url,
                        document_url=normalized,
                        internal_id=source_id,
                        filename="",
                        discovery_method="dom_link",
                        language_context=language_context,
                        download_strategy="",
                        result="skipped",
                        reason=blocked_reason,
                    )
                    continue
                if not self._looks_document_candidate_url(normalized, label, section_text):
                    continue
                existing = candidates.get(normalized)
                if existing is None:
                    candidates[normalized] = DocumentCandidate(
                        url=normalized,
                        discovery_method="dom_link",
                        label=label,
                        section_context=section_text,
                        source_id=source_id,
                        in_document_context=in_document_context,
                    )
                else:
                    existing.in_document_context = existing.in_document_context or in_document_context
                    if not existing.label:
                        existing.label = label
                    if not existing.section_context:
                        existing.section_context = section_text
        return list(candidates.values())

    def _extract_internal_links(self, page: Page, page_url: str, base_netloc: str) -> list[LinkCandidate]:
        script = """
() => Array.from(document.querySelectorAll("a[href]")).map((node) => ({
  href: node.href || "",
  text: (node.innerText || "").trim(),
  title: node.getAttribute("title") || "",
  aria: node.getAttribute("aria-label") || "",
  rel: node.getAttribute("rel") || "",
  hreflang: node.getAttribute("hreflang") || "",
  lang: node.getAttribute("lang") || "",
  className: node.getAttribute("class") || "",
  parentText: ((node.parentElement?.innerText || "")).slice(0, 200)
})).slice(0, 1500)
"""
        try:
            entries = page.evaluate(script)
        except Error:
            return []

        candidates: list[LinkCandidate] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            href = _normalize_url(urljoin(page_url, str(entry.get("href", ""))))
            if not href or not self._is_allowed_domain(base_netloc, href):
                continue
            if self._is_negative_link(href):
                self._log_queue_event(
                    source_page=page_url,
                    target_url=href,
                    score=-100,
                    depth=0,
                    language_context="",
                    result="rejected",
                    reason=self._negative_url_reason(href),
                )
                continue
            blocked_reason = self._blocked_extension_reason(href)
            if blocked_reason:
                self._log_queue_event(
                    source_page=page_url,
                    target_url=href,
                    score=-100,
                    depth=0,
                    language_context="",
                    result="rejected",
                    reason=blocked_reason,
                )
                continue

            label = " ".join(
                [
                    str(entry.get("text", "")),
                    str(entry.get("title", "")),
                    str(entry.get("aria", "")),
                    str(entry.get("parentText", "")),
                ]
            ).strip()
            if self._is_probable_language_switcher(label, href, entry):
                continue

            score = self._score_link_candidate(href, label)
            if score <= 0:
                self._log_queue_event(
                    source_page=page_url,
                    target_url=href,
                    score=score,
                    depth=0,
                    language_context="",
                    result="rejected",
                    reason="skipped_navigation_page",
                )
                continue

            candidates.append(
                LinkCandidate(
                    url=href,
                    label=label,
                    reason=self._link_reason(href, label),
                    score=score,
                    language_context=_detect_language_context(
                        href,
                        f"{entry.get('hreflang', '')} {entry.get('lang', '')} {label}",
                    ),
                )
            )

        candidates.sort(key=lambda item: item.score, reverse=True)
        return _dedupe_links(candidates)

    def _extract_language_variants(self, page: Page, page_url: str, base_netloc: str) -> list[LinkCandidate]:
        script = """
() => {
  const items = [];
  document.querySelectorAll("link[rel='alternate'][hreflang][href]").forEach((node) => {
    items.push({ href: node.href || "", text: node.getAttribute("hreflang") || "", kind: "alternate" });
  });
  document.querySelectorAll("a[href],button[onclick]").forEach((node) => {
    const href = node.href || node.getAttribute("data-href") || node.getAttribute("href") || "";
    if (!href) return;
    items.push({
      href,
      text: [
        node.innerText || "",
        node.getAttribute("title") || "",
        node.getAttribute("aria-label") || "",
        node.getAttribute("lang") || "",
        node.getAttribute("hreflang") || "",
        node.getAttribute("class") || ""
      ].join(" ").trim(),
      kind: "switcher"
    });
  });
  return items.slice(0, 250);
}
"""
        try:
            entries = page.evaluate(script)
        except Error:
            return []

        candidates: list[LinkCandidate] = []
        current_signature = _language_variant_signature(page_url)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            href = _normalize_url(urljoin(page_url, str(entry.get("href", ""))))
            text = str(entry.get("text", ""))
            if not href or not self._is_allowed_domain(base_netloc, href):
                continue
            if self._is_negative_link(href) or self._blocked_extension_reason(href):
                continue
            if _language_variant_signature(href) != current_signature:
                continue
            if not self._is_probable_language_switcher(text, href, entry):
                continue
            language_context = _detect_language_context(href, text)
            candidates.append(
                LinkCandidate(
                    url=href,
                    label=text,
                    reason="language_variant",
                    score=self._score_link_candidate(href, text) + 5,
                    language_context=language_context,
                )
            )
        return _dedupe_links(candidates)

    def _collect_document_control_candidates(self, page: Page) -> list[ControlCandidate]:
        candidates: list[ControlCandidate] = []
        for selector in CONTROL_SELECTORS:
            locator = page.locator(selector)
            try:
                count = min(locator.count(), 80)
            except Error:
                continue
            for index in range(count):
                element = locator.nth(index)
                try:
                    label = self._extract_element_label(element)
                except Error:
                    continue
                if not label or NEGATIVE_CONTROL_TEXT_RE.search(label):
                    continue
                score = self._score_control_label(label)
                if score <= 0:
                    continue
                candidates.append(
                    ControlCandidate(
                        selector=selector,
                        index=index,
                        label=label,
                        fingerprint=self._build_element_fingerprint(element, selector, label),
                        score=score,
                    )
                )
        candidates.sort(key=lambda item: item.score, reverse=True)
        unique: list[ControlCandidate] = []
        seen: set[str] = set()
        for item in candidates:
            if item.fingerprint in seen:
                continue
            seen.add(item.fingerprint)
            unique.append(item)
        return unique

    def _extract_element_label(self, element: object) -> str:
        locator = element
        parts = [
            locator.inner_text(timeout=500).strip() if locator.is_visible() else "",
            locator.get_attribute("aria-label") or "",
            locator.get_attribute("title") or "",
            locator.get_attribute("value") or "",
            locator.get_attribute("href") or "",
            locator.get_attribute("id") or "",
            locator.get_attribute("class") or "",
            locator.get_attribute("aria-controls") or "",
            locator.get_attribute("data-testid") or "",
        ]
        return " ".join(part for part in parts if part).strip()

    def _build_element_fingerprint(self, element: object, selector: str, label: str) -> str:
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

    def _explore_document_controls(
        self,
        context: BrowserContext,
        page: Page,
        page_url: str,
        base_netloc: str,
        runtime: PageRuntimeState,
    ) -> tuple[list[LinkCandidate], list[LinkCandidate]]:
        clicked_keys: set[str] = set()
        discovered_links: list[LinkCandidate] = []
        language_variants: list[LinkCandidate] = []
        max_clicks = self.config.max_document_controls_per_page + (
            self.config.document_page_bonus_clicks if runtime.document_rich else 0
        )
        for _ in range(max_clicks):
            candidates = self._collect_document_control_candidates(page)
            next_candidate = next((item for item in candidates if item.fingerprint not in clicked_keys), None)
            if next_candidate is None:
                if not clicked_keys:
                    self._log_document_event(
                        page_url=page_url,
                        document_url="",
                        internal_id="page_controls",
                        filename="",
                        discovery_method="click_scan",
                        language_context=runtime.language_context,
                        download_strategy="",
                        result="skipped",
                        reason="not_clicked_keyword_mismatch",
                    )
                break

            clicked_keys.add(next_candidate.fingerprint)
            runtime.recent_document_interaction = True
            LOGGER.info(
                "click_attempt %s",
                json.dumps(
                    {
                        "page_url": page_url,
                        "label": _shorten(next_candidate.label),
                        "score": next_candidate.score,
                        "language_context": runtime.language_context,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            )

            element = page.locator(next_candidate.selector).nth(next_candidate.index)
            browser_saved = self._try_click_with_download(
                page=page,
                element=element,
                page_url=page_url,
                language_context=runtime.language_context,
                runtime=runtime,
            )
            if not browser_saved:
                try:
                    element.click(timeout=2500, force=True)
                except Error:
                    try:
                        element.dispatch_event("click")
                    except Error:
                        self._log_document_event(
                            page_url=page_url,
                            document_url="",
                            internal_id=next_candidate.fingerprint,
                            filename="",
                            discovery_method="click_download",
                            language_context=runtime.language_context,
                            download_strategy="browser_download",
                            result="rejected",
                            reason="download_strategy_failed_browser",
                        )
                        continue

            self._settle_after_click(page)
            if self.config.post_click_rescan:
                for candidate in self._extract_document_links(page, page_url, runtime.language_context):
                    runtime.document_context_urls.add(_normalize_url(candidate.url))
                    self._download_document_candidate(
                        context=context,
                        current_page=page,
                        page_url=page_url,
                        language_context=runtime.language_context,
                        candidate=candidate,
                        runtime=runtime,
                    )
                discovered_links.extend(self._extract_internal_links(page, page_url, base_netloc))
                language_variants.extend(self._extract_language_variants(page, page_url, base_netloc))
            runtime.recent_document_interaction = False

        return _dedupe_links(discovered_links), _dedupe_links(language_variants)

    def _try_click_with_download(
        self,
        page: Page,
        element: object,
        page_url: str,
        language_context: str,
        runtime: PageRuntimeState,
    ) -> bool:
        try:
            with page.expect_download(timeout=2500) as download_info:
                element.click(timeout=2000, force=True)
            download = download_info.value
        except (TimeoutError, Error):
            return False

        self._save_download(
            url=download.url,
            filename=download.suggested_filename,
            temp_path=download.path(),
            page_url=page_url,
            discovery_method="click_download",
            language_context=language_context,
            download_strategy="browser_download",
            internal_id="click_download",
        )
        runtime.document_context_urls.add(_normalize_url(download.url))
        return True

    def _download_document_candidate(
        self,
        context: BrowserContext,
        current_page: Page,
        page_url: str,
        language_context: str,
        candidate: DocumentCandidate,
        runtime: PageRuntimeState,
    ) -> None:
        normalized_url = _normalize_url(candidate.url)
        negative_reason = self._negative_url_reason(normalized_url)
        if negative_reason:
            self._log_document_event(
                page_url=page_url,
                document_url=normalized_url,
                internal_id=candidate.source_id,
                filename="",
                discovery_method=candidate.discovery_method,
                language_context=language_context,
                download_strategy="",
                result="skipped",
                reason=negative_reason,
            )
            return
        blocked_reason = self._blocked_extension_reason(normalized_url)
        if blocked_reason:
            self._log_document_event(
                page_url=page_url,
                document_url=normalized_url,
                internal_id=candidate.source_id,
                filename="",
                discovery_method=candidate.discovery_method,
                language_context=language_context,
                download_strategy="",
                result="skipped",
                reason=blocked_reason,
            )
            return
        if self.state.has_url(normalized_url):
            self._log_document_event(
                page_url=page_url,
                document_url=normalized_url,
                internal_id=candidate.source_id,
                filename="",
                discovery_method=candidate.discovery_method,
                language_context=language_context,
                download_strategy="",
                result="skipped",
                reason="duplicate_url",
            )
            return

        runtime.document_context_urls.add(normalized_url)
        if self._download_via_direct_request(context, page_url, language_context, candidate, runtime):
            return
        if self._download_via_page_context(context, page_url, language_context, candidate, runtime):
            return
        if self._download_via_browser_flow(context, current_page, page_url, language_context, candidate, runtime):
            return

        self._log_document_event(
            page_url=page_url,
            document_url=normalized_url,
            internal_id=candidate.source_id,
            filename="",
            discovery_method=candidate.discovery_method,
            language_context=language_context,
            download_strategy="network_capture",
            result="rejected",
            reason="download_strategy_failed_network_capture",
        )

    def _download_via_direct_request(
        self,
        context: BrowserContext,
        page_url: str,
        language_context: str,
        candidate: DocumentCandidate,
        runtime: PageRuntimeState,
    ) -> bool:
        normalized_url = _normalize_url(candidate.url)
        try:
            response = context.request.get(
                normalized_url,
                timeout=self.config.timeout_ms,
                headers={"Referer": page_url, "Accept": "*/*"},
            )
            return self._save_response_like(
                response_url=normalized_url,
                response=response,
                body=response.body(),
                page_url=page_url,
                discovery_method=candidate.discovery_method,
                language_context=language_context,
                download_strategy="direct_request",
                internal_id=candidate.source_id,
                candidate=candidate,
                runtime=runtime,
            )
        except Exception:
            self._log_document_event(
                page_url=page_url,
                document_url=normalized_url,
                internal_id=candidate.source_id,
                filename="",
                discovery_method=candidate.discovery_method,
                language_context=language_context,
                download_strategy="direct_request",
                result="rejected",
                reason="download_strategy_failed_direct",
            )
            return False

    def _download_via_page_context(
        self,
        context: BrowserContext,
        page_url: str,
        language_context: str,
        candidate: DocumentCandidate,
        runtime: PageRuntimeState,
    ) -> bool:
        session_page = context.new_page()
        normalized_url = _normalize_url(candidate.url)
        try:
            session_page.set_default_timeout(self.config.timeout_ms)
            session_page.set_extra_http_headers({"Referer": page_url})
            response = session_page.goto(normalized_url, wait_until="domcontentloaded")
            if response is None:
                self._log_document_event(
                    page_url=page_url,
                    document_url=normalized_url,
                    internal_id=candidate.source_id,
                    filename="",
                    discovery_method=candidate.discovery_method,
                    language_context=language_context,
                    download_strategy="page_context",
                    result="rejected",
                    reason="download_strategy_failed_page_context",
                )
                return False
            return self._save_response_like(
                response_url=normalized_url,
                response=response,
                body=response.body(),
                page_url=page_url,
                discovery_method=candidate.discovery_method,
                language_context=language_context,
                download_strategy="page_context",
                internal_id=candidate.source_id,
                candidate=candidate,
                runtime=runtime,
            )
        except Exception:
            self._log_document_event(
                page_url=page_url,
                document_url=normalized_url,
                internal_id=candidate.source_id,
                filename="",
                discovery_method=candidate.discovery_method,
                language_context=language_context,
                download_strategy="page_context",
                result="rejected",
                reason="download_strategy_failed_page_context",
            )
            return False
        finally:
            session_page.close()

    def _download_via_browser_flow(
        self,
        context: BrowserContext,
        current_page: Page,
        page_url: str,
        language_context: str,
        candidate: DocumentCandidate,
        runtime: PageRuntimeState,
    ) -> bool:
        normalized_url = _normalize_url(candidate.url)
        try:
            with current_page.expect_download(timeout=2500) as download_info:
                current_page.evaluate(
                    """url => {
                        const anchor = document.createElement('a');
                        anchor.href = url;
                        anchor.target = '_self';
                        anchor.rel = 'noopener';
                        anchor.download = '';
                        document.body.appendChild(anchor);
                        anchor.click();
                        anchor.remove();
                    }""",
                    normalized_url,
                )
            download = download_info.value
        except Exception:
            self._log_document_event(
                page_url=page_url,
                document_url=normalized_url,
                internal_id=candidate.source_id,
                filename="",
                discovery_method=candidate.discovery_method,
                language_context=language_context,
                download_strategy="browser_download",
                result="rejected",
                reason="download_strategy_failed_browser",
            )
            return False

        self._save_download(
            url=download.url,
            filename=download.suggested_filename,
            temp_path=download.path(),
            page_url=page_url,
            discovery_method=candidate.discovery_method,
            language_context=language_context,
            download_strategy="browser_download",
            internal_id=candidate.source_id,
        )
        runtime.document_context_urls.add(_normalize_url(download.url))
        return True

    def _handle_network_response(self, response: Response, runtime: PageRuntimeState) -> None:
        try:
            document_url = _normalize_url(response.url)
            if document_url in runtime.page_network_seen:
                return
            headers = _response_headers(response)
            in_document_context = document_url in runtime.document_context_urls or runtime.recent_document_interaction
            blocked_extension_reason = self._blocked_extension_reason(document_url, _resolve_filename(document_url, headers))
            blocked_content_reason = self._blocked_content_type_reason(_content_type(headers))
            negative_reason = self._negative_url_reason(document_url)
            if blocked_extension_reason or blocked_content_reason or negative_reason:
                if in_document_context:
                    self._log_document_event(
                        page_url=runtime.page_url,
                        document_url=document_url,
                        internal_id="network_response",
                        filename="",
                        discovery_method="network_response",
                        language_context=runtime.language_context,
                        download_strategy="network_capture",
                        result="skipped",
                        reason=blocked_extension_reason or blocked_content_reason or negative_reason or "skipped_non_document_asset",
                        content_type=_content_type(headers),
                    )
                return
            if not self._is_document_response_candidate(document_url, headers, in_document_context):
                if in_document_context:
                    self._log_document_event(
                        page_url=runtime.page_url,
                        document_url=document_url,
                        internal_id="network_response",
                        filename="",
                        discovery_method="network_response",
                        language_context=runtime.language_context,
                        download_strategy="network_capture",
                        result="skipped",
                        reason="not_document_like",
                        content_type=_content_type(headers),
                    )
                return
            runtime.page_network_seen.add(document_url)
            self._save_response_body(
                response=response,
                page_url=runtime.page_url,
                language_context=runtime.language_context,
                discovery_method="network_response",
                download_strategy="network_capture",
                internal_id="network_response",
                in_document_context=in_document_context,
            )
        except Error:
            LOGGER.debug("network response failure page_url=%s", runtime.page_url, exc_info=True)
        except Exception:
            LOGGER.exception("Unexpected network response failure page_url=%s", runtime.page_url)

    def _save_response_like(
        self,
        response_url: str,
        response: object,
        body: bytes,
        page_url: str,
        discovery_method: str,
        language_context: str,
        download_strategy: str,
        internal_id: str,
        candidate: DocumentCandidate,
        runtime: PageRuntimeState,
    ) -> bool:
        headers = _response_headers(response)
        content_length = _parse_content_length(headers)
        if content_length is not None and content_length > self._max_file_size_bytes:
            self._log_document_event(
                page_url=page_url,
                document_url=response_url,
                internal_id=internal_id,
                filename="",
                discovery_method=discovery_method,
                language_context=language_context,
                download_strategy=download_strategy,
                result="rejected",
                reason="rejected_size_limit",
                content_type=_content_type(headers),
                size=content_length,
            )
            return False

        status = getattr(response, "status", 200)
        ok = bool(getattr(response, "ok", True))
        if not ok:
            self._log_document_event(
                page_url=page_url,
                document_url=response_url,
                internal_id=internal_id,
                filename="",
                discovery_method=discovery_method,
                language_context=language_context,
                download_strategy=download_strategy,
                result="rejected",
                reason=f"http_status_{status}",
                content_type=_content_type(headers),
            )
            return False

        filename = _resolve_filename(response_url, headers)
        return self._persist_document_bytes(
            url=response_url,
            filename=filename,
            body=body,
            page_url=page_url,
            discovery_method=discovery_method,
            language_context=language_context,
            download_strategy=download_strategy,
            headers=headers,
            internal_id=internal_id,
            in_document_context=candidate.in_document_context or runtime.recent_document_interaction,
        )

    def _save_response_body(
        self,
        response: Response,
        page_url: str,
        language_context: str,
        discovery_method: str,
        download_strategy: str,
        internal_id: str,
        in_document_context: bool,
    ) -> None:
        document_url = _normalize_url(response.url)
        if self.state.has_url(document_url):
            self._log_document_event(
                page_url=page_url,
                document_url=document_url,
                internal_id=internal_id,
                filename="",
                discovery_method=discovery_method,
                language_context=language_context,
                download_strategy=download_strategy,
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
                internal_id=internal_id,
                filename="",
                discovery_method=discovery_method,
                language_context=language_context,
                download_strategy=download_strategy,
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
                internal_id=internal_id,
                filename="",
                discovery_method=discovery_method,
                language_context=language_context,
                download_strategy=download_strategy,
                result="rejected",
                reason=f"http_status_{response.status}",
                content_type=_content_type(headers),
            )
            return

        filename = _resolve_filename(document_url, headers)
        self._persist_document_bytes(
            url=document_url,
            filename=filename,
            body=response.body(),
            page_url=page_url,
            discovery_method=discovery_method,
            language_context=language_context,
            download_strategy=download_strategy,
            headers=headers,
            internal_id=internal_id,
            in_document_context=in_document_context,
        )

    def _save_download(
        self,
        url: str,
        filename: str,
        temp_path: str | None,
        page_url: str,
        discovery_method: str,
        language_context: str,
        download_strategy: str,
        internal_id: str,
    ) -> None:
        normalized_url = _normalize_url(url)
        if not temp_path:
            self._log_document_event(
                page_url=page_url,
                document_url=normalized_url,
                internal_id=internal_id,
                filename=filename,
                discovery_method=discovery_method,
                language_context=language_context,
                download_strategy=download_strategy,
                result="rejected",
                reason="download_strategy_failed_browser",
            )
            return

        self._persist_document_bytes(
            url=normalized_url,
            filename=filename,
            body=Path(temp_path).read_bytes(),
            page_url=page_url,
            discovery_method=discovery_method,
            language_context=language_context,
            download_strategy=download_strategy,
            headers={},
            storage_url=page_url,
            internal_id=internal_id,
            in_document_context=True,
        )

    def _persist_document_bytes(
        self,
        url: str,
        filename: str,
        body: bytes,
        page_url: str,
        discovery_method: str,
        language_context: str,
        download_strategy: str,
        headers: dict[str, str],
        internal_id: str,
        in_document_context: bool,
        storage_url: str | None = None,
    ) -> bool:
        normalized_url = _normalize_url(url)
        content_type = _content_type(headers)
        rejection_reason = self._validate_document_body(
            url=normalized_url,
            filename=filename,
            body=body,
            headers=headers,
            content_type=content_type,
            in_document_context=in_document_context,
        )
        if rejection_reason:
            self._log_document_event(
                page_url=page_url,
                document_url=normalized_url,
                internal_id=internal_id,
                filename=filename,
                discovery_method=discovery_method,
                language_context=language_context,
                download_strategy=download_strategy,
                result="rejected",
                reason=rejection_reason,
                content_type=content_type,
                size=len(body),
            )
            return False

        checksum = hashlib.sha256(body).hexdigest()
        sanitized_name = _sanitize_filename(filename)
        if self.state.has_checksum(checksum):
            self.state.register(normalized_url, checksum, sanitized_name)
            self.state.flush_if_needed(self.config.save_state_every_n_files)
            self._log_document_event(
                page_url=page_url,
                document_url=normalized_url,
                internal_id=internal_id,
                filename=sanitized_name,
                discovery_method=discovery_method,
                language_context=language_context,
                download_strategy=download_strategy,
                result="skipped",
                reason="duplicate_checksum",
                content_type=content_type,
                size=len(body),
            )
            return True

        target_dir = self._target_directory_for_url(storage_url or normalized_url)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = _build_unique_path(target_dir, sanitized_name, self.state)
        target_path.write_bytes(body)
        self.state.register(normalized_url, checksum, target_path.name)
        self.state.flush_if_needed(self.config.save_state_every_n_files)
        self._log_document_event(
            page_url=page_url,
            document_url=normalized_url,
            internal_id=internal_id,
            filename=target_path.name,
            discovery_method=discovery_method,
            language_context=language_context,
            download_strategy=download_strategy,
            result="saved",
            reason="saved",
            content_type=content_type,
            size=len(body),
        )
        return True

    def _validate_document_body(
        self,
        url: str,
        filename: str,
        body: bytes,
        headers: dict[str, str],
        content_type: str,
        in_document_context: bool,
    ) -> str | None:
        blocked_extension_reason = self._blocked_extension_reason(url, filename)
        if blocked_extension_reason:
            return blocked_extension_reason
        if not body:
            return "rejected_empty_body"
        if len(body) > self._max_file_size_bytes:
            return "rejected_size_limit"
        if _looks_like_html(body):
            return "rejected_html_response"
        blocked_content_reason = self._blocked_content_type_reason(content_type)
        if blocked_content_reason:
            return blocked_content_reason
        if TEXTUAL_REJECTION_CONTENT_TYPE_RE.match(content_type):
            return "rejected_content_type"
        if content_type and not self._is_allowed_content_type(url, filename, headers, content_type, in_document_context):
            return "not_document_like"
        if self.config.documents_only and not (
            self._is_document_url(url)
            or self._looks_like_document_name(url, filename)
            or "attachment" in (headers.get("content-disposition") or "").lower()
            or (content_type and self._is_allowed_content_type(url, filename, headers, content_type, in_document_context))
        ):
            return "not_document_like"
        return None

    def _is_allowed_content_type(
        self,
        url: str,
        filename: str,
        headers: dict[str, str],
        content_type: str,
        in_document_context: bool,
    ) -> bool:
        normalized = content_type.split(";", 1)[0].strip().lower()
        content_disposition = (headers.get("content-disposition") or "").lower()
        filename_looks_document = self._looks_like_document_name(url, filename)
        if not normalized:
            return self._is_document_url(url) or filename_looks_document or "attachment" in content_disposition
        if DOCUMENT_CONTENT_TYPE_RE.match(normalized):
            return True
        if GENERIC_BINARY_CONTENT_TYPE_RE.match(normalized):
            return self._is_document_url(url) or filename_looks_document or "attachment" in content_disposition or in_document_context
        if normalized.startswith("application/") and (
            filename_looks_document or "attachment" in content_disposition or in_document_context
        ):
            return True
        return False

    def _is_document_response_candidate(self, url: str, headers: dict[str, str], in_document_context: bool) -> bool:
        content_disposition = (headers.get("content-disposition") or "").lower()
        content_type = _content_type(headers)
        filename = _resolve_filename(url, headers)
        if self._blocked_extension_reason(url, filename):
            return False
        if self._blocked_content_type_reason(content_type):
            return False
        if "attachment" in content_disposition or DOCUMENT_CONTENT_TYPE_RE.match(content_type):
            return True
        if GENERIC_BINARY_CONTENT_TYPE_RE.match(content_type):
            return self._looks_like_document_name(url, filename) or in_document_context
        if content_type.startswith("application/") and in_document_context:
            return True
        return self._is_document_url(url) or self._looks_like_document_name(url, filename)

    def _looks_like_document_name(self, url: str, filename: str) -> bool:
        text = _normalize_match_text(f"{url} {filename}")
        return any(extension in text for extension in self.config.allowed_extensions)

    def _looks_document_candidate_url(self, url: str, label: str, section_text: str) -> bool:
        if self._blocked_extension_reason(url) or self._negative_url_reason(url):
            return False
        if self._is_document_url(url):
            return True
        if self._looks_document_related_text(f"{label} {section_text}"):
            return True
        normalized_url = _normalize_match_text(url)
        return any(pattern in normalized_url for pattern in self._positive_url_patterns)

    def _looks_document_related_text(self, text: str) -> bool:
        normalized = _normalize_match_text(text)
        return _count_keyword_hits(normalized, self._document_keywords) > 0 or _count_keyword_hits(normalized, self._section_keywords) > 0

    def _score_control_label(self, label: str) -> int:
        normalized = _normalize_match_text(label)
        return _count_keyword_hits(normalized, self._document_keywords) * 4 + _count_keyword_hits(normalized, self._section_keywords) * 3

    def _score_link_candidate(self, url: str, label: str) -> int:
        normalized_url = _normalize_match_text(url)
        normalized_label = _normalize_match_text(label)
        score = 0
        if self._blocked_extension_reason(url):
            return -100
        if self._negative_url_reason(url):
            return -100
        score += _count_keyword_hits(normalized_url, self._positive_url_patterns) * 6
        score += _count_keyword_hits(normalized_label, self._document_keywords) * 5
        score += _count_keyword_hits(normalized_label, self._section_keywords) * 4
        score += _count_keyword_hits(normalized_label, self._product_page_hints) * 3
        if self._is_document_url(url):
            score += 12
        if any(pattern in normalized_url for pattern in self._negative_url_patterns):
            score -= 25
        if self._looks_like_product_page(url, label):
            score += 8
        return score

    def _score_page(
        self,
        page_url: str,
        title: str,
        body_text: str,
        headings: list[str],
        document_links: list[DocumentCandidate],
        control_candidates: list[ControlCandidate],
    ) -> int:
        signal_text = " ".join([page_url, title, body_text[:2500], " ".join(headings[:15])])
        normalized = _normalize_match_text(signal_text)
        score = 0
        score += _count_keyword_hits(normalized, self._positive_url_patterns) * 5
        score += _count_keyword_hits(normalized, self._document_keywords) * 4
        score += _count_keyword_hits(normalized, self._section_keywords) * 5
        score += _count_keyword_hits(normalized, self._product_page_hints) * 3
        score += min(len(document_links), 8) * 6
        score += min(len(control_candidates), 8) * 4
        if self._looks_like_product_page(page_url, f"{title} {' '.join(headings)}"):
            score += 10
        if any(pattern in normalized for pattern in self._negative_url_patterns):
            score -= 20
        return score

    def _is_document_rich(
        self,
        page_score: int,
        body_text: str,
        headings: list[str],
        document_links: list[DocumentCandidate],
        control_candidates: list[ControlCandidate],
    ) -> bool:
        signal_text = _normalize_match_text(f"{body_text[:2500]} {' '.join(headings[:15])}")
        repeated_items = len(document_links) >= 2 or len(control_candidates) >= 2
        section_hits = _count_keyword_hits(signal_text, self._section_keywords)
        return page_score >= 18 or repeated_items or section_hits >= 2

    def _looks_like_product_page(self, url: str, text: str) -> bool:
        normalized = _normalize_match_text(f"{url} {text}")
        return _count_keyword_hits(normalized, self._product_page_hints) > 0

    def _link_reason(self, url: str, label: str) -> str:
        normalized = _normalize_match_text(f"{url} {label}")
        if _count_keyword_hits(normalized, self._document_keywords):
            return "document_keyword"
        if _count_keyword_hits(normalized, self._section_keywords):
            return "section_keyword"
        if _count_keyword_hits(normalized, self._product_page_hints):
            return "product_page_hint"
        return "internal_link"

    def _enqueue_target(
        self,
        queue: list[tuple[int, int, CrawlTarget]],
        queued: set[str],
        target: CrawlTarget,
        base_netloc: str,
        source_page: str,
        max_depth_override: int | None = None,
    ) -> None:
        normalized = _normalize_url(target.url)
        max_depth = self.config.max_depth if max_depth_override is None else max_depth_override
        if target.depth > max_depth:
            self._log_queue_event(source_page=source_page, target_url=normalized, score=target.score, depth=target.depth, language_context=target.language_context, result="rejected", reason="depth_limit")
            return
        if normalized in queued:
            return
        if not self._is_allowed_domain(base_netloc, normalized):
            self._log_queue_event(source_page=source_page, target_url=normalized, score=target.score, depth=target.depth, language_context=target.language_context, result="rejected", reason="outside_domain")
            return
        negative_reason = self._negative_url_reason(normalized)
        if negative_reason:
            self._log_queue_event(source_page=source_page, target_url=normalized, score=target.score, depth=target.depth, language_context=target.language_context, result="rejected", reason=negative_reason)
            return
        blocked_reason = self._blocked_extension_reason(normalized)
        if blocked_reason:
            self._log_queue_event(source_page=source_page, target_url=normalized, score=target.score, depth=target.depth, language_context=target.language_context, result="rejected", reason=blocked_reason)
            return
        queued.add(normalized)
        self._queue_counter += 1
        heappush(queue, (-target.score, self._queue_counter, target))
        self._log_queue_event(source_page=source_page, target_url=normalized, score=target.score, depth=target.depth, language_context=target.language_context, result="queued", reason=target.reason)

    def _merge_link_candidates(self, left: list[LinkCandidate], right: list[LinkCandidate]) -> list[LinkCandidate]:
        return _dedupe_links([*left, *right])

    def _is_probable_language_switcher(self, text: str, url: str, entry: dict[str, object]) -> bool:
        normalized_text = _normalize_match_text(text)
        normalized_url = _normalize_match_text(url)
        if any(hint in normalized_text for hint in self._language_switcher_hints):
            return True
        if any(hint in normalized_url for hint in self._language_switcher_hints):
            return True
        for key in ("lang", "hreflang"):
            value = _normalize_match_text(str(entry.get(key, "")))
            if value in LANGUAGE_CODES:
                return True
        return False

    def _is_negative_link(self, url: str) -> bool:
        return self._negative_url_reason(url) is not None

    def _negative_url_reason(self, url: str) -> str | None:
        normalized = _normalize_match_text(url)
        if normalized.startswith("#"):
            return "skipped_navigation_page"
        if "/producttags/" in normalized or "/tags/" in normalized:
            return "skipped_tag_page"
        for pattern in self._negative_url_patterns:
            if pattern in normalized:
                if any(token in pattern for token in ("/news", "/blog", "/contact", "/search", "privacy", "terms", "cookie")):
                    return "skipped_navigation_page"
                return "blocked_negative_url_pattern"
        return None

    def _blocked_extension_reason(self, url: str, filename: str = "") -> str | None:
        parsed = urlsplit(url)
        values = [parsed.path.lower(), filename.lower()]
        for query_values in parse_qs(parsed.query).values():
            values.extend(str(item).lower() for item in query_values)
        for value in values:
            if any(value.endswith(extension) for extension in self._blocked_extensions):
                return "blocked_extension"
        normalized = _normalize_match_text(f"{url} {filename}")
        if "favicon" in normalized or "thumbnail" in normalized or "thumb" in normalized:
            return "skipped_non_document_asset"
        return None

    def _blocked_content_type_reason(self, content_type: str) -> str | None:
        normalized = _normalize_match_text(content_type)
        for blocked in self._blocked_content_types:
            if not blocked:
                continue
            if blocked.endswith("/") and normalized.startswith(blocked):
                return "blocked_content_type"
            if normalized == blocked:
                return "blocked_content_type"
        return None

    def _settle_after_click(self, page: Page) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=min(self.config.timeout_ms, 3000))
        except TimeoutError:
            pass
        try:
            page.wait_for_timeout(900)
        except Error:
            pass

    def _target_directory_for_url(self, url: str) -> Path:
        if not self.config.group_by_domain:
            return self.config.download_directory
        netloc = urlparse(url).netloc or "unknown-domain"
        return self.config.download_directory / _sanitize_path_segment(netloc)

    def _log_document_event(
        self,
        *,
        page_url: str,
        document_url: str,
        internal_id: str,
        filename: str,
        discovery_method: str,
        language_context: str,
        download_strategy: str,
        result: str,
        reason: str,
        content_type: str | None = None,
        size: int | None = None,
    ) -> None:
        payload = {
            "page_url": page_url,
            "document_url": document_url,
            "internal_id": internal_id,
            "filename": filename,
            "discovery_method": discovery_method,
            "language_context": language_context,
            "download_strategy": download_strategy,
            "result": result,
            "reason": reason,
            "content_type": content_type or "",
            "size": size if size is not None else "",
        }
        LOGGER.info("document_event %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _log_queue_event(
        self,
        *,
        source_page: str,
        target_url: str,
        score: int,
        depth: int,
        language_context: str,
        result: str,
        reason: str,
    ) -> None:
        payload = {
            "source_page": source_page,
            "target_url": target_url,
            "page_score": score,
            "depth": depth,
            "language_context": language_context,
            "result": result,
            "reason": reason,
        }
        LOGGER.info("queue_event %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))

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


def _dedupe_links(candidates: list[LinkCandidate]) -> list[LinkCandidate]:
    best: dict[str, LinkCandidate] = {}
    for item in candidates:
        normalized = _normalize_url(item.url)
        existing = best.get(normalized)
        if existing is None or item.score > existing.score:
            best[normalized] = LinkCandidate(url=normalized, label=item.label, reason=item.reason, score=item.score, language_context=item.language_context)
    return sorted(best.values(), key=lambda value: value.score, reverse=True)


def _normalize_match_text(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", stripped).strip()


def _count_keyword_hits(text: str, keywords: Iterable[str]) -> int:
    return sum(1 for keyword in keywords if keyword and keyword in text)


def _detect_language_context(url: str, text: str) -> str:
    normalized = _normalize_match_text(f"{url} {text}")
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    for part in path_parts[:3]:
        code = _normalize_match_text(part)
        if code in LANGUAGE_CODES:
            return LANGUAGE_CODES[code]
    for token, code in LANGUAGE_CODES.items():
        if token in normalized:
            return code
    return "unknown"


def _language_variant_signature(url: str) -> str:
    parsed = urlsplit(url)
    parts = [part for part in parsed.path.split("/") if part]
    if parts and _normalize_match_text(parts[0]) in LANGUAGE_CODES:
        parts = parts[1:]
    normalized_path = "/" + "/".join(parts)
    return f"{parsed.netloc.lower()}|{normalized_path}|{parsed.query}"


def _extract_urls_from_text(text: str, page_url: str) -> list[str]:
    matches = re.findall(r"""https?://[^\s'"]+|/[^\s'"]+|[\w./?=&%-]+\.(?:pdf|xls|xlsx|csv|doc|docx|zip)""", text, re.IGNORECASE)
    resolved: list[str] = []
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


def _shorten(value: str, limit: int = 120) -> str:
    return value if len(value) <= limit else f"{value[:limit]}..."
