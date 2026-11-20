"""Walk the anonymous checkout demo in a fresh Chromium context."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.parse import urljoin

from playwright.sync_api import expect, sync_playwright

from demo.public_site import build


def verify(url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=os.getenv("RELEASETRACE_TEST_BROWSER_CHANNEL"))
        context = browser.new_context(java_script_enabled=False, viewport={"width": 390, "height": 844})
        page = context.new_page()
        assert page.goto(url).ok
        expect(page.get_by_text("Synthetic demo data")).to_be_visible()
        page.get_by_role("link", name="Blocked candidate", exact=False).click()
        expect(page.locator("article code")).to_have_text(["checkout-rollback-v2", "checkout-rollback-v1"])
        page.get_by_role("link", name="Open corrected passing run", exact=False).click()
        expect(page.locator(".decision-hero")).to_have_attribute("data-verdict", "pass")
        expect(page.locator("article code")).to_have_text(["checkout-rollback-v2", "checkout-rollback-v2"])
        assert page.locator("form, input, button").count() == 0
        private = context.request.get(urljoin(url, "v1/projects/5101/runs"))
        mutation = context.request.post(urljoin(url, "v1/runs/public-demo-probe/approval"))
        assert private.status in (401, 403, 404), private.status
        assert mutation.status in (401, 403, 404, 405, 501), mutation.status
        print(f"Fresh browser verified blocked and corrected citations; private read {private.status}, "
              f"approval POST {mutation.status}.")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Published walkthrough directory URL, including trailing slash")
    args = parser.parse_args()
    if args.url:
        verify(args.url)
    else:
        with TemporaryDirectory() as tmp:
            output = Path(tmp)
            build(output)
            assert {file.name for file in output.iterdir()} == {
                "index.html", "styles.css", "stale-candidate.html", "corrected-candidate.html",
            }
            with ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=tmp)) as server:
                Thread(target=server.serve_forever, daemon=True).start()
                try:
                    verify(f"http://127.0.0.1:{server.server_port}/")
                finally:
                    server.shutdown()
