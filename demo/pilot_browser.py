"""Exercise production dashboard sessions in disposable Chromium contexts."""

import os
import json
import re
from time import time

from playwright.sync_api import Error, sync_playwright

from service.accounts import key_hash
from service.smoke_demo import require

COOKIE = "__Host-releasetrace-session"


def credential(token):
    return {"Authorization": f"Bearer {token}"}


def check_cache(response):
    require(response is not None and "no-store" in response.headers.get("cache-control", ""),
            "Dashboard response is missing no-store")


def sign_in(page, key, handle):
    check_cache(page.goto("/"))
    page.get_by_label("Access key").fill(key)
    page.get_by_role("button", name="Open dashboard").click()
    page.get_by_role("button", name="Sign out").wait_for()
    page.get_by_text(re.compile(rf"{handle} ·")).wait_for()
    cookies = [cookie for cookie in page.context.cookies() if cookie["name"] == COOKIE]
    require(len(cookies) == 1, "Browser did not store exactly one production session cookie")
    cookie = cookies[0]
    require(cookie["secure"] and cookie["httpOnly"] and cookie["sameSite"] == "Strict" and cookie["path"] == "/"
            and cookie["domain"] == "127.0.0.1", "Incorrect production cookie attributes")
    require(3540 < cookie["expires"] - time() <= 3610, "Browser cookie lifetime is not one hour")
    token = cookie["value"]
    require(re.fullmatch(r"rts_[A-Za-z0-9_-]{43}", token) and token != key, "Browser cookie is not opaque")
    require(COOKIE not in page.evaluate("document.cookie"), "Page JavaScript can read the session cookie")
    require(key not in page.url and key not in page.content() and token not in page.url and token not in page.content(),
            "Dashboard exposed credentials")
    return token


def cross_origin_sign_in(page, dashboard_url, other_origin, key):
    page.goto("/")
    form = page.locator("form").filter(has=page.get_by_label("Access key"))
    fields = form.locator("input[type=hidden]").evaluate_all("(inputs) => inputs.map(i => [i.name, i.value])")
    require(fields, "Sign-in form is missing its server action")
    page.goto(other_origin + "/healthz")
    with page.expect_navigation(wait_until="domcontentloaded") as navigation:
        page.evaluate("""({ destination, fields }) => {
            const form = document.createElement('form');
            form.method = 'POST';
            form.enctype = 'multipart/form-data';
            form.action = destination;
            for (const [name, value] of fields) {
                const input = document.createElement('input');
                input.type = 'hidden';
                input.name = name;
                input.value = value;
                form.append(input);
            }
            document.body.append(form);
            setTimeout(() => form.requestSubmit(), 0);
        }""", {"destination": dashboard_url + "/", "fields": [*fields, ["token", key]]})
    response = navigation.value
    require(response is not None and response.status >= 400
            and response.request.headers.get("origin") == other_origin
            and not page.context.cookies(), "Cross-origin sign-in was not rejected")


def exercise_browser(compose, dashboard_url, api, alpha_key, beta_key, beta_repository):
    account = ("exec", "-T", "api", "python", "-m", "service.accounts")
    other_origin = str(api.base_url).rstrip("/")
    try:
        with sync_playwright() as playwright:
            # CI uses Chrome from the runner image. Local runs use bundled Chromium.
            browser = playwright.chromium.launch(channel=os.getenv("RELEASETRACE_TEST_BROWSER_CHANNEL"))
            alpha, beta, foreign = [browser.new_context(base_url=dashboard_url).new_page() for _ in range(3)]
            observed_urls = []
            for page in (alpha, beta, foreign):
                page.on("request", lambda request: observed_urls.append(request.url))
            cross_origin_sign_in(foreign, dashboard_url, other_origin, alpha_key)
            alpha_token = sign_in(alpha, alpha_key, "alpha")
            beta_token = sign_in(beta, beta_key, "beta")
            for page, own, foreign_run in ((alpha, "alpha", "beta"), (beta, "beta", "alpha")):
                response = page.goto(f"/runs/pilot-{own}")
                check_cache(response)
                page.get_by_role("heading", name="Release decision").wait_for()
                require(page.goto(f"/runs/pilot-{foreign_run}").status == 404, "Browser read another project")
            beta.goto("/runs/pilot-beta")
            require(beta.get_by_role("button", name="Approve candidate").count() == 0,
                    "Viewer was shown the approval action")
            denied = api.post("/v1/runs/pilot-beta/approval", headers=credential(beta_token),
                              json={"decision": "approve", "image_digest": "sha256:" + "0" * 64})
            require(denied.status_code == 403 and denied.headers.get("cache-control") == "no-store",
                    "Viewer approval was not rejected without caching")

            alpha.get_by_role("button", name="Sign out").click()
            alpha.get_by_label("Access key").wait_for()
            require(not alpha.context.cookies()
                    and api.get("/v1/me", headers=credential(alpha_token)).status_code == 401,
                    "Logout did not clear and revoke the session")

            expired = sign_in(alpha, alpha_key, "alpha")
            # Age only this disposable database row, leaving the live cookie unchanged.
            statement = ("UPDATE browser_sessions SET expires_at = CURRENT_TIMESTAMP - INTERVAL '1 second' "
                         f"WHERE token_hash = '{key_hash(expired)}';\n")
            compose("exec", "-T", "db", "psql", "-X", "-v", "ON_ERROR_STOP=1", "-U", "releasetrace",
                    "-d", "releasetrace", input=statement.encode())
            require(any(cookie["value"] == expired for cookie in alpha.context.cookies()),
                    "Expiry check must start with a retained browser cookie")
            check_cache(alpha.reload())
            alpha.get_by_label("Access key").wait_for()
            require(api.get("/v1/me", headers=credential(expired)).status_code == 401,
                    "Server accepted an expired session")

            compose(*account, "revoke", "beta", str(beta_repository))
            require(beta.reload().status == 404, "Browser retained revoked project access")
            beta.goto("/")
            beta.get_by_role("link", name="Request project access").click()
            beta.get_by_label("Repository ID").fill(str(beta_repository))
            beta.get_by_role("button", name="Request access").click()
            beta.get_by_text(f"Repository {beta_repository} · pending", exact=True).wait_for()
            requests_cli = ("exec", "-T", "api", "python", "-m", "service.access_requests")
            pending = json.loads(compose(*requests_cli, "list", capture=True))
            request = next(row for row in pending if row["handle"] == "beta" and row["repository_id"] == beta_repository)
            compose(*requests_cli, "approve", request["id"], "--operator", "pilot-operator")
            check_cache(beta.reload())
            beta.get_by_text(f"Repository {beta_repository} · approved", exact=True).wait_for()
            beta.get_by_role("link", name="Release history", exact=True).click()
            beta.get_by_text(re.compile(r"beta · viewer")).wait_for()
            rotated_key = compose(*account, "rotate", "beta", capture=True).strip()
            beta.goto("/")
            beta.get_by_label("Access key").wait_for()
            require(api.get("/v1/me", headers=credential(beta_token)).status_code == 401
                    and api.get("/v1/me", headers=credential(beta_key)).status_code == 401,
                    "Rotation did not revoke the old key and session")
            rotated_token = sign_in(beta, rotated_key, "beta")
            compose(*account, "disable", "beta")
            beta.reload()
            beta.get_by_label("Access key").wait_for()
            require(api.get("/v1/me", headers=credential(rotated_token)).status_code == 401
                    and api.get("/v1/me", headers=credential(rotated_key)).status_code == 401,
                    "Disablement did not revoke the current key and session")
            secrets = (alpha_key, beta_key, rotated_key, alpha_token, beta_token, expired, rotated_token)
            require(not any(secret in url for secret in secrets for url in observed_urls), "A request URL contained a credential")
            logs = compose("logs", "--no-color", "dashboard", "api", capture=True)
            require(not any(secret in logs for secret in secrets), "A service log contained a credential")
    except Error as error:
        # Browser exceptions can contain form values. Emit only the failure class.
        raise RuntimeError(f"Chromium rehearsal failed ({type(error).__name__})") from None
    return {"engine": "chromium", "cookie": "secure/http-only/strict", "cross_origin_sign_in": "rejected",
            "logout": "revoked", "controlled_expiry": "rejected", "cross_project_reads": [404, 404],
            "viewer_approval": 403, "project_revocation": 404, "rotation": 401, "disablement": 401}
