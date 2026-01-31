"""
Workspace Manager — Handles file deposit for MCP deliveries.

Deposits fetched content into mise-fetch/{type}--{title}--{id}/ folders
in the current working directory. Each fetch gets its own folder.

This is a filesystem-first pattern: content goes to disk, Claude reads
what it needs. No context window spam.
"""

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

# Type aliases
ContentType = Literal["slides", "doc", "sheet", "gmail", "pdf", "docx", "xlsx", "pptx", "video", "image", "text"]


def slugify(text: str, max_length: int = 50) -> str:
    """
    Convert text to a filesystem-safe slug.

    - Converts to lowercase
    - Replaces spaces/punctuation with hyphens
    - Removes non-ASCII characters
    - Collapses multiple hyphens
    - Truncates to max_length

    Examples:
        "AMI Deck 2026" -> "ami-deck-2026"
        "Q4 Planning Notes (Draft)" -> "q4-planning-notes-draft"
        "Über Cool Presentation!!!" -> "uber-cool-presentation"
    """
    # Normalize unicode (é -> e, etc)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")

    # Lowercase
    text = text.lower()

    # Replace non-alphanumeric with hyphens
    text = re.sub(r"[^a-z0-9]+", "-", text)

    # Remove leading/trailing hyphens
    text = text.strip("-")

    # Collapse multiple hyphens
    text = re.sub(r"-+", "-", text)

    # Truncate
    if len(text) > max_length:
        # Try to break at a hyphen
        text = text[:max_length].rsplit("-", 1)[0]

    return text or "untitled"


def get_deposit_folder(
    content_type: ContentType,
    title: str,
    resource_id: str,
    base_path: Path | None = None,
) -> Path:
    """
    Get the folder path for depositing fetched content.

    Creates the folder structure:
        mise-fetch/{type}--{title-slug}--{id}/

    Args:
        content_type: Type of content (slides, doc, sheet, gmail)
        title: Human-readable title (will be slugified)
        resource_id: Google resource ID (presentation ID, doc ID, etc)
        base_path: Base directory (defaults to cwd)

    Returns:
        Path to the deposit folder (created if not exists)

    Example:
        get_deposit_folder("slides", "AMI Deck 2026", "1OepZju...")
        -> Path("mise-fetch/slides--ami-deck-2026--1OepZju.../")
    """
    base = base_path or Path.cwd()
    mise_fetch = base / "mise-fetch"

    # Build folder name: {type}--{slug}--{id}
    # Truncate ID to first 12 chars for readability
    slug = slugify(title)
    short_id = resource_id[:12] if len(resource_id) > 12 else resource_id
    folder_name = f"{content_type}--{slug}--{short_id}"

    folder_path = mise_fetch / folder_name
    folder_path.mkdir(parents=True, exist_ok=True)

    return folder_path


def write_content(
    folder: Path,
    content: str,
    filename: str = "content.md",
) -> Path:
    """
    Write text content to the deposit folder.

    Args:
        folder: Deposit folder from get_deposit_folder()
        content: Text content to write
        filename: Output filename (default: content.md)

    Returns:
        Path to the written file
    """
    file_path = folder / filename
    file_path.write_text(content, encoding="utf-8")
    return file_path


def write_thumbnail(
    folder: Path,
    image_bytes: bytes,
    slide_index: int,
) -> Path:
    """
    Write a slide thumbnail to the deposit folder.

    Args:
        folder: Deposit folder from get_deposit_folder()
        image_bytes: PNG image data
        slide_index: 0-based slide index

    Returns:
        Path to the written file

    Example:
        write_thumbnail(folder, png_bytes, 0) -> folder/slide_01.png
    """
    # 1-indexed, zero-padded for sorting
    filename = f"slide_{slide_index + 1:02d}.png"
    file_path = folder / filename
    file_path.write_bytes(image_bytes)
    return file_path


def write_image(
    folder: Path,
    image_bytes: bytes,
    filename: str,
) -> Path:
    """
    Write image file to deposit folder.

    Args:
        folder: Deposit folder from get_deposit_folder()
        image_bytes: Image data (any format)
        filename: Output filename (e.g., "image.png", "image.svg")

    Returns:
        Path to the written file
    """
    file_path = folder / filename
    file_path.write_bytes(image_bytes)
    return file_path


def write_chart(
    folder: Path,
    image_bytes: bytes,
    chart_index: int,
) -> Path:
    """
    Write a chart PNG to the deposit folder.

    Args:
        folder: Deposit folder from get_deposit_folder()
        image_bytes: PNG image data
        chart_index: 0-based chart index

    Returns:
        Path to the written file

    Example:
        write_chart(folder, png_bytes, 0) -> folder/chart_01.png
    """
    # 1-indexed, zero-padded for sorting
    filename = f"chart_{chart_index + 1:02d}.png"
    file_path = folder / filename
    file_path.write_bytes(image_bytes)
    return file_path


def write_charts_metadata(
    folder: Path,
    charts: list[dict[str, Any]],
) -> Path:
    """
    Write charts.json metadata to the deposit folder.

    Args:
        folder: Deposit folder from get_deposit_folder()
        charts: List of chart metadata dicts with title, type, sheet_name, etc.

    Returns:
        Path to the written file
    """
    file_path = folder / "charts.json"
    file_path.write_text(json.dumps(charts, indent=2), encoding="utf-8")
    return file_path


def write_manifest(
    folder: Path,
    content_type: ContentType,
    title: str,
    resource_id: str,
    extra: dict[str, Any] | None = None,
) -> Path:
    """
    Write a manifest.json to make the deposit folder self-describing.

    Args:
        folder: Deposit folder from get_deposit_folder()
        content_type: Type of content (slides, doc, sheet, gmail)
        title: Original title
        resource_id: Google resource ID
        extra: Additional metadata (slide_count, has_thumbnails, warnings, etc.)

    Returns:
        Path to the manifest file
    """
    manifest = {
        "type": content_type,
        "title": title,
        "id": resource_id,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        manifest.update(extra)

    file_path = folder / "manifest.json"
    file_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return file_path


def write_search_results(
    query: str,
    results: dict[str, Any],
    base_path: Path | None = None,
) -> Path:
    """
    Write search results to a JSON file in mise-fetch/.

    Args:
        query: The search query (used for slugified filename)
        results: The full search results dict
        base_path: Base directory (defaults to cwd)

    Returns:
        Path to the written file

    Example:
        write_search_results("Q4 planning", {...})
        -> Path("mise-fetch/search--q4-planning--2026-01-31T21-12-53.json")
    """
    base = base_path or Path.cwd()
    mise_fetch = base / "mise-fetch"
    mise_fetch.mkdir(parents=True, exist_ok=True)

    # Build filename: search--{query-slug}--{timestamp}.json
    slug = slugify(query, max_length=40)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    filename = f"search--{slug}--{timestamp}.json"

    file_path = mise_fetch / filename
    file_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return file_path


def list_deposit_folders(base_path: Path | None = None) -> list[Path]:
    """
    List all deposit folders in mise-fetch/.

    Args:
        base_path: Base directory (defaults to cwd)

    Returns:
        List of deposit folder paths, sorted by modification time (newest first)
    """
    base = base_path or Path.cwd()
    mise_fetch = base / "mise-fetch"

    if not mise_fetch.exists():
        return []

    folders = [f for f in mise_fetch.iterdir() if f.is_dir()]
    # Sort by modification time, newest first
    folders.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return folders


def parse_folder_name(folder: Path) -> dict[str, str] | None:
    """
    Parse a deposit folder name into its components.

    Args:
        folder: Path to a deposit folder

    Returns:
        Dict with 'type', 'title_slug', 'id' or None if not parseable

    Example:
        parse_folder_name(Path("mise-fetch/slides--ami-deck--1OepZ"))
        -> {"type": "slides", "title_slug": "ami-deck", "id": "1OepZ"}
    """
    name = folder.name
    parts = name.split("--")
    if len(parts) >= 3:
        return {
            "type": parts[0],
            "title_slug": "--".join(parts[1:-1]),  # Handle titles with -- in them
            "id": parts[-1],
        }
    return None


def get_deposit_summary(folder: Path) -> dict[str, str | int | list[str]]:
    """
    Get a summary of a deposit folder for MCP response.

    Args:
        folder: Deposit folder path

    Returns:
        Dict with folder metadata suitable for MCP response
    """
    parsed = parse_folder_name(folder)
    files = list(folder.iterdir())

    # Separate content, thumbnails, and charts
    content_file = None
    thumbnails: list[str] = []
    charts: list[str] = []

    for f in files:
        if f.suffix == ".md":
            content_file = f.name
        elif f.suffix == ".png" and f.name.startswith("slide_"):
            thumbnails.append(f.name)
        elif f.suffix == ".png" and f.name.startswith("chart_"):
            charts.append(f.name)

    thumbnails.sort()
    charts.sort()

    result: dict[str, str | int | list[str]] = {
        "path": str(folder),
        "type": parsed["type"] if parsed else "unknown",
        "id": parsed["id"] if parsed else folder.name,
    }

    if content_file:
        result["content_file"] = content_file
    if thumbnails:
        result["thumbnails"] = thumbnails
        result["thumbnail_count"] = len(thumbnails)
    if charts:
        result["charts"] = charts
        result["chart_count"] = len(charts)

    return result
