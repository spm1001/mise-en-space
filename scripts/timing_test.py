#!/usr/bin/env python3
"""
Timing test: thread text vs thread + attachments

Measures the cost of fetching attachments to inform design decisions.
"""

import sys
import time
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from adapters.gmail import fetch_thread
from adapters.services import get_gmail_service
from extractors.gmail import extract_thread_content


def time_thread_fetch(thread_id: str) -> dict:
    """Time the various stages of thread fetching."""
    results = {}
    
    # Stage 1: Fetch thread (text only)
    start = time.perf_counter()
    thread_data = fetch_thread(thread_id)
    results["thread_fetch_ms"] = (time.perf_counter() - start) * 1000
    
    # Stage 2: Extract content
    start = time.perf_counter()
    content = extract_thread_content(thread_data)
    results["extraction_ms"] = (time.perf_counter() - start) * 1000
    
    # Collect attachment info
    attachments = []
    for msg in thread_data.messages:
        for att in msg.attachments:
            attachments.append({
                "filename": att.filename,
                "size": att.size,
                "mime_type": att.mime_type,
                "message_id": msg.message_id,
                "attachment_id": att.attachment_id,
            })
    
    results["attachment_count"] = len(attachments)
    results["attachments"] = attachments
    results["message_count"] = len(thread_data.messages)
    results["content_length"] = len(content)
    
    # Stage 3: Download each attachment (if any)
    if attachments:
        service = get_gmail_service()
        download_times = []
        
        for att in attachments:
            start = time.perf_counter()
            try:
                # Download attachment bytes
                att_data = service.users().messages().attachments().get(
                    userId="me",
                    messageId=att["message_id"],
                    id=att["attachment_id"]
                ).execute()
                elapsed = (time.perf_counter() - start) * 1000
                download_times.append({
                    "filename": att["filename"],
                    "size_kb": att["size"] / 1024,
                    "download_ms": elapsed,
                })
            except Exception as e:
                download_times.append({
                    "filename": att["filename"],
                    "error": str(e),
                })
        
        results["attachment_downloads"] = download_times
        results["total_download_ms"] = sum(d.get("download_ms", 0) for d in download_times)
    
    return results


def main():
    if len(sys.argv) < 2:
        print("Usage: python timing_test.py <thread_id_or_url>")
        print("\nFind a thread with attachments and pass its ID or URL.")
        sys.exit(1)
    
    from validation import extract_gmail_id
    thread_id = extract_gmail_id(sys.argv[1])
    
    print(f"Testing thread: {thread_id}\n")
    
    results = time_thread_fetch(thread_id)
    
    print("=" * 60)
    print("TIMING RESULTS")
    print("=" * 60)
    print(f"Messages: {results['message_count']}")
    print(f"Content length: {results['content_length']:,} chars")
    print(f"Attachments: {results['attachment_count']}")
    print()
    print(f"Thread fetch:     {results['thread_fetch_ms']:>8.1f} ms")
    print(f"Text extraction:  {results['extraction_ms']:>8.1f} ms")
    print(f"                  ─────────")
    print(f"Subtotal (text):  {results['thread_fetch_ms'] + results['extraction_ms']:>8.1f} ms")
    print()
    
    if results.get("attachment_downloads"):
        print("Attachment downloads:")
        for d in results["attachment_downloads"]:
            if "error" in d:
                print(f"  {d['filename']}: ERROR - {d['error']}")
            else:
                print(f"  {d['filename']:30} {d['size_kb']:>8.1f} KB  {d['download_ms']:>8.1f} ms")
        print(f"                  ─────────")
        print(f"Download total:   {results['total_download_ms']:>8.1f} ms")
        print()
        print(f"GRAND TOTAL:      {results['thread_fetch_ms'] + results['extraction_ms'] + results['total_download_ms']:>8.1f} ms")
    
    print()
    print("=" * 60)
    print("INTERPRETATION")
    print("=" * 60)
    text_time = results['thread_fetch_ms'] + results['extraction_ms']
    total_time = text_time + results.get('total_download_ms', 0)
    
    if results['attachment_count'] > 0:
        print(f"Text-only return:  {text_time:.0f} ms")
        print(f"With attachments:  {total_time:.0f} ms")
        print(f"Attachment cost:   {total_time - text_time:.0f} ms ({(total_time - text_time) / text_time * 100:.0f}% overhead)")
        print()
        print("Note: This is download only. PDF/Office extraction adds more time.")
    else:
        print("No attachments in this thread.")


if __name__ == "__main__":
    main()
