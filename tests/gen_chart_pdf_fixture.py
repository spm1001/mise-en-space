"""
Generator for fixtures/pdf/chart_page.pdf — run once, commit the output.

The smallest thing that exercises exhibit-crop extraction (mise-jopohi):
one page of real text (so pdftotext has a text layer and a page for the
anchor to land in) plus one embedded sub-page JPEG carrying a value that
exists ONLY in pixels — the census's "Excel chart pasted as a picture"
class, miniaturised. Image is 400x300px placed at 300x200pt on US Letter:
min dimension 300 >= 240, single page, ~12% page coverage — squarely
inside the crop filter.

The PDF is hand-assembled (raw xref arithmetic) because no PDF-writing
library is a dependency and pillow's own PDF writer only makes full-page
images, which the coverage filter correctly rejects.

Usage: uv run --all-extras python tests/gen_chart_pdf_fixture.py
"""

import io
import zlib
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).parent.parent / "fixtures" / "pdf" / "chart_page.pdf"

# --- the chart image: bars + a data label no text layer carries ---
img = Image.new("RGB", (400, 300), "white")
d = ImageDraw.Draw(img)
d.rectangle([60, 120, 140, 260], fill=(255, 179, 0))
d.rectangle([230, 60, 310, 260], fill=(230, 81, 0))
d.line([40, 260, 380, 260], fill="black", width=2)
d.text((70, 95), "31.5", fill="black")
d.text((240, 35), "JOPOHI-42.7", fill="black")
buf = io.BytesIO()
img.save(buf, format="JPEG", quality=90)
jpeg = buf.getvalue()

# --- content stream: a line of text, then the image at 300x200pt ---
content = (
    b"BT /F1 12 Tf 72 720 Td "
    b"(The chart below shows the pixel-only value; this sentence is the text layer.) Tj ET\n"
    b"q 300 0 0 200 150 400 cm /Im1 Do Q\n"
)
content_z = zlib.compress(content)

objs = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
    b"/Resources << /XObject << /Im1 4 0 R >> "
    b"/Font << /F1 6 0 R >> >> /Contents 5 0 R >>",
    b"<< /Type /XObject /Subtype /Image /Width 400 /Height 300 "
    b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode "
    b"/Length " + str(len(jpeg)).encode() + b" >>\nstream\n" + jpeg + b"\nendstream",
    b"<< /Filter /FlateDecode /Length " + str(len(content_z)).encode()
    + b" >>\nstream\n" + content_z + b"\nendstream",
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
]

out = bytearray(b"%PDF-1.4\n")
offsets = []
for i, body in enumerate(objs, start=1):
    offsets.append(len(out))
    out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
xref_at = len(out)
out += f"xref\n0 {len(objs)+1}\n".encode()
out += b"0000000000 65535 f \n"
for off in offsets:
    out += f"{off:010d} 00000 n \n".encode()
out += (
    f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\n"
    f"startxref\n{xref_at}\n%%EOF\n"
).encode()

OUT.write_bytes(bytes(out))
print(f"wrote {OUT} ({len(out)} bytes, jpeg {len(jpeg)} bytes)")
