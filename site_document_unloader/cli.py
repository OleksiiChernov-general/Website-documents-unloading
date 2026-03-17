import argparse
import yaml
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    print("🚀 Запуск site_document_unloader")
    print(f"Config file: {args.config}")

    # читаем конфиг
    with open(args.config, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    print("✅ Конфиг загружен:")
    print(config)

    # тестовый запуск playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://example.com")
        print("🌐 Страница открыта:", page.title())
        browser.close()

    print("🎉 Готово!")
