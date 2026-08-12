"""Executive LinkedIn Banner & Visual Asset Generator.

Generates production-grade 1200x627px visual graphic banners for LinkedIn posts,
featuring obsidian slate gradients, glassmorphism card overlays, brand badge tags,
and crisp typography.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def _get_font(size: int, bold: bool = False) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    font_names = (
        "Helvetica-Bold.ttf" if bold else "Helvetica.ttf",
        "Arial-Bold.ttf" if bold else "Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    )
    for font_path in font_names:
        try:
            return ImageFont.truetype(font_path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def generate_linkedin_banner(
    *,
    action_id: str,
    category: str = "STRATEGIC PARTNERSHIP",
    headline: str = "NexusAI & ChiefMind Announcement",
    subtext: str = "50,000+ Enterprise Users · 12 Countries · Launch Q3 2026",
    output_dir: Path | None = None,
) -> Path:
    """Generate a 1200x627 executive visual graphic image for LinkedIn feed."""
    width, height = 1200, 627
    image = Image.new("RGBA", (width, height), (15, 23, 42, 255))
    draw = ImageDraw.Draw(image)

    # 1. Gradient Background (Deep Obsidian Slate #0F172A to #1E293B with Electric Teal accent glow)
    for y in range(height):
        r = int(15 + (30 - 15) * (y / height))
        g = int(23 + (41 - 23) * (y / height))
        b = int(42 + (59 - 42) * (y / height))
        draw.line([(0, y), (width, y)], fill=(r, g, b, 255))

    # Glow Orbs (Top Left Cyan Glow & Bottom Right Purple Glow)
    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse([(-100, -100), (500, 500)], fill=(14, 165, 233, 45))
    glow_draw.ellipse([(800, 200), (1300, 700)], fill=(124, 58, 237, 40))
    glow = glow.filter(ImageFilter.GaussianBlur(80))
    image = Image.alpha_composite(image, glow)
    draw = ImageDraw.Draw(image)

    # Decorative Grid Graphic Lines
    grid_color = (255, 255, 255, 12)
    for x in range(0, width, 60):
        draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
    for y in range(0, height, 60):
        draw.line([(0, y), (width, y)], fill=grid_color, width=1)

    # 2. Main Glassmorphism Card Backdrop
    card_margin_x, card_margin_y = 70, 60
    card_rect = [card_margin_x, card_margin_y, width - card_margin_x, height - card_margin_y]

    card_overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    card_draw = ImageDraw.Draw(card_overlay)
    card_draw.rounded_rectangle(
        card_rect,
        radius=20,
        fill=(30, 41, 59, 190),
        outline=(255, 255, 255, 40),
        width=2,
    )
    image = Image.alpha_composite(image, card_overlay)
    draw = ImageDraw.Draw(image)

    # Left Accent Bar
    accent_bar_rect = [card_margin_x, card_margin_y, card_margin_x + 8, height - card_margin_y]
    draw.rounded_rectangle(accent_bar_rect, radius=4, fill=(14, 165, 233, 255))

    # 3. Content Layout & Typography
    content_x = card_margin_x + 40
    curr_y = card_margin_y + 45

    # Badge / Category Tag
    cat_text = f"  {category.upper()}  "
    cat_font = _get_font(16, bold=True)
    cat_bbox = draw.textbbox((content_x, curr_y), cat_text, font=cat_font)
    badge_rect = [content_x, curr_y - 4, cat_bbox[2] + 16, cat_bbox[3] + 4]
    draw.rounded_rectangle(badge_rect, radius=6, fill=(14, 165, 233, 40), outline=(56, 189, 248, 180), width=1)
    draw.text((content_x + 8, curr_y), cat_text, font=cat_font, fill=(56, 189, 248, 255))

    curr_y += 50

    # Main Headline
    head_font = _get_font(34, bold=True)
    # Wrap headline if needed
    words = headline.split()
    lines: list[str] = []
    current_line = ""
    for w in words:
        test_line = f"{current_line} {w}".strip()
        if draw.textbbox((0, 0), test_line, font=head_font)[2] < (width - content_x - 120):
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = w
    if current_line:
        lines.append(current_line)

    for line in lines[:3]:
        draw.text((content_x, curr_y), line, font=head_font, fill=(255, 255, 255, 255))
        curr_y += 46

    curr_y += 15

    # Subtext / Metrics Highlight
    sub_font = _get_font(20, bold=False)
    draw.text((content_x, curr_y), subtext, font=sub_font, fill=(148, 163, 184, 255))

    # Bottom Branding Footer inside Card
    footer_y = height - card_margin_y - 45
    draw.line([(content_x, footer_y - 15), (width - card_margin_x - 40, footer_y - 15)], fill=(255, 255, 255, 20), width=1)
    
    brand_font = _get_font(16, bold=True)
    draw.text((content_x, footer_y), "⚡ ChiefMind AI Personal Employee", font=brand_font, fill=(255, 255, 255, 220))
    draw.text((width - card_margin_x - 180, footer_y), "Verified Announcement", font=_get_font(14), fill=(56, 189, 248, 220))

    # Save output
    target_dir = output_dir or Path("dashboard/static/generated_images")
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"linkedin_{action_id}.png"
    image.save(file_path, "PNG")
    return file_path
