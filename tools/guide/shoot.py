"""Regenerate docs/BUCS-Install-Guide.pdf: python tools/guide/shoot.py, then
python tools/guide/build_pdf.py docs/BUCS-Install-Guide.pdf  (needs reportlab, pillow, websockets, Chrome)."""
"""Capture element screenshots with headless Chrome over CDP."""
import asyncio, base64, json, subprocess, sys, time, urllib.request
from pathlib import Path

import websockets

OUT = Path(__file__).parent
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 9333
SITE = "https://saikiranthatikonda-coder.github.io/Avengers-Bobbiey-UCS/"
ACT = (Path(r"C:\Users\Saikiran\Avengers-Bobbiey-UCS\static\activate.html")).as_uri()

proc = subprocess.Popen([CHROME, "--headless=new", f"--remote-debugging-port={PORT}", "--hide-scrollbars",
                         "--window-size=1440,1000", f"--user-data-dir={OUT / 'chrome-profile'}", "about:blank"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
        ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page"); break
    except Exception:
        time.sleep(0.2)


async def main():
    async with websockets.connect(ws_url, max_size=50_000_000) as ws:
        n = 0

        async def call(method, **params):
            nonlocal n
            n += 1; my = n
            await ws.send(json.dumps({"id": my, "method": method, "params": params}))
            while True:
                m = json.loads(await ws.recv())
                if m.get("id") == my:
                    return m.get("result", {})

        async def ev(js):
            r = await call("Runtime.evaluate", expression=js, awaitPromise=True, returnByValue=True)
            return r.get("result", {}).get("value")

        async def shot(name, selector, pad=12, scale=2):
            rect = await ev(f"""(() => {{ const e = document.querySelector({json.dumps(selector)});
                e.scrollIntoView({{block: 'start'}}); const r = e.getBoundingClientRect();
                return {{x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height}}; }})()""")
            await asyncio.sleep(0.8)
            clip = {"x": max(0, rect["x"] - pad), "y": max(0, rect["y"] - pad),
                    "width": rect["w"] + 2 * pad, "height": rect["h"] + 2 * pad, "scale": scale}
            r = await call("Page.captureScreenshot", format="png", clip=clip, captureBeyondViewport=True)
            (OUT / f"{name}.png").write_bytes(base64.b64decode(r["data"]))
            print("saved", name, clip)

        await call("Page.enable")
        await call("Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1, mobile=False)
        # 1) website: Get Started + payment card
        await call("Page.navigate", url=SITE + "?guide=1")
        await asyncio.sleep(7)                           # boot overlay + fonts + payments.json
        await ev("document.querySelectorAll('.reveal').forEach(e => e.classList.add('in','visible','show')); "
                 "document.getElementById('boot')?.classList.add('done'); document.getElementById('nav').style.display='none'; 1")
        await asyncio.sleep(1.5)
        await shot("web_get", "#get .access-card")
        await shot("web_pay", "#pay-card")
        await shot("web_plan", ".price-card.solo")
        # 2) activation screen (local file; example install ID so the guide never shows a real one)
        await call("Page.navigate", url=ACT)
        await asyncio.sleep(3)
        await ev("document.getElementById('iid').textContent = '1A2B3-C4D5E'; "
                 "document.getElementById('key').value = 'BUCS-eyJ2IjoxLCJpZCI6…  (your key from the email)'; 1")
        await asyncio.sleep(0.5)
        await shot("act", "main.card")

asyncio.run(main())
proc.terminate()
