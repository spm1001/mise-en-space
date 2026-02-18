# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pyobjc-framework-Quartz>=10.0",
# ]
# ///
"""
Benchmark PDF page rendering using macOS-native APIs.

Three approaches:
1. qlmanage -t (Quick Look thumbnail — one page only)
2. sips (can't do PDF→image directly, skip)
3. CoreGraphics via PyObjC (full per-page rendering)

Run on macOS only: uv run --script pdf_thumb_macos.py <path-to-pdf>
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def bench_qlmanage(pdf_path: str) -> dict | None:
    """Quick Look thumbnail — single page, no deps beyond macOS."""
    with tempfile.TemporaryDirectory() as tmpdir:
        start = time.perf_counter()
        result = subprocess.run(
            ["qlmanage", "-t", "-s", "1200", "-o", tmpdir, pdf_path],
            capture_output=True, text=True, timeout=30,
        )
        elapsed = time.perf_counter() - start

        if result.returncode != 0:
            print(f"  qlmanage failed: {result.stderr.strip()}")
            return None

        # qlmanage outputs <filename>.png in the output dir
        pngs = list(Path(tmpdir).glob("*.png"))
        if not pngs:
            print(f"  qlmanage: no output files")
            return None

        size = pngs[0].stat().st_size
        return {
            "method": "qlmanage (Quick Look)",
            "pages": 1,
            "time_ms": round(elapsed * 1000, 1),
            "size_kb": round(size / 1024, 1),
            "note": "Page 1 only",
        }


def bench_cgpdf(pdf_path: str, dpi: int = 72, max_pages: int = 50) -> dict | None:
    """CoreGraphics PDF rendering via PyObjC — full per-page."""
    try:
        import Quartz
        from Foundation import NSURL
    except ImportError:
        print("  CoreGraphics: PyObjC not available (pip install pyobjc-framework-Quartz)")
        return None

    url = NSURL.fileURLWithPath_(pdf_path)
    pdf_doc = Quartz.CGPDFDocumentCreateWithURL(url)
    if not pdf_doc:
        print("  CoreGraphics: couldn't open PDF")
        return None

    page_count = min(Quartz.CGPDFDocumentGetNumberOfPages(pdf_doc), max_pages)
    scale = dpi / 72.0

    results = []
    total_start = time.perf_counter()

    for i in range(1, page_count + 1):
        page = Quartz.CGPDFDocumentGetPage(pdf_doc, i)
        if not page:
            continue

        start = time.perf_counter()

        # Get page dimensions
        rect = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFMediaBox)
        w = int(rect.size.width * scale)
        h = int(rect.size.height * scale)

        # Create bitmap context
        cs = Quartz.CGColorSpaceCreateDeviceRGB()
        ctx = Quartz.CGBitmapContextCreate(
            None, w, h, 8, w * 4, cs,
            Quartz.kCGImageAlphaPremultipliedFirst,
        )

        # White background
        Quartz.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)
        Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, w, h))

        # Scale and render
        Quartz.CGContextScaleCTM(ctx, scale, scale)
        Quartz.CGContextDrawPDFPage(ctx, page)

        # Get image
        image = Quartz.CGBitmapContextCreateImage(ctx)

        # Export to PNG
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            dest = Quartz.CGImageDestinationCreateWithURL(
                NSURL.fileURLWithPath_(tmp.name), "public.png", 1, None
            )
            Quartz.CGImageDestinationAddImage(dest, image, None)
            Quartz.CGImageDestinationFinalize(dest)
            size = os.path.getsize(tmp.name)
            os.unlink(tmp.name)

        elapsed = time.perf_counter() - start
        results.append({
            "page": i,
            "time_ms": round(elapsed * 1000, 1),
            "size_kb": round(size / 1024, 1),
        })

    total_elapsed = time.perf_counter() - total_start

    if not results:
        return None

    return {
        "method": f"CoreGraphics (Quartz) @ {dpi} DPI",
        "pages": len(results),
        "time_s": round(total_elapsed, 3),
        "ms_per_page": round(total_elapsed / len(results) * 1000, 1),
        "avg_size_kb": round(sum(r["size_kb"] for r in results) / len(results), 1),
        "total_size_kb": round(sum(r["size_kb"] for r in results), 1),
        "per_page": results[:10],
    }


def main():
    if sys.platform != "darwin":
        print("This script requires macOS. Run on your Mac.")
        sys.exit(1)

    pdf_path = sys.argv[1] if len(sys.argv) > 1 else None
    if not pdf_path:
        print("Usage: uv run --script pdf_thumb_macos.py <path-to-pdf>")
        sys.exit(1)

    pdf_path = str(Path(pdf_path).resolve())
    print(f"PDF: {Path(pdf_path).name}")
    print(f"Size: {Path(pdf_path).stat().st_size / 1024:.1f} KB")
    print()

    # Quick Look (page 1 only)
    print("=== qlmanage (Quick Look) ===")
    ql = bench_qlmanage(pdf_path)
    if ql:
        print(f"  {ql['time_ms']}ms, {ql['size_kb']}KB ({ql['note']})")
    print()

    # CoreGraphics at different DPIs
    for dpi in [72, 100, 150]:
        print(f"=== CoreGraphics @ {dpi} DPI ===")
        cg = bench_cgpdf(pdf_path, dpi=dpi)
        if cg:
            print(f"  {cg['pages']} pages, {cg['time_s']}s total, "
                  f"{cg['ms_per_page']}ms/page, {cg['avg_size_kb']}KB/page avg")
            if cg["per_page"]:
                for r in cg["per_page"]:
                    print(f"    Page {r['page']:2d}: {r['time_ms']:6.1f}ms  {r['size_kb']:6.1f}KB")
                if cg["pages"] > 10:
                    print(f"    ... ({cg['pages'] - 10} more)")
        print()


if __name__ == "__main__":
    main()
