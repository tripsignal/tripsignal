"""Test RedTag with full Chrome headers and Incapsula flow."""
import requests
import re
import json
import html as html_mod

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Cache-Control": "max-age=0",
    "Connection": "keep-alive",
    "Sec-CH-UA": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

session = requests.Session()

# Step 1: Visit home page
print("Step 1: Loading home page...")
r1 = session.get("https://secure-res.redtag.ca/vacations/", headers=headers, timeout=30)
print(f"  Status: {r1.status_code}")
print(f"  Cookies: {list(session.cookies.keys())}")

# Step 2: Visit search with referer
print("\nStep 2: Loading search page...")
headers["Referer"] = "https://secure-res.redtag.ca/vacations/"
headers["Sec-Fetch-Site"] = "same-origin"

url = (
    "https://secure-res.redtag.ca/vacations/search?"
    "dest_dep=2&gateway_dep=YYZ&date=20260501"
    "&duration=7days,8days&numberOfRooms=1&numberOfAdults=2"
    "&numberOfChildren=0&all_inclusive=y&date_format=Ymd"
    "&alias=engine&sentalias=api&lang=en"
)
r2 = session.get(url, headers=headers, timeout=30)
m = re.search(r'data-search="([^"]+)"', r2.text)
if m:
    ds = json.loads(html_mod.unescape(m.group(1)))
    print(f"  searchParams: {ds.get('searchParams')}")
    print(f"  error: {ds.get('error')}")
else:
    print("  No data-search found")

# Check if there is an ___utmv cookie or reese84 cookie (Imperva)
print(f"\n  All cookies after search:")
for c in session.cookies:
    print(f"    {c.name}={c.value[:40]}...")

# Try visiting the exact URL from the HAR file spec
print("\nStep 3: Trying exact HAR URL format...")
har_url = (
    "https://secure-res.redtag.ca/vacations/search?"
    "dest_dep=9&gateway_dep=YQR&date=20260401"
    "&duration=7days,8days&numberOfRooms=1&numberOfAdults=2"
    "&numberOfChildren=0&all_inclusive=y&date_format=Ymd"
    "&alias=engine&sentalias=api&lang=en"
)
r3 = session.get(har_url, headers=headers, timeout=30)
m3 = re.search(r'data-search="([^"]+)"', r3.text)
if m3:
    ds3 = json.loads(html_mod.unescape(m3.group(1)))
    print(f"  searchParams: {ds3.get('searchParams')}")
    print(f"  error: {ds3.get('error')}")

# Try JUST the base search URL with NO parameters
print("\nStep 4: Search with no params...")
r4 = session.get("https://secure-res.redtag.ca/vacations/search", headers=headers, timeout=30)
m4 = re.search(r'data-search="([^"]+)"', r4.text)
if m4:
    ds4 = json.loads(html_mod.unescape(m4.group(1)))
    print(f"  searchParams: {ds4.get('searchParams')}")
    print(f"  error: {ds4.get('error')}")
