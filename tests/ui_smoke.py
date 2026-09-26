"""Manual UI smoke test (not collected by pytest).

    python tests/ui_smoke.py http://localhost:7860 <dir containing proxy.webm and ad.webm>

The headless Chromium in the dev container lacks H.264, so .mp4 requests are served as WebM stand-ins of the same
content; the player logic under test is unchanged. Checks: run → break panel → ad cut → resume within ±0.5 s.
"""

import asyncio
import glob
import sys

from playwright.async_api import async_playwright

BASE, OUT = sys.argv[1], sys.argv[2]


async def main() -> None:
    async with async_playwright() as p:
        exe = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome") or [None])[0]
        browser = await p.chromium.launch(executable_path=exe, args=["--autoplay-policy=no-user-gesture-required"])
        pg = await browser.new_page(viewport={"width": 1400, "height": 1000})
        errs: list[str] = []
        pg.on("pageerror", lambda e: errs.append(str(e)))

        async def webm(route):
            name = "proxy.webm" if "proxy" in route.request.url else "ad.webm"
            with open(f"{OUT}/{name}", "rb") as f:
                body = f.read()
            await route.fulfill(status=200, body=body, headers={"Content-Type": "video/webm"})

        await pg.route("**/*.mp4", webm)
        await pg.goto(BASE)
        await pg.wait_for_selector("#brands tr >> nth=1")
        await pg.select_option("#sample", "bhojon_bilashi")
        await pg.click("#go")
        await pg.wait_for_function("document.getElementById('summary').textContent.includes('breaks')",
                                   timeout=180000)
        print("summary:", await pg.text_content("#summary"))
        await pg.click("#timeline rect.break")
        print("detail:", (await pg.text_content("#detail"))[:300].replace("\n", " "))
        await pg.screenshot(path=f"{OUT}/ui_results.png", full_page=True)

        t = await pg.evaluate("BREAKS[0].t")
        await pg.evaluate(f"content.muted = true; ad.muted = true; content.currentTime = {t} - 2; content.play()")
        await pg.wait_for_function("getComputedStyle(ad).display === 'block' && !ad.paused", timeout=20000)
        print(f"break at {t:.3f}; content paused at {await pg.evaluate('content.currentTime'):.3f}")
        await pg.screenshot(path=f"{OUT}/ui_ad.png")
        await pg.evaluate("ad.currentTime = ad.duration - 0.5")
        await pg.wait_for_function("getComputedStyle(ad).display === 'none' && !content.paused", timeout=20000)
        await asyncio.sleep(0.3)
        resumed = await pg.evaluate("content.currentTime")
        print(f"resumed at {resumed:.3f} (offset error {resumed - t:+.3f}s)")
        print("page errors:", errs)
        await browser.close()


asyncio.run(main())
