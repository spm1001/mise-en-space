"""Sample Drive file descriptions to understand the distribution."""

from adapters.drive import get_drive_service


def sample_descriptions(max_files: int = 100):
    """Sample descriptions from Drive files."""
    service = get_drive_service()

    # Search for files with descriptions (non-empty)
    response = service.files().list(
        q="description != ''",
        pageSize=min(max_files, 100),
        fields="files(id,name,description,parents)",
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()

    files = response.get("files", [])

    if not files:
        print("No files with descriptions found.")
        return []

    # Analyze
    results = []
    for f in files:
        desc = f.get("description", "")
        has_message_id = "Message ID:" in desc
        results.append({
            "name": f.get("name"),
            "desc_length": len(desc),
            "is_exfil": has_message_id,
            "preview": desc[:100] + "..." if len(desc) > 100 else desc,
        })

    # Summary
    exfil_count = sum(1 for r in results if r["is_exfil"])
    lengths = [r["desc_length"] for r in results]

    print(f"Files with descriptions: {len(results)}")
    print(f"Exfil'd files: {exfil_count}")
    print(f"Non-exfil files: {len(results) - exfil_count}")
    print(f"Description lengths: min={min(lengths)}, max={max(lengths)}, avg={sum(lengths)/len(lengths):.0f}")

    # Show non-exfil descriptions
    non_exfil = [r for r in results if not r["is_exfil"]]
    if non_exfil:
        print("\n--- Non-exfil descriptions (showing first 20) ---")
        for r in non_exfil[:20]:
            print(f"  {r['name']}: [{r['desc_length']} chars] {r['preview']}")

    # Show length distribution for exfil files
    exfil_lengths = [r["desc_length"] for r in results if r["is_exfil"]]
    if exfil_lengths:
        print(f"\n--- Exfil description lengths ---")
        print(f"  min={min(exfil_lengths)}, max={max(exfil_lengths)}, avg={sum(exfil_lengths)/len(exfil_lengths):.0f}")

    return results


if __name__ == "__main__":
    sample_descriptions()
