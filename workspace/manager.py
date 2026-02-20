"""
Workspace Manager — Handles file deposit for MCP deliveries.

Deposits fetched content into mise/{type}--{title}--{id}/ folders
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
ContentType = Literal["slides", "doc", "sheet", "gmail", "pdf", "docx", "xlsx", "pptx", "video", "image", "text", "web", "folder"]


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
        mise/{type}--{title-slug}--{id}/

    Args:
        content_type: Type of content (slides, doc, sheet, gmail)
        title: Human-readable title (will be slugified)
        resource_id: Google resource ID (presentation ID, doc ID, etc)
        base_path: Base directory (defaults to cwd)

    Returns:
        Path to the deposit folder (created if not exists)

    Example:
        get_deposit_folder("slides", "AMI Deck 2026", "1OepZju...")
        -> Path("mise/slides--ami-deck-2026--1OepZju.../")
    """
    if base_path is None:
        raise ValueError("base_path is required — deposits must not fall back to MCP server's cwd")
    mise_fetch = base_path / "mise"

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


def write_page_thumbnail(
    folder: Path,
    image_bytes: bytes,
    page_index: int,
) -> Path:
    """
    Write a PDF page thumbnail to the deposit folder.

    Args:
        folder: Deposit folder from get_deposit_folder()
        image_bytes: PNG image data
        page_index: 0-based page index

    Returns:
        Path to the written file

    Example:
        write_page_thumbnail(folder, png_bytes, 0) -> folder/page_01.png
    """
    # 1-indexed, zero-padded for sorting
    filename = f"page_{page_index + 1:02d}.png"
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


def enrich_manifest(folder: Path, extra: dict[str, Any]) -> Path:
    """
    Merge additional fields into an existing manifest.json.

    Used post-creation to stamp a deposit with its published state
    (file_id, web_link, status, created_at).

    Args:
        folder: Deposit folder containing manifest.json
        extra: Fields to merge into the manifest

    Returns:
        Path to the updated manifest file

    Raises:
        FileNotFoundError: If manifest.json doesn't exist in folder
    """
    manifest_path = folder / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(extra)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest_path


def write_search_results(
    query: str,
    results: dict[str, Any],
    base_path: Path | None = None,
) -> Path:
    """
    Write search results to a JSON file in mise/.

    Args:
        query: The search query (used for slugified filename)
        results: The full search results dict
        base_path: Base directory (defaults to cwd)

    Returns:
        Path to the written file

    Example:
        write_search_results("Q4 planning", {...})
        -> Path("mise/search--q4-planning--2026-01-31T21-12-53.json")
    """
    if base_path is None:
        raise ValueError("base_path is required — deposits must not fall back to MCP server's cwd")
    mise_fetch = base_path / "mise"
    mise_fetch.mkdir(parents=True, exist_ok=True)

    # Build filename: search--{query-slug}--{timestamp}.json
    slug = slugify(query, max_length=40)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    filename = f"search--{slug}--{timestamp}.json"

    file_path = mise_fetch / filename
    file_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return file_path


