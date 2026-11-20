"""
End-to-end click-through in a real headless browser (Edge or Chrome) over the DevTools protocol.
  python -m eval.browser_test http://127.0.0.1:8765        (server running with a throwaway IMPACT_DB)
Checks: name step -> type a note -> Save -> confirmation shows fields -> Undo restores the text and deletes the entry
        -> dashboard "Done" resolves a follow-up -> report renders -> phone simulator replies.
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import httpx
import websockets

sys.stdout.reconfigure(encoding="utf-8")
BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            shutil.which("chromium") or "", shutil.which("google-chrome") or ""]
PORT = 9333


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
                self.errors.append(msg["params"]["exceptionDetails"].get("exception", {}).get("description", "error"))
            if msg.get("id") == my:
                return msg.get("result", {})

    async def js(self, expr):
        r = await self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"])
        return r["result"].get("value")

    async def goto(self, url):
        await self.call("Page.navigate", url=url)
        await self.wait("document.readyState === 'complete'")

    async def wait(self, cond, timeout=8):
        end = time.time() + timeout
        while time.time() < end:
            if await self.js(f"!!({cond})"):
                return True
            await asyncio.sleep(0.1)
        raise TimeoutError(cond)


async def main(base: str) -> int:
    exe = next((b for b in BROWSERS if b and os.path.exists(b)), None)
    if not exe:
        print("No Edge/Chrome found; skipping.")
        return 0
    profile = tempfile.mkdtemp()
    proc = subprocess.Popen([exe, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
                             "--no-first-run", "--window-size=420,900", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fails = 0

    def check(label, ok, detail=""):
        nonlocal fails
        fails += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {label}{'  ' + str(detail) if detail else ''}")

    try:
        for _ in range(50):
            try:
                targets = httpx.get(f"http://127.0.0.1:{PORT}/json").json()
                break
            except httpx.HTTPError:
                await asyncio.sleep(0.2)
        ws_url = next(t["webSocketDebuggerUrl"] for t in targets if t["type"] == "page")
        async with websockets.connect(ws_url, max_size=None) as ws:
            p = Page(ws)
            await p.call("Runtime.enable")
            await p.call("Page.enable")

            await p.goto(base + "/")
            check("log page asks for a name first", await p.js("!document.getElementById('nameStep').classList.contains('hidden')"))
            await p.js("document.getElementById('nameInput').value='Tester'; document.getElementById('nameForm').requestSubmit()")
            await p.wait("!document.getElementById('logStep').classList.contains('hidden')")
            check("name saved, log step shown", await p.js("document.getElementById('who').textContent") == "Tester")

            note = "fed 12 kids at sherman park, sent a mom to 414 life, call her back friday"
            await p.js(f"document.getElementById('text').value={json.dumps(note)}; document.getElementById('logForm').requestSubmit()")
            await p.wait("!document.getElementById('done').classList.contains('hidden')")
            fields = await p.js("document.getElementById('fields').innerText")
            check("confirmation shows extracted fields", "12" in fields and "414 LIFE" in fields, fields.replace("\n", " | "))
            check("textarea cleared after save", await p.js("document.getElementById('text').value") == "")
            n_before = len(httpx.get(base + "/api/entries").json())

            await p.js("document.getElementById('undo').click()")
            await p.wait("document.getElementById('done').classList.contains('hidden')")
            check("undo puts the note back in the box", await p.js("document.getElementById('text').value") == note)
            check("undo deleted the entry", len(httpx.get(base + "/api/entries").json()) == n_before - 1)

            await p.js("document.getElementById('logForm').requestSubmit()")
            await p.wait("!document.getElementById('done').classList.contains('hidden')")
            await p.goto(base + "/")
            check("name remembered on reload", await p.js("document.getElementById('who').textContent") == "Tester")

            await p.goto(base + "/dashboard")
            await p.wait("document.querySelectorAll('#tiles .tile').length === 6")
            open_before = int(await p.js("document.getElementById('fuCount').textContent"))
            check("dashboard shows follow-up", open_before >= 1, open_before)
            await p.js("document.querySelector('#followups button').click()")
            await p.wait(f"+document.getElementById('fuCount').textContent === {open_before - 1}")
            check("Done button resolves the follow-up", True)
            await p.js("document.getElementById('days').value='7'; document.getElementById('days').dispatchEvent(new Event('change'))")
            await p.wait("document.getElementById('reportLink').href.endsWith('days=7')")
            check("period filter updates report link", True)

            await p.goto(base + "/report?days=7")
            await p.wait("document.querySelector('#paper h1') !== null")
            check("report renders with numbers", await p.js("document.querySelectorAll('.kpis div').length") == 5)

            await p.goto(base + "/phone")
            await p.js("document.getElementById('body').value='HELP'; document.getElementById('f').requestSubmit()")
            await p.wait("document.querySelectorAll('.msg.them').length >= 2")
            check("phone simulator gets a reply", "UNDO" in await p.js("[...document.querySelectorAll('.msg.them')].pop().textContent"))

            overflow = []
            for path in ["/", "/dashboard", "/report", "/phone"]:
                await p.goto(base + path)
                await asyncio.sleep(0.4)
                if await p.js("document.documentElement.scrollWidth > window.innerWidth + 1"):
                    overflow.append(path)
            check("no sideways scrolling at 420px", not overflow, overflow)
            check("no JavaScript errors", not p.errors, p.errors[:3])
    finally:
        proc.terminate()
        shutil.rmtree(profile, ignore_errors=True)
    print(f"\n{fails} failure(s)")
    return fails


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765")) else 0)
