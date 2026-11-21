"""
End-to-end in a real headless browser (Edge or Chrome) over the DevTools protocol, emulating phones.
  set ADMIN_PASSWORD=sim-admin and ADMIN_TOTP_SECRET=JBSWY3DPEHPK3PXP   (the server must use these)
  python -m eval.browser_test http://127.0.0.1:8765 [screenshot_dir]
Server: run with a throwaway IMPACT_DB.

Flows (on an emulated iPhone):
  regular person, no account -> name, save, undo, offline save + send later, admin pages refused
  regular person makes an account -> note credited, My notes, mark done
  admin -> dashboard, Done, report, connect, team (make admin), console, phone simulator
Then every page on iPhone, Android and desktop, signed out and as admin, light and dark:
  no sideways scrolling, no JavaScript errors, the right tabs, a screenshot of each.
"""
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

from app import mfa

sys.stdout.reconfigure(encoding="utf-8")
BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            os.getenv("CHROME_BIN") or "", shutil.which("google-chrome") or "", shutil.which("microsoft-edge") or "",
            shutil.which("chromium") or ""]  # chromium last: on Ubuntu it's often a snap wrapper that misbehaves headless
PORT = 9333
ADMIN_PW = os.getenv("ADMIN_PASSWORD", "sim-admin")
ADMIN_TOTP = os.getenv("ADMIN_TOTP_SECRET", "JBSWY3DPEHPK3PXP")  # admins need two-step; the server must use the same
DEVICES = {
    "iphone": dict(width=390, height=844, deviceScaleFactor=3, mobile=True,
                   ua="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"),
    "android": dict(width=412, height=915, deviceScaleFactor=2.625, mobile=True,
                    ua="Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"),
    # small/cheap phones (iPhone SE 1st gen, budget Android), and a stand-in for phones set to bigger text
    "small-phone": dict(width=320, height=640, deviceScaleFactor=2, mobile=True,
                        ua="Mozilla/5.0 (Linux; Android 12; SM-A035F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"),
    "desktop": dict(width=1280, height=800, deviceScaleFactor=1, mobile=False, ua=""),
}
PUBLIC_PAGES = ["/", "/phone", "/login", "/me", "/security"]
ADMIN_PAGES = ["/dashboard", "/report", "/connect", "/team", "/console"]


def admin_only_cookie(cookies: dict) -> bool:
    return any(c["name"] == "it_session" for c in cookies.get("cookies", []))


class Page:
    def __init__(self, ws):
        self.ws, self.n, self.errors = ws, 0, []

    async def call(self, method, **params):
        self.n += 1
        my = self.n
        await self.ws.send(json.dumps({"id": my, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("method") == "Runtime.exceptionThrown":
                d = msg["params"]["exceptionDetails"]
                self.errors.append(d.get("exception", {}).get("description", d.get("text", "error"))[:200])
            if msg.get("id") == my:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", r["exceptionDetails"]))
        return r["result"].get("value")

    async def goto(self, url):
        await self.call("Page.navigate", url=url)
        await asyncio.sleep(0.15)
        await self.wait("document.readyState === 'complete' && window.IMPACT_ME !== undefined")

    async def wait(self, cond, timeout=8):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if await self.js(f"!!({cond})"):
                    return True
            except RuntimeError:
                pass
            await asyncio.sleep(0.1)
        raise TimeoutError(cond)

    async def device(self, name, dark=False):
        d = DEVICES[name]
        await self.call("Emulation.setDeviceMetricsOverride", width=d["width"], height=d["height"],
                        deviceScaleFactor=d["deviceScaleFactor"], mobile=d["mobile"])
        await self.call("Emulation.setTouchEmulationEnabled", enabled=d["mobile"])
        if d["ua"]:
            await self.call("Emulation.setUserAgentOverride", userAgent=d["ua"])
        await self.call("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": "dark" if dark else "light"}])

    async def fresh(self):
        """A new person: no cookies, nothing remembered."""
        await self.call("Network.clearBrowserCookies")
        await self.js("try { localStorage.clear() } catch (e) {}")

    async def offline(self, on: bool):
        await self.call("Network.emulateNetworkConditions", offline=on, latency=0, downloadThroughput=-1, uploadThroughput=-1)

    async def shot(self, path: Path):
        r = await self.call("Page.captureScreenshot", format="png", captureBeyondViewport=False)
        path.write_bytes(base64.b64decode(r["data"]))

    async def tabs(self):
        return await self.js("[...document.querySelectorAll('nav a.tab')].map(a => a.textContent)")


async def main(base: str, shots: Path) -> int:
    exe = next((b for b in BROWSERS if b and os.path.exists(b)), None)
    if not exe:
        print("No Edge/Chrome found; skipping.")
        return 0
    shots.mkdir(parents=True, exist_ok=True)
    profile = tempfile.mkdtemp()
    proc = subprocess.Popen([exe, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
                             "--no-first-run", "--window-size=1280,900",
                             # GitHub's Ubuntu runners block Chrome's sandbox and have a tiny /dev/shm
                             *(["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"] if os.getenv("CI") else []),
                             "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    admin_api = httpx.Client(base_url=base)
    assert admin_api.post("/api/auth/login", json={"name": "admin", "pin": ADMIN_PW}).json() == {"need_code": True}, \
        "server's ADMIN_PASSWORD and ADMIN_TOTP_SECRET must match this script's"
    if time.time() % mfa.STEP > mfa.STEP - 3:  # about to roll over: wait, so the UI sign-in below gets the next code
        time.sleep(4)
    assert admin_api.post("/api/auth/code", json={"code": mfa.code_now(ADMIN_TOTP)}).status_code == 200, "ADMIN_TOTP_SECRET mismatch"
    def count():
        r = admin_api.get("/api/entries?limit=100000")
        assert r.status_code == 200, f"admin API session lost: {r.status_code} {r.text[:120]}"
        return len(r.json())
    fails = 0

    def check(label, ok, detail=""):
        nonlocal fails
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {label}{'  ' + str(detail) if detail not in ('', None) else ''}")

    try:
        targets = []
        for _ in range(200):  # up to 40 s: a cold Chrome start on a CI machine is slow
            try:
                targets = [t for t in httpx.get(f"http://127.0.0.1:{PORT}/json").json() if t["type"] == "page"]
                if targets:
                    break
            except httpx.HTTPError:
                pass
            if proc.poll() is not None:
                raise RuntimeError(f"{exe} exited with code {proc.returncode} before opening the DevTools port")
            await asyncio.sleep(0.2)
        if not targets:
            raise RuntimeError(f"{exe} didn't open DevTools on port {PORT} within 40 s")
        ws_url = targets[0]["webSocketDebuggerUrl"]
        print(f"Browser: {exe}")
        async with websockets.connect(ws_url, max_size=None) as ws:
            p = Page(ws)
            for domain in ("Runtime", "Page", "Network"):
                await p.call(f"{domain}.enable")
            await p.device("iphone")

            # ================= regular person, no account =================
            print("\n-- iPhone · regular person, no account")
            await p.goto(base + "/")
            await p.fresh()
            await p.goto(base + "/")
            check("asks for a name first, says an account is optional",
                  await p.js("!document.getElementById('nameStep').classList.contains('hidden') && document.getElementById('nameStep').textContent.includes('optional')"))
            check("tabs: only Log + Text it, plus Sign in", await p.tabs() == ["Sign in", "Log", "Text it"], await p.tabs())
            await p.js("document.getElementById('nameInput').value='Tester'; document.getElementById('nameForm').requestSubmit()")
            await p.wait("!document.getElementById('logStep').classList.contains('hidden')")
            check("name saved, log step shown", await p.js("document.getElementById('who').textContent") == "Tester")
            await p.shot(shots / "flow-1-log-page.png")

            note = "fed 12 kids at sherman park, sent a mom to 414 life, call her back friday"
            n0 = count()
            await p.js(f"document.getElementById('text').value={json.dumps(note)}; document.getElementById('logForm').requestSubmit()")
            await p.wait("!document.getElementById('done').classList.contains('hidden')")
            fields = await p.js("document.getElementById('fields').innerText")
            check("confirmation shows extracted fields", "12" in fields and "414 LIFE" in fields, fields.replace("\n", " | "))
            check("note saved once", count() == n0 + 1)
            await p.shot(shots / "flow-2-saved.png")
            await p.js("document.getElementById('undo').click()")
            await p.wait("document.getElementById('done').classList.contains('hidden')")
            check("undo works with no account (puts the text back, deletes it)",
                  await p.js("document.getElementById('text').value") == note and count() == n0)
            await p.js("document.getElementById('logForm').requestSubmit()")
            await p.wait("!document.getElementById('done').classList.contains('hidden')")
            check("saved again after undo", count() == n0 + 1)

            # offline: save with no signal, it goes out by itself later, exactly once
            await p.offline(True)
            await p.js("document.getElementById('text').value='cleaned up the lot on center st with 6 teens'; document.getElementById('logForm').requestSubmit()")
            await p.wait("!document.getElementById('queued').classList.contains('hidden')")
            check("offline: note waits on the phone", "1 note waiting" in await p.js("document.getElementById('queued').innerText"))
            await p.shot(shots / "flow-3-offline.png")
            n1 = count()
            await p.offline(False)
            await p.js("window.dispatchEvent(new Event('online'))")
            await p.wait("document.getElementById('queued').innerText.includes('Sent 1')")
            await p.js("window.dispatchEvent(new Event('online'))")  # a second 'online' must not send it again
            await asyncio.sleep(0.5)
            check("back online: sent by itself, counted once", count() == n1 + 1, count() - n1)

            for path in ADMIN_PAGES:
                await p.goto(base + path)
                ok = await p.js("location.pathname === '/login' && document.getElementById('title').textContent === 'Admins only'")
                check(f"admin page {path} -> 'Admins only' sign-in", ok, await p.js("location.pathname"))
            await p.shot(shots / "flow-4-admins-only.png")
            await p.goto(base + "/me")
            check("My notes asks to sign in", await p.js("!document.getElementById('signedOut').classList.contains('hidden')"))

            # ================= regular person makes an account =================
            print("\n-- iPhone · regular person makes an account (optional)")
            await p.goto(base + "/login?next=/")
            await p.js("document.getElementById('tab-up').click()")
            check("make-an-account form remembers the name", await p.js("document.getElementById('name').value") == "Tester")
            await p.js("document.getElementById('name').value='Dee'; document.getElementById('pin').value='4141'; document.getElementById('form').requestSubmit()")
            await p.wait("location.pathname === '/' && window.IMPACT_ME && window.IMPACT_ME.signed_in")
            await p.wait("document.getElementById('who').textContent === 'Dee'")
            check("signed in: skips the name step, uses the account name", True)
            check("tabs: Log, Text it, My notes (no admin tabs)", await p.tabs() == ["Log", "Text it", "My notes"], await p.tabs())
            await p.js("document.getElementById('text').value='took 8 teens to the rec, check on marcus tomorrow'; document.getElementById('logForm').requestSubmit()")
            await p.wait("!document.getElementById('done').classList.contains('hidden')")
            await p.goto(base + "/me")
            await p.wait("document.querySelectorAll('#notes .note').length >= 1")
            check("My notes shows the note", "8 teens" in (await p.js("document.getElementById('notes').innerText")).lower() or
                  await p.js("document.querySelectorAll('#notes .note').length") == 1)
            check("My notes shows the follow-up", await p.js("document.getElementById('fuCount').textContent") == "1")
            await p.shot(shots / "flow-5-my-notes.png")
            await p.js("document.querySelector('#followups button[data-done]').click()")
            await p.wait("document.getElementById('fuCount').textContent === '0'")
            check("marks own follow-up done", True)

            # optional two-step sign-in for a regular person, set up on the phone
            await p.goto(base + "/security")
            await p.wait("!document.getElementById('card').hidden")
            check("two-step: off by default for a worker, with a turn-on button", await p.js("!document.getElementById('off').hidden"))
            check("two-step: worker isn't told it's required", await p.js("document.getElementById('required').classList.contains('hidden')"))
            await p.js("document.getElementById('start').click()")
            await p.wait("!document.getElementById('setup').hidden && document.querySelector('#qr svg')")
            check("two-step: QR code shown", True)
            await p.shot(shots / "flow-5b-2fa-setup.png")
            key = (await p.js("document.getElementById('key').textContent")).replace(" ", "")
            await p.js("document.getElementById('code1').value='000000'; document.getElementById('enable').requestSubmit()")
            await p.wait("document.getElementById('msg1').classList.contains('bad')")
            check("two-step: wrong code explained", "didn't match" in await p.js("document.getElementById('msg1').textContent"))
            await p.js(f"document.getElementById('code1').value={json.dumps(mfa.code_now(key))}; document.getElementById('enable').requestSubmit()")
            await p.wait("!document.getElementById('backup').hidden")
            check("two-step: on, 10 backup codes shown", await p.js("document.querySelectorAll('#codes span').length") == 10)
            await p.shot(shots / "flow-5c-2fa-backup-codes.png")
            await p.js("document.getElementById('continue').click()")
            await p.wait("location.pathname === '/me'")
            check("two-step: continue goes to My notes, still signed in, lock shown",
                  await p.js("window.IMPACT_ME.mfa === true && document.querySelector('nav .who').textContent.includes('🔒')"))
            await p.goto(base + "/dashboard")
            check("worker still can't open the dashboard", await p.js("location.pathname") == "/login")

            # ================= admin =================
            print("\n-- iPhone · admin")
            await p.goto(base + "/")
            await p.wait("document.getElementById('signout')")
            await p.js("document.getElementById('signout').click()")
            await p.wait("location.pathname === '/' && window.IMPACT_ME && !window.IMPACT_ME.signed_in", timeout=8)
            await p.goto(base + "/login?next=/dashboard")
            await p.js(f"document.getElementById('name').value='admin'; document.getElementById('pin').value='wrong'; document.getElementById('form').requestSubmit()")
            await p.wait("document.getElementById('msg').classList.contains('bad')")
            check("wrong admin password refused", "don't match" in await p.js("document.getElementById('msg').textContent"))
            await p.js(f"document.getElementById('pin').value={json.dumps(ADMIN_PW)}; document.getElementById('form').requestSubmit()")
            await p.wait("!document.getElementById('codeForm').classList.contains('hidden')")
            check("right password: asks for the authenticator code, not signed in yet",
                  await p.js("location.pathname === '/login'") and not admin_only_cookie(await p.call("Network.getCookies")))
            await p.shot(shots / "flow-6a-admin-code.png")
            await p.js("document.getElementById('code').value='000000'; document.getElementById('codeForm').requestSubmit()")
            await p.wait("document.getElementById('codeMsg').classList.contains('bad')")
            check("wrong code refused", "didn't work" in await p.js("document.getElementById('codeMsg').textContent"))
            # next 30-second code: the current one was just used by the test's own API sign-in, and codes work once
            await p.js(f"document.getElementById('code').value={json.dumps(mfa.code_now(ADMIN_TOTP, 1))}; document.getElementById('codeForm').requestSubmit()")
            await p.wait("location.pathname === '/dashboard'")
            await p.wait("window.IMPACT_ME && window.IMPACT_ME.admin && document.querySelectorAll('#tiles .tile').length === 6")
            check("admin lands on the dashboard", True)
            check("admin tabs", await p.tabs() == ["Log", "Text it", "Dashboard", "Report", "Connect", "Team", "Console"], await p.tabs())
            await p.shot(shots / "flow-6-dashboard.png")
            open_before = int(await p.js("document.getElementById('fuCount').textContent"))
            await p.js("document.querySelector('#followups button[data-id]').click()")
            await p.wait(f"+document.getElementById('fuCount').textContent === {open_before - 1}")
            check("admin: Done resolves a follow-up", True)
            await p.goto(base + "/report?days=7")
            await p.wait("document.querySelector('#paper h1') !== null")
            check("report renders", await p.js("document.querySelectorAll('.kpis div').length") == 5)
            await p.goto(base + "/connect")
            await p.wait("document.getElementById('csv-url').value.includes('/feeds/')")
            check("connect shows the calendar + spreadsheet links", True)
            check("connect: Siri needs no password step now", await p.js("document.getElementById('siri-auth').classList.contains('hidden')"))
            await p.goto(base + "/team")
            await p.wait("document.querySelectorAll('#rows tr').length >= 1 && document.getElementById('rows').innerText.includes('Dee')")
            check("team lists Dee", True)
            await p.shot(shots / "flow-7-team.png")
            await p.js("document.querySelector('#rows button[data-role]').click()")
            await p.wait("document.getElementById('rows').innerText.includes('Admin')")
            check("team: make Dee an admin", admin_api.get("/api/team").json()["users"][0]["role"] == "admin")
            await p.js("document.querySelector('#rows button[data-role]').click()")
            await p.wait("!document.getElementById('rows').innerText.includes('Make worker')")
            await p.goto(base + "/console")
            await p.wait("document.querySelectorAll('#steps li').length > 3")
            check("console loads its next-steps list", "Merge PR" not in await p.js("document.getElementById('steps').innerText"))
            await p.goto(base + "/phone")
            await p.js("document.getElementById('body').value='HELP'; document.getElementById('f').requestSubmit()")
            await p.wait("document.querySelectorAll('.msg.them').length >= 2")
            check("phone simulator replies", "UNDO" in await p.js("[...document.querySelectorAll('.msg.them')].pop().textContent"))
            check("no JavaScript errors in the flows", not p.errors, p.errors[:3])

            # ================= every page, every device, signed out + admin, light + dark =================
            print("\n-- every page on iPhone / Android / desktop")
            admin_cookie = admin_api.cookies.get("it_session")
            problems = []
            for dev in DEVICES:
                for dark in (False, True):
                    await p.device(dev, dark)
                    for who in ("visitor", "admin"):
                        await p.call("Network.clearBrowserCookies")
                        if who == "admin":
                            await p.call("Network.setCookie", name="it_session", value=admin_cookie, url=base)
                        for path in PUBLIC_PAGES + (ADMIN_PAGES if who == "admin" else []):
                            p.errors.clear()
                            await p.goto(base + path)
                            await asyncio.sleep(0.6)  # let charts and lists fill in
                            label = f"{dev}{'-dark' if dark else ''} {who} {path}"
                            if await p.js("document.documentElement.scrollWidth > window.innerWidth + 1"):
                                problems.append(f"{label}: scrolls sideways")
                            # tabs sit in a row that scrolls sideways on a phone, so only check things outside it
                            if await p.js("[...document.querySelectorAll('nav > a, nav .account a, nav .account button, main button:not(.hidden)')].some(b => { const r = b.getBoundingClientRect(); return r.width && r.right > window.innerWidth + 1; })"):
                                problems.append(f"{label}: a button sticks out")
                            if await p.js("(() => { const t = document.querySelector('nav .tabs'); return t.scrollWidth > t.clientWidth + 1 && getComputedStyle(t).overflowX !== 'auto'; })()"):
                                problems.append(f"{label}: tabs cut off and can't scroll")
                            if p.errors:
                                problems.append(f"{label}: JS error {p.errors[0]}")
                            if dark and await p.js("getComputedStyle(document.body).backgroundColor") == "rgb(246, 245, 242)":
                                problems.append(f"{label}: not dark")
                            if not dark or path in ("/", "/dashboard"):
                                await p.shot(shots / f"{dev}{'-dark' if dark else ''}-{who}-{path.strip('/') or 'log'}.png")
            pages_checked = len(DEVICES) * 2 * (len(PUBLIC_PAGES) * 2 + len(ADMIN_PAGES))
            check(f"{pages_checked} page views: no sideways scroll, nothing cut off, no JS errors, dark mode works",
                  not problems, problems[:8])
    finally:
        proc.terminate()
        shutil.rmtree(profile, ignore_errors=True)
    print(f"\nScreenshots: {shots}")
    print(f"{fails} failure(s)")
    return fails


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.gettempdir()) / "impacttext-screens"
    sys.exit(1 if asyncio.run(main(url, out)) else 0)
