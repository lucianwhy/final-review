"""Isolated Chromium conversion, bounded by the export worker's subprocess deadline."""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    document, output, executable = sys.argv[1:]
    with sync_playwright() as playwright:
        options = {"headless": True}
        if executable:
            options["executable_path"] = executable
        with playwright.chromium.launch(**options) as browser:
            page = browser.new_page(java_script_enabled=False)
            page.route("**/*", lambda route: route.abort())
            page.set_content(
                Path(document).read_text(encoding="utf-8"), wait_until="load", timeout=30000
            )
            page.emulate_media(media="print")
            page.evaluate("document.fonts.ready")
            page.pdf(
                path=output,
                format="A4",
                prefer_css_page_size=True,
                print_background=True,
                display_header_footer=True,
                header_template="<span></span>",
                footer_template=(
                    '<div style="font-size:8px;width:100%;text-align:center">'
                    '<span class="pageNumber"></span> / '
                    '<span class="totalPages"></span></div>'
                ),
            )


if __name__ == "__main__":
    main()
