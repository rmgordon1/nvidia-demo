"""Build docs/bep-ops-ai-2pager.pdf: summary, workflow diagram, SBOM and a UI screenshot on two Letter pages.

Needs the webapp running on http://localhost:8080 and Playwright with Chromium:
    pip install playwright && playwright install chromium
    python docs/twopager_build.py
The diagram and its styles are lifted from docs/architecture.html, so re-run architecture_gen.py first if it changed.
"""
import asyncio, re, sys
from pathlib import Path
from playwright.async_api import async_playwright

DOCS = Path(__file__).resolve().parent
URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
SHOT, HTML, PDF = DOCS / "twopager_cv.png", DOCS / "twopager.html", DOCS / "bep-ops-ai-2pager.pdf"


def diagram():
    src = (DOCS / "architecture.html").read_text()
    root = re.search(r":root\{(.*?)\}", src, re.S).group(1)      # light-theme tokens only
    css = re.search(r"/\* svg \*/(.*?)/\* reference \*/", src, re.S).group(1)
    svg = re.search(r"<svg viewBox=\"0 0 1370 870\".*?</svg>", src, re.S).group(0)
    svg = svg.replace('width="1370" height="870"', 'width="100%"')
    return root, css, svg


async def screenshot(browser):
    pg = await browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
    await pg.add_init_script("localStorage.setItem('bep.role', JSON.stringify('admin'))")
    await pg.goto(URL + "/#/vision")
    await pg.wait_for_selector("text=Sheet review")
    await pg.wait_for_timeout(2500)                                # heatmap canvas + charts settle
    # sheet review panel (heatmap, magnified peak, stage 1/2, human disposition) + review queue beside it
    box = await pg.evaluate("""() => {
      const card = t => [...document.querySelectorAll('h3')].find(e => e.textContent.trim() === t)
                        .closest('.card');
      const a = card('Sheet review').getBoundingClientRect(), b = card('Review queue').getBoundingClientRect();
      return {x: a.left - 8, y: a.top + scrollY - 8, width: b.right - a.left + 16, height: 670};
    }""")
    await pg.screenshot(path=str(SHOT), clip=box, full_page=True)
    await pg.close()


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        await screenshot(browser)
        root, css, svg = diagram()
        tpl = (DOCS / "twopager_tpl.html").read_text()
        HTML.write_text(tpl.replace("/*ARCH_ROOT*/", root).replace("/*ARCH_CSS*/", css).replace("<!--ARCH_SVG-->", svg))
        pg = await browser.new_page()
        await pg.goto(HTML.as_uri())
        await pg.wait_for_load_state("networkidle")
        await pg.evaluate("document.fonts.ready")
        await pg.pdf(path=str(PDF), format="Letter", print_background=True,
                     margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        await browser.close()
    print("wrote", PDF)

asyncio.run(main())
