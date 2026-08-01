"""End-to-end smoke test for the flashcards backend security fixes.

Run with: python test_security.py

This exercises the new account-management endpoints and the security
hardening added in this round of fixes. It is intentionally self-contained
and uses only the stdlib so it can run anywhere.
"""

import json
import time
import urllib.request
import urllib.error

BASE = "http://localhost:8000/api"

# Unique suffix so the test can be re-run without hitting "username exists".
SUFFIX = str(int(time.time()))
USERNAME = f"testsec_{SUFFIX}"
EMAIL = f"testsec_{SUFFIX}@example.com"
PASSWORD = "S3cure!Passw0rd#2026"
NEW_PASSWORD = "BrandNew!2026#Secure"


def call(method, path, body=None, token=None):
    """Make an HTTP call and return (status, parsed_json_or_text)."""
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req)
        raw = r.read().decode()
        try:
            return r.status, json.loads(raw)
        except json.JSONDecodeError:
            return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def expect(label, got, expected, ok_if=None):
    """Tiny assertion helper that prints PASS/FAIL."""
    if ok_if is not None:
        passed = ok_if(got)
    else:
        passed = got == expected
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {label}  -> got {got!r}")
    if not passed:
        print(f"        expected: {expected!r}")
    return passed


def main():
    results = []

    # 1. Health check returns DB status
    s, body = call("GET", "/health/")
    results.append(expect("health check status=ok", s, 200))
    results.append(expect("health check db=true", body.get("db") if isinstance(body, dict) else None, True))

    # 2. Register: reject weak password via Django validators
    s, body = call("POST", "/auth/register/", {
        "username": f"{USERNAME}_weak",
        "password": "abcdefgh",
        "email": "",
    })
    results.append(expect("register rejects common password", s, 400))
    if isinstance(body, dict):
        print(f"        message: {body.get('error')!r}")

    # 3. Register: reject short password
    s, body = call("POST", "/auth/register/", {
        "username": f"{USERNAME}_short",
        "password": "123",
        "email": "",
    })
    results.append(expect("register rejects short password", s, 400))

    # 4. Register: success with strong password
    s, body = call("POST", "/auth/register/", {
        "username": USERNAME,
        "password": PASSWORD,
        "email": EMAIL,
    })
    results.append(expect("register with strong password returns 201", s, 201))
    access = body.get("access") if isinstance(body, dict) else None
    refresh = body.get("refresh") if isinstance(body, dict) else None
    results.append(expect("register returns access token", bool(access), True))
    results.append(expect("register returns refresh token", bool(refresh), True))

    # 5. Register: duplicate username rejected
    s, body = call("POST", "/auth/register/", {
        "username": USERNAME,
        "password": "AnotherStrong!2026",
        "email": f"other_{SUFFIX}@example.com",
    })
    results.append(expect("register rejects duplicate username", s, 400))

    # 6. Login with wrong password
    s, body = call("POST", "/auth/login/", {
        "username": USERNAME,
        "password": "wrongpassword",
    })
    results.append(expect("login with wrong password returns 401", s, 401))

    # 7. Login with correct password
    s, body = call("POST", "/auth/login/", {
        "username": USERNAME,
        "password": PASSWORD,
    })
    results.append(expect("login with correct password returns 200", s, 200))
    access = body.get("access") if isinstance(body, dict) else None
    refresh = body.get("refresh") if isinstance(body, dict) else None

    # 8. GET /auth/me/ with token
    s, body = call("GET", "/auth/me/", token=access)
    results.append(expect("GET /auth/me/ returns 200", s, 200))
    results.append(expect("/auth/me/ returns correct username",
                          body.get("username") if isinstance(body, dict) else None,
                          USERNAME))

    # 9. PATCH /auth/me/ to update email
    new_email = f"newmail_{SUFFIX}@example.com"
    s, body = call("PATCH", "/auth/me/", {"email": new_email}, token=access)
    results.append(expect("PATCH /auth/me/ returns 200", s, 200))
    results.append(expect("/auth/me/ updates email",
                          body.get("email") if isinstance(body, dict) else None,
                          new_email))

    # 10. Change password: wrong current password
    s, body = call("POST", "/auth/change-password/", {
        "current_password": "wrongcurrent",
        "new_password": NEW_PASSWORD,
    }, token=access)
    results.append(expect("change-password rejects wrong current", s, 400))

    # 11. Change password: new == current
    s, body = call("POST", "/auth/change-password/", {
        "current_password": PASSWORD,
        "new_password": PASSWORD,
    }, token=access)
    results.append(expect("change-password rejects same password", s, 400))

    # 12. Change password: success
    s, body = call("POST", "/auth/change-password/", {
        "current_password": PASSWORD,
        "new_password": NEW_PASSWORD,
    }, token=access)
    results.append(expect("change-password succeeds", s, 200))
    new_access = body.get("access") if isinstance(body, dict) else None
    results.append(expect("change-password returns new access token", bool(new_access), True))

    # 13. Old refresh token should now be blacklisted
    s, body = call("POST", "/auth/refresh/", {"refresh": refresh})
    results.append(expect("old refresh token is blacklisted", s, 406,
                          ok_if=lambda got: got in (401, 406)))

    # 14. Logout: blacklist the new refresh token
    s, body = call("POST", "/auth/login/", {
        "username": USERNAME,
        "password": NEW_PASSWORD,
    })
    new_refresh = body.get("refresh") if isinstance(body, dict) else None
    s, body = call("POST", "/auth/logout/", {"refresh": new_refresh}, token=new_access)
    results.append(expect("logout returns 204", s, 204))

    # 15. Logout: blacklisted refresh can't be used again
    s, body = call("POST", "/auth/refresh/", {"refresh": new_refresh})
    results.append(expect("refresh after logout rejected", s, 406,
                          ok_if=lambda got: got in (401, 406)))

    # 16. Category create + rename + delete
    s, body = call("POST", "/categories/", {"name": "TestCategory"}, token=new_access)
    results.append(expect("create category returns 201", s, 201))
    cat_id = body.get("id") if isinstance(body, dict) else None

    s, body = call("PATCH", f"/categories/{cat_id}/", {"name": "RenamedCategory"}, token=new_access)
    results.append(expect("rename category returns 200", s, 200))
    results.append(expect("rename category updates name",
                          body.get("name") if isinstance(body, dict) else None,
                          "RenamedCategory"))

    s, body = call("DELETE", f"/categories/{cat_id}/", token=new_access)
    results.append(expect("delete category returns 204", s, 204))

    # Summary
    print()
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"=== {passed}/{total} checks passed ===")
    if passed != total:
        print("FAILURES DETECTED")


if __name__ == "__main__":
    main()
