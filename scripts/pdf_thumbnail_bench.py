# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pdf2image>=1.17.0",
#     "pymupdf>=1.25.0",
#     "reportlab>=4.0",
# ]
# ///
"""
Benchmark PDF page rendering: pdf2image (poppler) vs PyMuPDF.

Tests: speed, output size, quality at various DPIs.
Creates a synthetic multi-page PDF if no path given.
"""
import io
import sys
import time
import tempfile
from pathlib import Path


def create_test_pdf(pages: int = 10) -> bytes:
    """Create a synthetic PDF with mixed content (text + shapes)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.colors import HexColor
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    for i in range(pages):
        # Background color block (simulates visual content)
        c.setFillColor(HexColor(f"#{(i*37+50) % 256:02x}{(i*73+100) % 256:02x}{(i*17+150) % 256:02x}"))
        c.rect(50, h - 200, w - 100, 150, fill=True)

        # Title text
        c.setFillColor(HexColor("#000000"))
        c.setFont("Helvetica-Bold", 24)
        c.drawString(50, h - 260, f"Page {i + 1}: Test Content")

        # Body text
        c.setFont("Helvetica", 12)
        for j in range(15):
            c.drawString(50, h - 300 - j * 20,
                         f"Line {j + 1}: Lorem ipsum dolor sit amet, consectetur adipiscing elit.")

        # Simple chart-like shapes
        for j in range(5):
            bar_height = ((i * 13 + j * 37) % 150) + 30
            c.setFillColor(HexColor(f"#{(j*50+100) % 256:02x}80c0"))
            c.rect(50 + j * 100, 50, 80, bar_height, fill=True)

        c.showPage()

    c.save()
    return buf.getvalue()


def bench_pdf2image(pdf_bytes: bytes, dpi: int, pages: int | None = None) -> dict:
    """Benchmark pdf2image (poppler backend)."""
    from pdf2image import convert_from_bytes

    start = time.perf_counter()
    kwargs = {"fmt": "png", "dpi": dpi}
    if pages:
        kwargs["first_page"] = 1
        kwargs["last_page"] = pages

    images = convert_from_bytes(pdf_bytes, **kwargs)
    elapsed = time.perf_counter() - start

    # Measure output sizes
    sizes = []
    for img in images:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        sizes.append(len(buf.getvalue()))

    return {
        "library": "pdf2image (poppler)",
        "dpi": dpi,
        "pages": len(images),
        "time_s": round(elapsed, 3),
        "ms_per_page": round(elapsed / len(images) * 1000, 1),
        "avg_size_kb": round(sum(sizes) / len(sizes) / 1024, 1),
        "total_size_kb": round(sum(sizes) / 1024, 1),
        "resolution": f"{images[0].width}x{images[0].height}",
    }


def bench_pymupdf(pdf_bytes: bytes, dpi: int, pages: int | None = None) -> dict:
    """Benchmark PyMuPDF (fitz)."""
    import fitz

    start = time.perf_counter()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    page_count = pages or len(doc)
    zoom = dpi / 72  # PyMuPDF default is 72 DPI
    mat = fitz.Matrix(zoom, zoom)

    images = []
    for i in range(min(page_count, len(doc))):
        pix = doc[i].get_pixmap(matrix=mat)
        images.append(pix.tobytes("png"))

    elapsed = time.perf_counter() - start
    doc.close()

    # First image for resolution
    from PIL import Image
    img = Image.open(io.BytesIO(images[0]))

    return {
        "library": "PyMuPDF (fitz)",
        "dpi": dpi,
        "pages": len(images),
        "time_s": round(elapsed, 3),
        "ms_per_page": round(elapsed / len(images) * 1000, 1),
        "avg_size_kb": round(sum(len(b) for b in images) / len(images) / 1024, 1),
        "total_size_kb": round(sum(len(b) for b in images) / 1024, 1),
        "resolution": f"{img.width}x{img.height}",
    }


def bench_pymupdf_jpeg(pdf_bytes: bytes, dpi: int, quality: int = 80, pages: int | None = None) -> dict:
    """Benchmark PyMuPDF with JPEG output (smaller files)."""
    import fitz

    start = time.perf_counter()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    page_count = pages or len(doc)
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)

    images = []
    for i in range(min(page_count, len(doc))):
        pix = doc[i].get_pixmap(matrix=mat)
        images.append(pix.tobytes("jpeg"))

    elapsed = time.perf_counter() - start
    doc.close()

    from PIL import Image
    img = Image.open(io.BytesIO(images[0]))

    return {
        "library": f"PyMuPDF (JPEG q={quality})",
        "dpi": dpi,
        "pages": len(images),
        "time_s": round(elapsed, 3),
        "ms_per_page": round(elapsed / len(images) * 1000, 1),
        "avg_size_kb": round(sum(len(b) for b in images) / len(images) / 1024, 1),
        "total_size_kb": round(sum(len(b) for b in images) / 1024, 1),
        "resolution": f"{img.width}x{img.height}",
    }


def main():
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else None

    if pdf_path:
        pdf_bytes = Path(pdf_path).read_bytes()
        print(f"Using: {pdf_path} ({len(pdf_bytes) / 1024:.1f} KB)")
    else:
        print("Creating synthetic 10-page PDF...")
        pdf_bytes = create_test_pdf(10)
        print(f"Created: {len(pdf_bytes) / 1024:.1f} KB, 10 pages")

    print()

    # Run benchmarks at different DPIs
    results = []
    for dpi in [72, 100, 150]:
        print(f"--- DPI {dpi} ---")
        try:
            r = bench_pdf2image(pdf_bytes, dpi)
            results.append(r)
            print(f"  pdf2image:     {r['time_s']}s total, {r['ms_per_page']}ms/page, "
                  f"{r['avg_size_kb']}KB/page, {r['resolution']}")
        except Exception as e:
            print(f"  pdf2image:     FAILED — {e}")

        try:
            r = bench_pymupdf(pdf_bytes, dpi)
            results.append(r)
            print(f"  PyMuPDF PNG:   {r['time_s']}s total, {r['ms_per_page']}ms/page, "
                  f"{r['avg_size_kb']}KB/page, {r['resolution']}")
        except Exception as e:
            print(f"  PyMuPDF PNG:   FAILED — {e}")

        try:
            r = bench_pymupdf_jpeg(pdf_bytes, dpi)
            results.append(r)
            print(f"  PyMuPDF JPEG:  {r['time_s']}s total, {r['ms_per_page']}ms/page, "
                  f"{r['avg_size_kb']}KB/page, {r['resolution']}")
        except Exception as e:
            print(f"  PyMuPDF JPEG:  FAILED — {e}")

        print()

    # Summary
    if results:
        print("=== SUMMARY ===")
        fastest = min(results, key=lambda r: r["ms_per_page"])
        smallest = min(results, key=lambda r: r["avg_size_kb"])
        print(f"Fastest:  {fastest['library']} @ {fastest['dpi']} DPI — {fastest['ms_per_page']}ms/page")
        print(f"Smallest: {smallest['library']} @ {smallest['dpi']} DPI — {smallest['avg_size_kb']}KB/page")


if __name__ == "__main__":
    main()
