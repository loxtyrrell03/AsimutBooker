"""Generate deterministic Asimut phone PWA icons.

This is intentionally code-native artwork: the three-bar mark matches the app
header and stays crisp at every required home-screen size.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "phone" / "public"


def draw_icon(size: int, *, maskable: bool) -> Image.Image:
    image = Image.new("RGB", (size, size), "#0b1512")
    draw = ImageDraw.Draw(image)
    inset = int(size * (0.10 if maskable else 0.055))
    radius = int(size * 0.22)
    draw.rounded_rectangle(
        (inset, inset, size - inset, size - inset),
        radius=radius,
        fill="#14271f",
        outline="#315342",
        width=max(1, size // 128),
    )
    bar_width = max(8, int(size * 0.075))
    gap = int(size * 0.065)
    heights = (int(size * 0.29), int(size * 0.51), int(size * 0.39))
    total_width = bar_width * 3 + gap * 2
    start_x = (size - total_width) // 2
    baseline = int(size * 0.69)
    for index, height in enumerate(heights):
        left = start_x + index * (bar_width + gap)
        draw.rounded_rectangle(
            (left, baseline - height, left + bar_width, baseline),
            radius=bar_width // 2,
            fill="#a8efc7",
        )
    return image


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    draw_icon(192, maskable=False).save(OUTPUT / "icon-192.png", optimize=True)
    draw_icon(512, maskable=False).save(OUTPUT / "icon-512.png", optimize=True)
    draw_icon(512, maskable=True).save(OUTPUT / "icon-maskable-512.png", optimize=True)
    draw_icon(180, maskable=False).save(OUTPUT / "apple-touch-icon.png", optimize=True)


if __name__ == "__main__":
    main()
