#!/usr/bin/env python3
"""Fetch latest stable browser versions and print updated BROWSER_PROFILES.

Usage:
    python scripts/update_user_agents.py

This script queries public version APIs for Chrome, Edge, and Firefox,
then prints an updated BROWSER_PROFILES list and UA_LAST_UPDATED date
that you can paste into backend/app/workers/shared/browser_profiles.py.

After pasting, also update the curl_cffi impersonate targets to match
the major version numbers (e.g., chrome131 → chrome133).

Check supported impersonate targets at:
    python -c "from curl_cffi.requests import BrowserType; print([t.name for t in BrowserType])"
"""
import json
import sys
import urllib.request
from datetime import date


def fetch_chrome_version() -> str:
    """Fetch latest stable Chrome version from Chrome for Testing API."""
    url = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions.json"
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        data = json.loads(resp.read().decode())
        return data["channels"]["Stable"]["version"]
    except Exception as e:
        print(f"  WARNING: Could not fetch Chrome version: {e}", file=sys.stderr)
        return "unknown"


def fetch_firefox_version() -> str:
    """Fetch latest stable Firefox version from Mozilla's product-details API."""
    url = "https://product-details.mozilla.org/1.0/firefox_versions.json"
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        data = json.loads(resp.read().decode())
        return data.get("LATEST_FIREFOX_VERSION", "unknown")
    except Exception as e:
        print(f"  WARNING: Could not fetch Firefox version: {e}", file=sys.stderr)
        return "unknown"


def fetch_edge_version() -> str:
    """Fetch latest stable Edge version from Microsoft's Edge update API."""
    url = "https://edgeupdates.microsoft.com/api/products"
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        data = json.loads(resp.read().decode())
        for product in data:
            if product.get("Product") == "Stable":
                for release in product.get("Releases", []):
                    if release.get("Platform") == "Windows" and release.get("Architecture") == "x64":
                        return release.get("ProductVersion", "unknown")
        return "unknown"
    except Exception as e:
        print(f"  WARNING: Could not fetch Edge version: {e}", file=sys.stderr)
        return "unknown"


def main():
    print("Fetching latest stable browser versions...\n")

    chrome = fetch_chrome_version()
    firefox = fetch_firefox_version()
    edge = fetch_edge_version()

    chrome_major = chrome.split(".")[0] if chrome != "unknown" else "???"
    firefox_major = firefox.split(".")[0] if firefox != "unknown" else "???"
    edge_major = edge.split(".")[0] if edge != "unknown" else "???"

    print(f"  Chrome:  {chrome} (major: {chrome_major})")
    print(f"  Firefox: {firefox} (major: {firefox_major})")
    print(f"  Edge:    {edge} (major: {edge_major})")
    print()
    print("=" * 70)
    print("UPDATE browser_profiles.py WITH THE FOLLOWING:")
    print("=" * 70)
    print()
    print(f"1. Set UA_LAST_UPDATED = date({date.today().year}, {date.today().month}, {date.today().day})")
    print()
    print(f'2. Update impersonate targets to match major versions:')
    print(f'   - Chrome profiles: "chrome{chrome_major}"')
    print(f'   - Edge profiles:   "edge{edge_major}"')
    print()
    print(f'3. Update Sec-CH-UA header strings:')
    print(f'   - Chrome: \'"Chromium";v="{chrome_major}", "Google Chrome";v="{chrome_major}", "Not_A Brand";v="24"\'')
    print(f'   - Edge:   \'"Microsoft Edge";v="{edge_major}", "Chromium";v="{edge_major}", "Not_A Brand";v="24"\'')
    print()
    print("4. Check which impersonate targets curl_cffi supports:")
    print('   python -c "from curl_cffi.requests import BrowserType; print([t.name for t in BrowserType])"')
    print()
    print("   If a version isn't supported yet, use the closest lower version.")
    print()
    print("=" * 70)


if __name__ == "__main__":
    main()
