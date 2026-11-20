"""Verify two isolated live workspaces without saving their keys or browser state."""

import argparse
import os
import re

from playwright.sync_api import expect, sync_playwright


def workspace(page, url):
    page.goto(url)
    page.get_by_role("button", name="Create free workspace").click()
    key = page.locator("code.workspace-key")
    key.wait_for(timeout=30000)
    value = key.inner_text()
    if not re.fullmatch(r"rtw_[\w-]{43}", value):
        raise RuntimeError("Workspace creation returned an invalid key")
    page.get_by_role("button", name="Open my workspace").click()
    expect(page.get_by_role("heading", name="Your release workspace")).to_be_visible(timeout=30000)
    return value


def verify(url):
    with sync_playwright() as driver:
        with driver.chromium.launch(channel=os.getenv("RELEASETRACE_TEST_BROWSER_CHANNEL")) as browser:
            alice, bob = [browser.new_context().new_page() for _ in range(2)]
            key = workspace(alice, url)
            other = workspace(bob, url)
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
            alice.get_by_role("link", name="sujayv26/release-trace", exact=True).click()
            expect(alice.get_by_role("heading", name="Recent workflow runs")).to_be_visible(timeout=30000)
            alice.get_by_role("button", name="Remove", exact=True).click()
            expect(alice.get_by_text("Enter your first repository above", exact=False)).to_be_visible()
            for page in (alice, bob):
                page.get_by_role("button", name="Sign out").click()
            print("Verified separate keys, isolated saved repositories, revoked session, sign-in and removal.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    verify(parser.parse_args().url)
