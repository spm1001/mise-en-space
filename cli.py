#!/usr/bin/env python3
"""
CLI interface for mise-en-space.

Usage:
    mise search "query"
    mise fetch <file_id_or_url>
    mise create "Title" --content "markdown"

This provides the same functionality as the MCP tools but via command line,
making it accessible to agents that don't support MCP (like pi).
"""

import argparse
import json
import sys

from tools import do_search, do_fetch, do_create


def cmd_search(args: argparse.Namespace) -> None:
    """Search Drive and Gmail."""
    sources = args.sources if args.sources else None
    result = do_search(args.query, sources, args.max_results)
    print(json.dumps(result.to_dict(), indent=2))


def cmd_fetch(args: argparse.Namespace) -> None:
    """Fetch content to filesystem."""
    result = do_fetch(args.file_id)
    print(json.dumps(result.to_dict(), indent=2))


def cmd_create(args: argparse.Namespace) -> None:
    """Create Google Doc from markdown."""
    content = args.content
    if content is None:
        # Read from stdin if no --content provided
        content = sys.stdin.read()

    result = do_create(content, args.title, args.type, args.folder)
    print(json.dumps(result.to_dict(), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Google Workspace content fetching CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    mise search "quarterly reports"
    mise search "from:alice budget" --sources gmail
    mise fetch 1abc123def456
    mise fetch "https://docs.google.com/document/d/1abc.../edit"
    mise fetch "https://simonwillison.net/2024/Dec/19/one-shot-python-tools/"
    mise create "Meeting Notes" --content "# Meeting Notes\\n\\n- Item 1"
    echo "# Notes" | mise create "Notes"
""",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # search
    search_p = subparsers.add_parser("search", help="Search Drive and Gmail")
    search_p.add_argument("query", help="Search query")
    search_p.add_argument(
        "--sources",
        nargs="+",
        choices=["drive", "gmail"],
        help="Sources to search (default: both)",
    )
    search_p.add_argument(
        "--max-results",
        type=int,
        default=20,
        help="Maximum results per source (default: 20)",
    )
    search_p.set_defaults(func=cmd_search)

    # fetch
    fetch_p = subparsers.add_parser("fetch", help="Fetch content to mise/")
    fetch_p.add_argument(
        "file_id",
        help="Drive file ID, Gmail thread ID, or URL (web, Drive, Gmail)",
    )
    fetch_p.set_defaults(func=cmd_fetch)

    # create
    create_p = subparsers.add_parser("create", help="Create Google Doc from markdown")
    create_p.add_argument("title", help="Document title")
    create_p.add_argument(
        "--content",
        help="Markdown content (or read from stdin)",
    )
    create_p.add_argument(
        "--type",
        choices=["doc", "sheet", "slides"],
        default="doc",
        help="Document type (default: doc)",
    )
    create_p.add_argument(
        "--folder",
        help="Destination folder ID",
    )
    create_p.set_defaults(func=cmd_create)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
