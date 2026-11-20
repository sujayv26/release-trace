"""Verify two isolated live workspaces without saving their keys or browser state."""

import argparse
import base64
import hashlib
import hmac
import os
import re
import time

from playwright.sync_api import expect, sync_playwright


def authenticator_code(secret, step):
    digest = hmac.digest(base64.b32decode(secret), step.to_bytes(8, "big"), hashlib.sha1)
    offset = digest[-1] & 15
    value = int.from_bytes(digest[offset:offset + 4], "big") & 0x7fffffff
    return f"{value % 1_000_000:06d}"


def workspace(page, url):
    page.goto(url)
    page.get_by_role("button", name="Create free workspace").click()
    key = page.locator("code.workspace-key")
    key.wait_for(timeout=30000)
    value = key.inner_text()
    if not re.fullmatch(r"rtw_[\w-]{43}", value):
        raise RuntimeError("Workspace creation returned an invalid key")
    secret = page.locator(".authenticator-secret").inner_text()
    step = int(time.time()) // 30
    code = authenticator_code(secret, step)
    assert not any(c["name"] == "__Host-rt-tracker" for c in page.context.cookies())
    page.get_by_label("Authenticator code").fill(code)
    page.get_by_role("button", name="Verify and open workspace").click()
    expect(page.get_by_role("heading", name="Your release workspace")).to_be_visible(timeout=30000)
    return value, secret, step


def verify(url):
    with sync_playwright() as driver:
        with driver.chromium.launch(channel=os.getenv("RELEASETRACE_TEST_BROWSER_CHANNEL")) as browser:
            alice, bob = [browser.new_context().new_page() for _ in range(2)]
            key, secret, step = workspace(alice, url)
            other, _, _ = workspace(bob, url)
            assert key != other
            alice.get_by_label("GitHub repository").fill("https://github.com/sujayv26/release-trace")
            alice.get_by_role("button", name="Track repository", exact=True).click()
            expect(alice.get_by_role("heading", name="Recent workflow runs")).to_be_visible(timeout=45000)
            runs = alice.locator('a[href*="github.com/sujayv26/release-trace/actions/runs/"]')
            assert runs.count() > 0
            print(f"GitHub returned {runs.count()} workflow runs; " +
                  " / ".join(alice.locator(".metric").all_inner_texts()))
            selected = alice.url
            bob.goto(selected)
            expect(bob.get_by_text("Enter your first repository above", exact=False)).to_be_visible()
            expect(bob.get_by_role("heading", name="Recent workflow runs")).to_have_count(0)
            original = alice.context.cookies()
            session = next(c for c in original if c["name"] == "__Host-rt-tracker")
            assert session["secure"] and session["httpOnly"] and session["sameSite"] == "Strict"
            alice.get_by_role("button", name="Sign out").click()
            expect(alice.get_by_role("button", name="Sign in", exact=True)).to_be_visible()
            alice.context.add_cookies([session])
            alice.goto(selected)
            expect(alice.get_by_role("heading", name="Your release workspace")).to_have_count(0)
            alice.get_by_label("Workspace key").fill(key)
            alice.get_by_role("button", name="Sign in", exact=True).click()
            expect(alice.get_by_role("alert").filter(has_text="Sign-in failed")).to_be_visible()
            expect(alice.locator(".authenticator-secret")).to_have_count(0)
            alice.get_by_label("Workspace key").fill(key)
            alice.get_by_label("Authenticator code").fill(authenticator_code(secret, step))
            alice.get_by_role("button", name="Sign in", exact=True).click()
            expect(alice.get_by_role("button", name="Sign in", exact=True)).to_be_enabled()
            expect(alice.get_by_role("heading", name="Your release workspace")).to_have_count(0)
            # A previously consumed code stays invalid throughout its time window.
            while int(time.time()) // 30 <= step:
                time.sleep(0.5)
            alice.get_by_label("Workspace key").fill(key)
            alice.get_by_label("Authenticator code").fill(authenticator_code(secret, int(time.time()) // 30))
            alice.get_by_role("button", name="Sign in", exact=True).click()
            alice.get_by_role("link", name="sujayv26/release-trace", exact=True).click()
            expect(alice.get_by_role("heading", name="Recent workflow runs")).to_be_visible(timeout=30000)
            alice.get_by_role("button", name="Remove", exact=True).click()
            expect(alice.get_by_text("Enter your first repository above", exact=False)).to_be_visible()
            for page in (alice, bob):
                page.get_by_role("button", name="Sign out").click()
            print("Verified authenticator enrollment, key-only and reused-code denial, fresh-code sign-in, "
                  "isolated saved repositories, revoked session and removal.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    verify(parser.parse_args().url)
