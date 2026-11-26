"""Create a printable profile QR card using the site's own logo."""

from base64 import b64encode
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from textwrap import wrap

import qrcode
from PIL import Image, ImageDraw, ImageFont
from qrcode.image.pil import PilImage

LOGO = Path(__file__).with_name("static") / "apple-touch-icon.png"


@lru_cache(maxsize=256)
def profile_qr_image(url: str) -> str:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=16)
    qr.add_data(url)
    image = qr.make_image(image_factory=PilImage).get_image().convert("RGB")
    width = image.width
    with Image.open(LOGO) as source:
        logo_size = round(width * 0.15)
        logo = source.convert("RGBA").resize((logo_size, logo_size), Image.Resampling.LANCZOS)
    offset = (width - logo_size) // 2
    inset = round(width * 0.018)
    ImageDraw.Draw(image).rectangle(
        (offset - inset, offset - inset, offset + logo_size + inset, offset + logo_size + inset),
        fill="white",
    )
    image.paste(logo, (offset, offset), logo)

    lines = wrap(url, width=36, break_on_hyphens=False)
    size = round(width * 0.052)
    font = ImageFont.load_default(size=size)
    while max(map(font.getlength, lines)) > width * 0.92:
        size -= 1
        font = ImageFont.load_default(size=size)
    line_height = round(size * 1.35)
    card = Image.new("RGB", (width, width + line_height * len(lines) + size), "white")
    card.paste(image)
    draw = ImageDraw.Draw(card)
    for index, line in enumerate(lines):
        draw.text((width // 2, width + index * line_height), line, font=font, fill="black", anchor="mt")
    buffer = BytesIO()
    card.save(buffer, format="PNG")
    return "data:image/png;base64," + b64encode(buffer.getvalue()).decode("ascii")
