"""Regenerate desktop/deepfind.ico (+ installer bitmaps) from frontend/public/logo.svg.
Needs Playwright's Chromium (only when the logo changes; the .ico is committed)."""
import asyncio
import io
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
SVG = (ROOT / "frontend" / "public" / "logo.svg").read_text()


async def render(size: int) -> Image.Image:
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": size, "height": size})
        await pg.set_content(f"<html><body style='margin:0;background:transparent'>"
                             f"<div style='width:{size}px;height:{size}px'>{SVG.replace('<svg ', f'<svg width={size} height={size} ')}</div></body></html>")
        png = await pg.screenshot(omit_background=True)
        await b.close()
    return Image.open(io.BytesIO(png)).convert("RGBA")


async def main():
    big = await render(256)
    out = ROOT / "desktop" / "deepfind.ico"
    big.save(out, sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.save(ROOT / "packaging" / "windows" / "logo-256.png")
    print("wrote", out)

asyncio.run(main())
