# /// script
# requires-python = ">=3.11"
# dependencies = ["google-api-python-client", "google-auth"]
# ///
"""
Verify whether Drive files().update() with text/markdown triggers the same
markdown→Google Doc conversion that files().create() does.

This is the key question blocking mise-zobuci (overwrite markdown rendering).

Usage:
    uv run --script scripts/test_overwrite_conversion.py

What it does:
    1. Creates a Google Doc via Drive import (text/markdown → GOOGLE_DOC_MIME)
       with bold, a table, and a heading
    2. Reads the doc to confirm formatting was applied (heading styles, bold runs)
    3. Overwrites the same doc via files().update() with different markdown
    4. Reads the doc again to check if the new content was also converted
    5. Reports whether update triggers conversion (YES/NO)
    6. Cleans up the test doc

Expected: YES — files().update() triggers import conversion like files().create().
If NO: fall back to temp-doc-body-copy approach documented in mise-zobuci brief.
"""

import io
import sys
from googleapiclient.http import MediaIoBaseUpload

# Add parent directory for imports
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from adapters.services import get_drive_service, get_docs_service
from adapters.drive import GOOGLE_DOC_MIME

TEST_MARKDOWN_CREATE = """# Test Heading

This has **bold text** and *italic text*.

| Column A | Column B |
|----------|----------|
| cell 1   | cell 2   |
| cell 3   | cell 4   |

- List item one
- List item two
"""

TEST_MARKDOWN_UPDATE = """# Updated Heading

This updated content has **different bold** and a new table.

| Name | Value |
|------|-------|
| alpha | 100  |
| beta  | 200  |
"""


def check_has_formatting(doc: dict) -> dict:
    """Check if a doc has non-plain-text formatting."""
    body = doc.get("body", {}).get("content", [])
    has_heading = False
    has_bold = False
    has_table = False

    for element in body:
        if "paragraph" in element:
            style = element["paragraph"].get("paragraphStyle", {})
            named = style.get("namedStyleType", "")
            if named.startswith("HEADING"):
                has_heading = True
            for run in element["paragraph"].get("elements", []):
                ts = run.get("textRun", {}).get("textStyle", {})
                if ts.get("bold"):
                    has_bold = True
        if "table" in element:
            has_table = True

    return {"heading": has_heading, "bold": has_bold, "table": has_table}


def main():
    drive = get_drive_service()
    docs = get_docs_service()

    # Step 1: Create via Drive import
    print("1. Creating doc via Drive import (text/markdown)...")
    media = MediaIoBaseUpload(
        io.BytesIO(TEST_MARKDOWN_CREATE.encode("utf-8")),
        mimetype="text/markdown",
        resumable=False,
    )
    created = drive.files().create(
        body={"name": "_mise_test_overwrite_conversion", "mimeType": GOOGLE_DOC_MIME},
        media_body=media,
        fields="id",
    ).execute()
    file_id = created["id"]
    print(f"   Created: {file_id}")

    # Step 2: Read and check formatting
    print("2. Reading doc to verify create formatting...")
    doc = docs.documents().get(documentId=file_id).execute()
    create_fmt = check_has_formatting(doc)
    print(f"   Create formatting: {create_fmt}")

    if not all(create_fmt.values()):
        print("   WARNING: Create didn't produce expected formatting. Test inconclusive.")

    # Step 3: Overwrite via files().update() with markdown
    print("3. Overwriting via files().update() with new markdown...")
    update_media = MediaIoBaseUpload(
        io.BytesIO(TEST_MARKDOWN_UPDATE.encode("utf-8")),
        mimetype="text/markdown",
        resumable=False,
    )
    drive.files().update(
        fileId=file_id,
        media_body=update_media,
        supportsAllDrives=True,
    ).execute()
    print("   Updated.")

    # Step 4: Read and check formatting after update
    print("4. Reading doc to verify update formatting...")
    doc2 = docs.documents().get(documentId=file_id).execute()
    update_fmt = check_has_formatting(doc2)
    print(f"   Update formatting: {update_fmt}")

    # Step 5: Verdict
    print()
    if update_fmt["heading"] and update_fmt["bold"] and update_fmt["table"]:
        print("RESULT: YES — files().update() triggers markdown→Doc conversion!")
        print("The simple fix works: replace _overwrite_doc with files().update().")
    elif update_fmt["heading"] or update_fmt["bold"]:
        print("RESULT: PARTIAL — some formatting preserved, not all.")
        print(f"  Create: {create_fmt}")
        print(f"  Update: {update_fmt}")
        print("May need the temp-doc-body-copy fallback for full fidelity.")
    else:
        print("RESULT: NO — files().update() does NOT convert markdown.")
        print("Use the temp-doc-body-copy approach from mise-zobuci brief.")

    # Step 6: Cleanup
    print()
    print("6. Cleaning up test doc...")
    try:
        drive.files().delete(fileId=file_id).execute()
        print("   Deleted.")
    except Exception as e:
        print(f"   Cleanup failed: {e}")
        print(f"   Manual cleanup: delete file {file_id}")


if __name__ == "__main__":
    main()
