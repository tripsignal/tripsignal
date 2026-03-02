"""Test RedTag search form submission via Playwright."""
import asyncio
import sys
from playwright.async_api import async_playwright


async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})

        print("Loading main page...", flush=True)
        await page.goto(
            "https://secure-res.redtag.ca/vacations/",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await page.wait_for_timeout(5000)

        state = await page.evaluate(
            "() => { var g = document.getElementById('vac_gatewayText');"
            " var d = document.getElementById('vac_destinationText');"
            " return {gw: g ? g.value : null, dest: d ? d.value : null}; }"
        )
        print(f"Form state: {state}", flush=True)

        # Dismiss overlays
        await page.evaluate(
            "var els = document.querySelectorAll('.modal,.overlay,.popup,.cookie-banner');"
            " for(var i=0;i<els.length;i++) els[i].remove();"
        )

        # Click search button with force
        try:
            await page.locator("button.search-btn").click(force=True, timeout=5000)
            await page.wait_for_timeout(5000)
            print(f"URL after click: {page.url[:250]}", flush=True)
        except Exception as e:
            print(f"Click error: {e}", flush=True)

        ds = await page.evaluate(
            "() => { var el = document.querySelector('[data-search]');"
            " if(el) try { return JSON.parse(el.getAttribute('data-search')); } catch(e) {}"
            " return null; }"
        )
        if ds:
            print(f"searchParams: {ds.get('searchParams')}", flush=True)
            print(f"error: {ds.get('error')}", flush=True)
            print(f"session: {ds.get('session', {}).get('id', 'none')}", flush=True)
        else:
            print("No data-search on result page", flush=True)
            title = await page.title()
            print(f"Page title: {title}", flush=True)

        await browser.close()


asyncio.run(test())
