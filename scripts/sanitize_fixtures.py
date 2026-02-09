#!/usr/bin/env python3
"""
Sanitize fixtures by replacing PII with generic values.

Replaces:
- Email addresses → alice@example.com, bob@example.com, etc.
- Names → Alice Smith, Bob Jones, etc.
- Domains → example.com, test.org
- IPs → 192.0.2.x (RFC 5737 documentation range)
"""

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = PROJECT_ROOT / "fixtures"

# Mapping of real values to sanitized values (built during processing)
EMAIL_MAP: dict[str, str] = {}
NAME_MAP: dict[str, str] = {}

# Generic replacements
GENERIC_EMAILS = [
    "alice@example.com",
    "bob@example.com",
    "carol@example.com",
    "david@example.com",
    "eve@example.com",
]

GENERIC_NAMES = [
    "Alice Smith",
    "Bob Jones",
    "Carol Williams",
    "David Brown",
    "Eve Davis",
]

GENERIC_TITLES = [
    "Q4 Budget Report",
    "Project Proposal",
    "Meeting Notes",
    "Strategic Plan",
    "Team Update",
]

GENERIC_FOLDER_NAMES = [
    "Shared Folder",
    "Team Workspace",
    "Project Files",
]

# Mapping of real titles/folders to sanitized values (built during processing)
TITLE_MAP: dict[str, str] = {}

# Patterns to find and replace
EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
# Common name patterns in email headers: "Name <email>" or just the name part
NAME_EMAIL_PATTERN = re.compile(r'([A-Z][a-z]+ [A-Z][a-z]+)\s*<[^>]+>')


def get_sanitized_email(email: str) -> str:
    """Get or create a sanitized email for a real email."""
    email_lower = email.lower()

    # Skip already-sanitized emails
    if 'example.com' in email_lower or 'example.org' in email_lower:
        return email

    # Skip Google internal addresses
    if email_lower.endswith('.google.com') or '1e100.net' in email_lower:
        return email

    if email_lower not in EMAIL_MAP:
        idx = len(EMAIL_MAP) % len(GENERIC_EMAILS)
        EMAIL_MAP[email_lower] = GENERIC_EMAILS[idx]

    return EMAIL_MAP[email_lower]


def get_sanitized_name(name: str) -> str:
    """Get or create a sanitized name for a real name.

    Also creates a "Last, First" variant for email header formats.
    """
    name_lower = name.lower()

    if name_lower not in NAME_MAP:
        idx = len(NAME_MAP) % len(GENERIC_NAMES)
        sanitized = GENERIC_NAMES[idx]
        NAME_MAP[name_lower] = sanitized
        # Add variants for "Last, First" and first-name-only
        parts = name.split()
        if len(parts) == 2:
            sanitized_parts = sanitized.split()
            if len(sanitized_parts) == 2:
                # "Last, First" variant
                reversed_name = f"{parts[1]}, {parts[0]}"
                if reversed_name.lower() not in NAME_MAP:
                    NAME_MAP[reversed_name.lower()] = f"{sanitized_parts[1]}, {sanitized_parts[0]}"
                # First-name-only variant
                if parts[0].lower() not in NAME_MAP:
                    NAME_MAP[parts[0].lower()] = sanitized_parts[0]

    return NAME_MAP[name_lower]


def sanitize_string(text: str) -> str:
    """Sanitize a string by replacing emails and names."""
    if not text:
        return text

    # Replace "Name <email>" patterns first
    def replace_name_email(match: re.Match) -> str:
        name = match.group(1)
        full_match = match.group(0)
        # Extract email from the full match
        email_match = EMAIL_PATTERN.search(full_match)
        if email_match:
            sanitized_email = get_sanitized_email(email_match.group(0))
            sanitized_name = get_sanitized_name(name)
            return f"{sanitized_name} <{sanitized_email}>"
        return full_match

    text = NAME_EMAIL_PATTERN.sub(replace_name_email, text)

    # Replace remaining standalone emails
    def replace_email(match: re.Match) -> str:
        return get_sanitized_email(match.group(0))

    text = EMAIL_PATTERN.sub(replace_email, text)

    # Replace known names from NAME_MAP (standalone, not paired with email)
    for real_name, sanitized_name in NAME_MAP.items():
        text = re.sub(re.escape(real_name), sanitized_name, text, flags=re.IGNORECASE)

    # Replace known titles from TITLE_MAP
    for real_title, sanitized_title in TITLE_MAP.items():
        text = re.sub(re.escape(real_title), sanitized_title, text, flags=re.IGNORECASE)

    # Replace company name in remaining text (e.g. folder names)
    text = re.sub(r'\bITV\b', 'Acme', text)

    # Replace IP addresses with documentation range
    text = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '192.0.2.1', text)

    # Replace specific domains in URLs/text (but keep structure)
    text = re.sub(r'planetmodha\.com', 'example.com', text, flags=re.IGNORECASE)
    text = re.sub(r'itv\.com', 'example.org', text, flags=re.IGNORECASE)

    return text


def sanitize_value(value):
    """Recursively sanitize a JSON value."""
    if isinstance(value, str):
        return sanitize_string(value)
    elif isinstance(value, dict):
        return {k: sanitize_value(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [sanitize_value(item) for item in value]
    else:
        return value


def get_sanitized_title(title: str) -> str:
    """Get or create a sanitized title for a real file/folder title."""
    title_lower = title.lower()

    # Skip already-generic titles
    for generic in GENERIC_TITLES + GENERIC_FOLDER_NAMES:
        if title_lower == generic.lower():
            return title

    if title_lower not in TITLE_MAP:
        # Preserve file extension if present
        ext = ""
        base = title
        if "." in title:
            parts = title.rsplit(".", 1)
            if len(parts[1]) <= 5:  # Reasonable extension length
                base, ext = parts
                ext = "." + ext

        idx = len(TITLE_MAP) % len(GENERIC_TITLES)
        TITLE_MAP[title_lower] = GENERIC_TITLES[idx] + ext

    return TITLE_MAP[title_lower]


def _collect_titles(data) -> None:
    """Pre-scan fixture for file/folder titles and add them to TITLE_MAP."""
    if isinstance(data, dict):
        for key in ("title", "oldTitle", "newTitle"):
            if key in data:
                val = data[key]
                if val and isinstance(val, str):
                    get_sanitized_title(val)
        for v in data.values():
            _collect_titles(v)
    elif isinstance(data, list):
        for item in data:
            _collect_titles(item)


def _collect_author_names(data) -> None:
    """Pre-scan fixture for author/actor names and add them to NAME_MAP.

    Also generates "Last, First" variants for email header format.
    """
    if isinstance(data, dict):
        # Check fields that contain person names
        for key in ("author_name", "actor_name", "name", "displayName"):
            if key in data:
                name = data[key]
                if name and isinstance(name, str) and name != "Unknown":
                    # Only treat "name" as a person name if it looks like one
                    # (two+ capitalized words, no file extension)
                    if key == "name":
                        words = name.split()
                        if len(words) < 2 or not all(w[0].isupper() for w in words):
                            continue
                        if "." in words[-1]:  # Likely a filename
                            continue
                    get_sanitized_name(name)
        for v in data.values():
            _collect_author_names(v)
    elif isinstance(data, list):
        for item in data:
            _collect_author_names(item)


def sanitize_fixture(filepath: Path) -> None:
    """Sanitize a single fixture file."""
    print(f"Sanitizing: {filepath.name}")

    with open(filepath) as f:
        data = json.load(f)

    # Reset mappings for consistent results per file
    EMAIL_MAP.clear()
    NAME_MAP.clear()
    TITLE_MAP.clear()

    # Pre-scan for standalone author names and file titles
    _collect_author_names(data)
    _collect_titles(data)

    sanitized = sanitize_value(data)

    with open(filepath, 'w') as f:
        json.dump(sanitized, f, indent=2)

    print(f"  Emails replaced: {len(EMAIL_MAP)}")
    print(f"  Names replaced: {len(NAME_MAP)}")
    if TITLE_MAP:
        print(f"  Titles replaced: {len(TITLE_MAP)}")


def main():
    """Sanitize all fixtures that might contain PII."""
    print("=== Sanitizing Fixtures ===\n")

    # Gmail fixtures definitely need sanitization
    gmail_dir = FIXTURES_DIR / "gmail"
    if gmail_dir.exists():
        for filepath in gmail_dir.glob("*.json"):
            sanitize_fixture(filepath)

    # Activity fixtures contain person names
    activity_dir = FIXTURES_DIR / "activity"
    if activity_dir.exists():
        for filepath in activity_dir.glob("*.json"):
            sanitize_fixture(filepath)

    # Comment fixtures contain author names and emails
    comments_dir = FIXTURES_DIR / "comments"
    if comments_dir.exists():
        for filepath in comments_dir.glob("real_*.json"):
            sanitize_fixture(filepath)

    # Docs/Sheets/Slides might have user info in metadata
    for subdir in ["docs", "sheets", "slides"]:
        dir_path = FIXTURES_DIR / subdir
        if dir_path.exists():
            for filepath in dir_path.glob("real_*.json"):
                sanitize_fixture(filepath)

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
