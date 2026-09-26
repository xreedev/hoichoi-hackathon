"""Manual UI smoke test (not collected by pytest): python tests/ui_smoke.py http://localhost:7860 <dir with proxy.webm, ad.webm>.
Headless Chromium here lacks H.264, so .mp4 requests are served as WebM stand-ins; player logic is unchanged."""
import asyncio, sys, glob
from playwright.async_api import async_playwright
BASE = sys.argv[1]; OUT = sys.argv[2]

async def main():
    async with async_playwright() as p:
        exe = (glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome") or [None])[0]
        b = await p.chromium.launch(executable_path=exe, args=["--autoplay-policy=no-user-gesture-required"])
        pg = await b.new_page(viewport={"width": 1400, "height": 1000})
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e))); pg.on("console", lambda m: m.type == "error" and errs.append(m.text))
        async def webm(route):
            u = route.request.url
            if u.endswith(".mp4"):
                body = open(f"{OUT}/proxy.webm" if "proxy" in u else f"{OUT}/ad.webm", "rb").read()
                await route.fulfill(status=200, body=body, headers={"Content-Type": "video/webm", "Accept-Ranges": "bytes"})
            else:
                await route.continue_()
        await pg.route("**/*.mp4", webm)
        await pg.goto(BASE)
        await pg.wait_for_selector("#brands tr >> nth=1")
        await pg.select_option("#sample", "bhojon_bilashi")
        await pg.click("#go")
        await pg.wait_for_function("document.getElementById('summary').textContent.includes('breaks')", timeout=180000)
        print("summary:", await pg.text_content("#summary"))
        print("stages:", (await pg.text_content("#stages"))[:300].replace("\n", " | "))
        # click the selected candidate
        await pg.click("#timeline rect.break")
        print("detail:", (await pg.text_content("#detail"))[:400].replace("\n", " "))
        await pg.screenshot(path=f"{OUT}/ui_results.png", full_page=True)
        # ad playback: jump 2 s before the break, play, expect ad, then resume at the offset
        t = await pg.evaluate("BREAKS[0].t")
        await pg.evaluate(f"content.muted = true; ad.muted = true; content.currentTime = {t} - 2; content.play()")
        await pg.wait_for_function("getComputedStyle(ad).display === 'block' && !ad.paused", timeout=20000)
        paused_at = await pg.evaluate("content.currentTime")
        print(f"break at {t:.3f}; content paused at {paused_at:.3f}; ad src {await pg.evaluate('ad.src')}")
        await pg.screenshot(path=f"{OUT}/ui_ad.png")
        await pg.evaluate("ad.currentTime = ad.duration - 0.5")
        await pg.wait_for_function("getComputedStyle(ad).display === 'none' && !content.paused", timeout=20000)
        await asyncio.sleep(0.3)
        resumed = await pg.evaluate("content.currentTime")
        print(f"resumed at {resumed:.3f} (offset error {resumed - t:+.3f}s)")
        print("page errors:", errs)
        await b.close()
asyncio.run(main())
