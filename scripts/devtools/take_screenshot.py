import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        try:
            # Launch MS Edge instead of Chrome
            browser = await p.chromium.launch(channel="msedge", headless=True)
            page = await browser.new_page()
            
            print("Navigating to login...")
            await page.goto("http://localhost:8080")
            
            # Wait for login inputs
            print("Logging in...")
            await page.fill('input[type="email"], input[name="email"], input[name="username"]', "admin@turkpatent.gov.tr")
            await page.fill('input[type="password"], input[name="password"]', "admin123")
            
            # Click login button (try common selectors)
            login_btn = await page.query_selector('button[type="submit"], button:has-text("Login"), button:has-text("Giriş")')
            if login_btn:
                await login_btn.click()
            else:
                await page.keyboard.press('Enter')
                
            # Wait for navigation after login
            await page.wait_for_timeout(3000)
            
            print("Searching for 'dogan patent'...")
            # Try to find the search input
            search_input = await page.query_selector('input[type="search"], input[type="text"], input[placeholder*="Search"], input[placeholder*="Ara"]')
            
            if search_input:
                await search_input.fill("dogan patent")
                
                # trigger search
                search_btn = await page.query_selector('button:has-text("Search"), button:has-text("Ara")')
                if search_btn:
                    await search_btn.click()
                else:
                    await page.keyboard.press('Enter')
            else:
                print("Could not find search input")
                
            # Wait for results to load
            print("Waiting for results...")
            await page.wait_for_timeout(5000)
            
            screenshot_path = "C:\\Users\\701693\\turk_patent\\search_results.png"
            await page.screenshot(path=screenshot_path, full_page=False)
            print(f"Screenshot saved to: {screenshot_path}")
            
            await browser.close()
            
        except Exception as e:
            print(f"Error during browser automation: {e}")

if __name__ == "__main__":
    asyncio.run(main())
