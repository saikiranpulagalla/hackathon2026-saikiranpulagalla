import asyncio
from playwright.async_api import async_playwright
import subprocess

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={'width': 1200, 'height': 900})
        page = await context.new_page()

        print("Capturing Streamlit Live Testing...")
        await page.goto("http://localhost:8501")
        # Wait for Streamlit to render
        await page.wait_for_selector(".stApp")
        await asyncio.sleep(5)  # Wait a bit for animations
        await page.screenshot(path="screenshots/live_testing.png")

        print("Capturing Streamlit Analytics Dashboard...")
        # Click the second tab (Analytics Dashboard)
        # Streamlit tabs are usually buttons with role="tab"
        tabs = await page.query_selector_all('button[role="tab"]')
        if len(tabs) >= 2:
            await tabs[1].click()
            await asyncio.sleep(5)  # wait for plots to render
            
            # Top summary
            await page.screenshot(path="screenshots/dashboard_summary.png")
            
            # Scroll down to gantt chart
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(2)
            await page.screenshot(path="screenshots/dashboard_gantt.png")
        else:
            print("Could not find the Analytics Dashboard tab")

        # Terminal output fake
        print("Capturing terminal output...")
        result = subprocess.run(["python", "-m", "src.main"], capture_output=True, text=True)
        # Just grab the last ~30 lines (the ProcessingReport)
        lines = result.stdout.split('\n')
        report_start = 0
        for i, line in enumerate(lines):
            if "+==============================================================+" in line:
                report_start = i
                break
        report_text = "\n".join(lines[report_start:])
        if not report_text:
            report_text = "Processing Complete\n(See audit log)"

        html_content = f"""
        <html>
        <body style="background-color: #1e1e1e; color: #d4d4d4; font-family: Consolas, 'Courier New', monospace; margin: 20px;">
            <pre style="font-size: 14px;">{report_text}</pre>
        </body>
        </html>
        """
        with open("term.html", "w", encoding="utf-8") as f:
            f.write(html_content)
        
        term_page = await context.new_page()
        # Set a smaller viewport for the terminal screenshot
        await term_page.set_viewport_size({"width": 800, "height": 600})
        import os
        await term_page.goto(f"file:///{os.path.abspath('term.html')}")
        await term_page.screenshot(path="screenshots/terminal_output.png")

        await browser.close()
        print("All screenshots captured!")

if __name__ == "__main__":
    asyncio.run(main())
