"""Test RedTag with Playwright full browser (non-headless) via Xvfb."""
import asyncio
import os
from playwright.async_api import async_playwright
import json

# Use xvfb-run or set DISPLAY for virtual display
os.environ["DISPLAY"] = ":99"


async def test():
    async with async_playwright() as p:
        # Install full Chromium (not headless shell)
        # Launch in headed mode (non-headless)
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-CA",
            timezone_id="America/Toronto",
        )

        # Remove automation indicators
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => false});
        """)

        page = await context.new_page()

        url = (
            "https://secure-res.redtag.ca/vacations/search?"
            "dest_dep=2&gateway_dep=YYZ&date=20260501"
            "&duration=7days,8days&numberOfRooms=1&numberOfAdults=2"
            "&numberOfChildren=0&all_inclusive=y&date_format=Ymd"
            "&alias=engine&sentalias=api&lang=en"
        )

        print("Loading search page (headed mode via Xvfb)...", flush=True)
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        print(f"Status: {response.status}", flush=True)

        # Wait for Incapsula challenge resolution
        await page.wait_for_timeout(8000)

        ds = await page.evaluate(
            "() => { var el = document.querySelector('[data-search]');"
            " if(el) try { return JSON.parse(el.getAttribute('data-search')); } catch(e) {}"
            " return null; }"
        )

        if ds:
            print(f"searchParams: {ds.get('searchParams')}", flush=True)
            print(f"error: {ds.get('error')}", flush=True)
            sid = ds.get("session", {}).get("id", "none")
            print(f"session: {sid}", flush=True)

            if ds.get("searchParams"):
                print("SUCCESS! Search params found!", flush=True)
                # Now try the AJAX call
                ajax = await page.evaluate(
                    """async (sid) => {
                    var resp = await fetch('/vacations/search/ajaxRefineSearchresults', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({sid: sid, filter: {}, token: null}),
                    });
                    return await resp.json();
                }""",
                    sid,
                )
                rows = ajax.get("packageResults", {}).get("rows", [])
                print(f"AJAX results: {len(rows)} rows", flush=True)
                if rows:
                    first = rows[0]
                    hotel = first.get("package", {}).get("hotel", {})
                    pricing = first.get("rateInfo", {}).get("pricingInfo", {}).get("perPerson", {})
                    print(f"First: {hotel.get('hotelName')} - ${pricing.get('total')}/pp", flush=True)
        else:
            print("No data-search found", flush=True)

        await browser.close()


asyncio.run(test())
