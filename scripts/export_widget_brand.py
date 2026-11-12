"""Export the private widget's logo from the original Figma SVG, at build time.

Requires CairoSVG and Pillow. Regenerate daily-private.min.html afterwards.
The small palette lets the entire logo travel with the widget, even offline.
"""
from io import BytesIO
from pathlib import Path

import cairosvg
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def main():
    svg = ROOT / "web/demo/assets/logo.svg"  # Figma layer 15:203, both mark and wordmark.
    with Image.open(BytesIO(cairosvg.svg2png(url=str(svg), output_width=696))) as image:
        on_white = Image.new("RGB", image.size, "white")
        on_white.paste(image, mask=image.getchannel("A"))
    width = 255
    on_white = on_white.resize((width, round(width * 47 / 203)), Image.Resampling.LANCZOS)
    palette = Image.new("P", (1, 1))
    colors = [255, 255, 255]
    for color in ((169, 87, 35), (24, 29, 39)):
        for alpha in (0.5, 1):
            colors.extend(round(255 + (channel - 255) * alpha) for channel in color)
    palette.putpalette(colors)
    result = on_white.quantize(palette=palette, dither=Image.Dither.NONE)
    result.save(ROOT / "app/assets/widgets/figma-logo.webp", lossless=True, method=6)


if __name__ == "__main__":
    main()
