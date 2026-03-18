# Website documents unloading

Python utility for finding and downloading documents from websites with Playwright.

## What it does

- crawls start URLs from a YAML config
- renders pages in Chromium and analyzes the post-JavaScript DOM
- downloads direct document links from the page
- clicks multilingual document controls, tabs, accordion sections, modals, and other document-like UI controls
- captures document-like browser network responses, including XHR and fetch scenarios
- rescans the DOM after relevant clicks to find links that appear dynamically
- prioritizes likely product, catalog, resource, and document-rich pages instead of plain BFS
- opens alternate language variants only for relevant document-rich pages and with a hard limit
- uses fallback download strategies when a direct request is not enough
- in `documents_only` mode saves only real document-like files and drops HTML, images, scripts, styles, fonts, and other web assets
- rejects HTML error pages and oversized responses
- deduplicates by URL, checksum, and filename
- stores downloads in one folder or in per-domain subfolders
- persists crawler state to avoid re-downloading the same files

## Project structure

- `site_document_unloader/` - package source code
- `config.example.yaml` - example configuration
- `requirements.txt` - Python dependencies
- `site_document_unloader.spec` - PyInstaller one-folder build configuration
- `pyinstaller_runtime_hook.py` - runtime hook that points Playwright to bundled browsers
- `start.bat` - Windows launcher for the packaged executable

## Installation

```powershell
cd "C:\Users\Aleksey.Chernov\Desktop\Бюджет закупок\CSV_Export\Website documents unloading"
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

## Run

The CLI is unchanged:

```powershell
python -m site_document_unloader --config config.yaml
```

or

```powershell
python -m site_document_unloader --config config.example.yaml
```

## Portable Windows build

The CLI stays unchanged. The packaged executable is started with the same argument:

```bat
site_document_unloader.exe --config config.yaml
```

### Build prerequisites

Only the build machine needs Python installed. The end user does not.

```powershell
cd "C:\Users\Aleksey.Chernov\Desktop\Р‘СЋРґР¶РµС‚ Р·Р°РєСѓРїРѕРє\CSV_Export\Website documents unloading"
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install pyinstaller
```

Install Chromium for Playwright before building. One of the two variants below must be used:

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH="0"
playwright install chromium
```

or, if browsers are already installed into the default Playwright cache:

```powershell
playwright install chromium
```

Build the portable one-folder bundle:

```powershell
pyinstaller --noconfirm --clean site_document_unloader.spec
```

What the spec does:

- builds `one-folder`, not `one-file`
- keeps the existing CLI entrypoint
- includes `config.yaml` and `start.bat` in the distribution folder
- bundles Playwright Python package data
- copies Playwright Chromium runtime from either:
  - `PLAYWRIGHT_BROWSERS_PATH`
  - `playwright/driver/package/.local-browsers`
  - `%LOCALAPPDATA%\ms-playwright`

### Run without Python

After the build, the end user runs the packaged app without installing Python:

```powershell
cd .\dist\site_document_unloader
.\start.bat
```

Equivalent direct command:

```powershell
.\site_document_unloader.exe --config config.yaml
```

### Files to transfer to the user

Copy these items to the target Windows machine:

- `dist\site_document_unloader\...`
- `config.yaml`
- `start.bat`

`site_document_unloader.spec` already copies `config.yaml` and `start.bat` into `dist\site_document_unloader\`. If needed, replace `config.yaml` there with the final user-specific configuration before handing off the package.

Recommended layout on the user machine:

```text
site_document_unloader\
  site_document_unloader.exe
  start.bat
  config.yaml
  ms-playwright\
  _internal\
```

## Configuration

Copy `config.example.yaml` to `config.yaml` and update `urls`.

Main options:

- `headless` - run browser without UI
- `timeout_ms` - Playwright timeout for page load and requests
- `wait_until` - Playwright `goto(..., wait_until=...)` mode
- `max_depth` - crawl depth for internal pages
- `max_pages_per_domain` - page limit per domain
- `follow_subdomains` - allow subdomain traversal
- `documents_only` - strict mode that allows exploration but saves only real document-like files
- `allowed_extensions` - file extensions treated as documents
- `blocked_extensions` - denylist for images, scripts, styles, fonts, media, and other web assets
- `blocked_content_types` - denylist for HTML and generic web asset MIME types
- `download_directory` - target directory for downloaded files
- `state_file` - JSON state file for deduplication
- `log_file` - crawler log path
- `max_file_size_mb` - maximum accepted file size in MB
- `group_by_domain` - when `true`, save files into `download_directory/<domain>/`
- `max_clicks_per_page` - maximum relevant download-like clicks per page
- `max_document_controls_per_page` - base limit for multilingual document-related UI controls per page
- `max_language_variants_per_page` - cap for alternate language versions of the same relevant page
- `document_page_bonus_clicks` - extra clicks allowed on detected document-rich pages
- `document_page_bonus_depth` - extra crawl depth allowed below detected document-rich pages
- `max_links_enqueued_per_page` - cap for prioritized internal links added from one page
- `save_state_every_n_files` - buffered state persistence threshold; `null` disables periodic flush and keeps end-of-page/domain/run saves
- `network_capture_enabled` - enable saving document-like browser network responses
- `post_click_rescan` - rescan DOM and internal links after a relevant click
- `document_keywords` - multilingual document-control keywords used for controls, sections, scoring, and prioritization
- `section_keywords` - multilingual section/header hints for document-rich areas
- `positive_url_patterns` - URL fragments that raise queue priority
- `negative_url_patterns` - URL fragments that lower priority or exclude pages
- `language_switcher_hints` - hints for detecting alternate language variants
- `product_page_hints` - hints for identifying product or catalog detail pages

Example:

```yaml
headless: true
timeout_ms: 30000
wait_until: networkidle
max_depth: 2
max_pages_per_domain: 50
follow_subdomains: false
documents_only: true

allowed_extensions:
  - .pdf
  - .xls
  - .xlsx
  - .csv
  - .doc
  - .docx
  - .zip

blocked_extensions:
  - .jpg
  - .jpeg
  - .png
  - .webp
  - .gif
  - .svg
  - .ico
  - .js
  - .css
  - .map
  - .woff
  - .woff2
  - .ttf
  - .mp3
  - .mp4

blocked_content_types:
  - text/html
  - image/
  - text/css
  - application/javascript
  - text/javascript
  - font/
  - audio/
  - video/

download_directory: ./downloads
state_file: ./state/download-state.json
log_file: ./logs/downloader.log

max_file_size_mb: 100
group_by_domain: true
max_clicks_per_page: 25
max_document_controls_per_page: 25
max_language_variants_per_page: 2
document_page_bonus_clicks: 8
document_page_bonus_depth: 1
max_links_enqueued_per_page: 20
save_state_every_n_files: 20
network_capture_enabled: true
post_click_rescan: true

urls:
  - https://example.com
```

## Detection logic

The crawler now finds documents from four main sources:

1. `dom_link`
   Direct links found in `href`, `src`, `data-*`, inline handlers, and other DOM text.

2. `click_download`
   Browser downloads triggered by clicking multilingual document controls, tabs, accordion sections, summaries, and other document-like UI controls.

3. `network_response`
   Browser responses intercepted while the page is loading or reacting to user actions. This covers many JavaScript-driven download flows, including XHR and fetch responses that return documents without exposing a direct link in the DOM.

4. `page_context`
   Same-context browser navigation for document URLs that reject plain direct requests and need cookies, referer, or session state.

## Crawl prioritization

The crawler no longer treats the site as a plain FIFO queue.

It scores and prioritizes pages using signals such as:

- multilingual document keywords in URL, title, anchor text, headings, and section text
- product-page hints
- document-rich sections like `Documents`, `Brochures`, `Certificates`, `Kataloglar`, `Belgeleri`, `Документы`
- repeated document links or repeated document-related controls

It de-prioritizes or rejects links such as:

- login, register, privacy, cookie, cart, account
- `/producttags/`, `/tags/`, `/news`, `/blog`, `/contact`, `/search`
- obvious social links and non-content utility links
- language switchers as generic crawl targets

Language variants are opened only for document-rich pages and only up to `max_language_variants_per_page`.

## Response validation

Before saving a file, the crawler checks:

- HTTP status
- `Content-Type`
- `Content-Length` when available
- actual body size after download
- whether the body looks like HTML instead of a document

The crawler rejects responses such as:

- HTML pages returned instead of files
- empty bodies
- oversized files above `max_file_size_mb`
- obviously unsupported content types

Common rejection reasons in the log:

- `blocked_extension`
- `blocked_content_type`
- `blocked_negative_url_pattern`
- `skipped_non_document_asset`
- `skipped_navigation_page`
- `skipped_tag_page`
- `rejected_html_response`
- `rejected_empty_body`
- `rejected_size_limit`
- `rejected_content_type`
- `not_document_like`
- `http_status_403`
- `http_status_404`
- `download_strategy_failed_direct`
- `download_strategy_failed_page_context`
- `download_strategy_failed_browser`
- `download_strategy_failed_network_capture`

## Logging

The crawler writes regular logs plus structured document events in a predictable JSON payload:

- `page_url`
- `document_url`
- `internal_id`
- `filename`
- `discovery_method`
- `language_context`
- `download_strategy`
- `result`
- `reason`
- `content_type`
- `size`

The crawler also writes queue events for crawl prioritization decisions, including score, depth, reason, and whether a URL was queued or rejected.

## State persistence

State is no longer written after every single file by default.

It is saved:

- periodically according to `save_state_every_n_files`
- at the end of each page
- at the end of each domain
- at normal crawler shutdown

This reduces unnecessary disk writes while keeping enough protection against repeated downloads.

## Download folders

When `group_by_domain: true`, files are stored like this:

```text
downloads/
  example.com/
    report.pdf
  sub.example.com/
    export.xlsx
```

When `group_by_domain: false`, the previous flat directory behavior is used.

## Limitations

- Sites that require authentication, CAPTCHA, or complex multi-step flows may still need site-specific handling.
- Browser network interception helps with many JavaScript-driven downloads, but not every blob-based flow exposes a reusable response body.
- Very dynamic sites can still hide documents behind custom UI states that are not reachable through safe generic clicks.
- The crawler intentionally avoids aggressive URL normalization and does not remove query parameters, because signed URLs often depend on them.
