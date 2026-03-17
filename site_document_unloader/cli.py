from __future__ import annotations

import argparse
import logging
from pathlib import Path

from site_document_unloader.config import load_config
from site_document_unloader.crawler import DocumentCrawler


def _setup_logging(log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find and download documents from websites."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to YAML config file.",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    _setup_logging(config.log_file)

    logging.getLogger(__name__).info("Starting site_document_unloader")
    logging.getLogger(__name__).info("Using config: %s", args.config)

    crawler = DocumentCrawler(config)
    crawler.run()

    logging.getLogger(__name__).info("site_document_unloader finished successfully")


if __name__ == "__main__":
    main()
