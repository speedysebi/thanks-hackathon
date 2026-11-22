"""
Takes the README screenshots (docs/screenshots/) from a running server with demo data.
  set ADMIN_PASSWORD=sim-admin and ADMIN_TOTP_SECRET=JBSWY3DPEHPK3PXP   (the server must use these)
  python -m app.seed --reset
  python -m eval.screenshots http://127.0.0.1:8765
Uses the same headless Edge/Chrome driver as eval/browser_test.py.
"""
import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

from app import mfa
from eval.browser_test import ADMIN_PW, ADMIN_TOTP, BROWSERS, PORT, Page

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"


async def main(base: str) -> None:
    exe = next((b for b in BROWSERS if b and os.path.exists(b)), None)
    if not exe:
        sys.exit("No Edge/Chrome found.")
    OUT.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([exe, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={tempfile.mkdtemp()}",
                             "--no-first-run", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    api = httpx.Client(base_url=base)
    api.post("/api/auth/login", json={"name": "admin", "pin": ADMIN_PW})
    if time.time() % mfa.STEP > mfa.STEP - 3:
        time.sleep(4)
    assert api.post("/api/auth/code", json={"code": mfa.code_now(ADMIN_TOTP)}).status_code == 200, "admin sign-in failed"
    admin_cookie = api.cookies.get("it_session")
    try:
        for _ in range(50):
            try:
                tabs = httpx.get(f"http://127.0.0.1:{PORT}/json").json()
                ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
                break
            except Exception:
                await asyncio.sleep(0.3)
        async with websockets.connect(ws_url, max_size=2**25) as ws:
            p = Page(ws)
            for d in ("Page", "Runtime", "Network"):
                await p.call(f"{d}.enable")

            # 1. texting, on a phone: a worker texts a note, gets the record back
            await p.device("iphone")
            await p.goto(base + "/phone")
            for msg in ("NAME Dee", "fed 20 kids at sherman park, sent 1 mom to 414 life, need to call her back friday"):
                await p.js(f"document.getElementById('body').value = {msg!r}; document.getElementById('f').requestSubmit()")
                await asyncio.sleep(1.2)
            await p.shot(OUT / "text-it.png")

            # 2. typing (or talking) on the web page
            await p.fresh()
            await p.goto(base + "/")
            await p.js("localStorage.setItem('worker', 'Dee')")
            await p.goto(base + "/")
            await p.js("document.getElementById('text').value = "
                       "'took 8 teens to play basketball at washington park, talked about staying out of beef'")
            await p.js("document.getElementById('logForm').requestSubmit()")
            await asyncio.sleep(1.5)
            await p.js("window.scrollTo(0, document.getElementById('mic').getBoundingClientRect().top + scrollY - 16)")
            await p.shot(OUT / "log.png")

            # 3 + 4. what an admin sees
            await p.call("Network.setCookie", name="it_session", value=admin_cookie, url=base)
            await p.device("desktop")
            await p.goto(base + "/dashboard")
            await asyncio.sleep(1)
            await p.shot(OUT / "dashboard.png")
            await p.goto(base + "/report")
            await asyncio.sleep(1.5)
            await p.shot(OUT / "report.png")
    finally:
        proc.terminate()
    print("Saved:", ", ".join(sorted(f.name for f in OUT.glob("*.png"))))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"))
