"""Quick test script for RedTag search via Playwright."""
import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})

        captured = []

        def on_request(request):
            u = request.url
            skip_exts = (".js", ".css", ".png", ".jpg", ".svg", ".woff", ".woff2", ".gif", ".ico")
            if "redtag" in u and not any(u.endswith(ext) for ext in skip_exts):
                pd = request.post_data or ""
                captured.append({"url": u[:300], "method": request.method, "post": pd[:400]})

        page.on("request", on_request)

        url = (
            "https://secure-res.redtag.ca/vacations/search?"
            "dest_dep=2&gateway_dep=YYZ&date=20260501"
            "&duration=7days,8days&numberOfRooms=1&numberOfAdults=2"
            "&numberOfChildren=0&all_inclusive=y&date_format=Ymd"
            "&alias=engine&sentalias=api&lang=en"
        )

        print("Loading search page...")
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(10000)

        print(f"\nCaptured {len(captured)} API requests:")
        for r in captured:
            method = r["method"]
            rurl = r["url"]
            print(f"  {method} {rurl}")
            if r["post"]:
                print(f"    body: {r['post']}")

        print(f"\nFinal URL: {page.url[:200]}")

        await browser.close()

asyncio.run(test())
