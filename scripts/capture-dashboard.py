"""Capture the provisioned dashboard using local Chrome and Playwright.
Usage: python scripts/capture-dashboard.py http://localhost:3008/d/quickticket-lab7 output.png
"""
import sys
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless=True)
    page = browser.new_page(viewport={'width':1500,'height':1200})
    page.goto(sys.argv[1], wait_until='networkidle')
    page.wait_for_timeout(8000)
    page.screenshot(path=sys.argv[2], full_page=True)
    browser.close()
