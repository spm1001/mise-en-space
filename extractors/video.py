"""
Video content extractor — pure markdown assembly from video metadata.

Receives summary data and metadata, returns formatted markdown.
No API calls, no system checks, no MCP awareness.
"""

from __future__ import annotations


def extract_video_content(
    title: str,
    *,
    summary: str | None = None,
    transcript_snippets: list[str] | None = None,
    summary_error: str | None = None,
    has_summary: bool = False,
    mime_type: str = "",
    duration_ms: int | str | None = None,
    web_view_link: str = "",
    cdp_available: bool = True,
) -> str:
    """
    Assemble markdown content for a video/audio file.

    Args:
        title: Video title
        summary: AI-generated summary text (from GenAI API)
        transcript_snippets: Transcript excerpt lines
        summary_error: Error string if summary failed ("stale_cookies", "permission_denied")
        has_summary: Whether the summary has actual content
        mime_type: Video MIME type (e.g., "video/mp4")
        duration_ms: Duration in milliseconds (int or string from API)
        web_view_link: Drive web view URL
        cdp_available: Whether chrome-debug is running (for tip text)

    Returns:
        Formatted markdown string
    """
    lines: list[str] = [f"# {title}", ""]

    # Summary section
    if has_summary:
        lines.append("## AI Summary")
        lines.append("")
        if summary:
            lines.append(summary)
            lines.append("")
        if transcript_snippets:
            lines.append("## Transcript Snippets")
            lines.append("")
            for snippet in transcript_snippets:
                lines.append(f"- {snippet}")
            lines.append("")
    elif summary_error == "stale_cookies":
        lines.append("*AI summary unavailable — browser session expired.*")
        lines.append("")
        lines.append(
            "_Tip: Refresh your Google session in chrome-debug, then retry._"
        )
        lines.append("")
    elif summary_error == "permission_denied":
        lines.append("*AI summary unavailable — no access to this video.*")
        lines.append("")
    else:
        lines.append("*No AI summary available.*")
        lines.append("")
        if not cdp_available:
            lines.append(
                "_Tip: Run `chrome-debug` to enable AI summaries for videos._"
            )
        lines.append("")

    # Metadata section
    lines.append("## Metadata")
    lines.append("")
    lines.append(f"- **Type:** {mime_type}")
    if duration_ms:
        lines.append(f"- **Duration:** {format_duration(int(duration_ms))}")
    lines.append(f"- **Link:** {web_view_link}")

    return "\n".join(lines)


def format_duration(duration_ms: int) -> str:
    """Format milliseconds as human-readable duration (e.g., '5:30' or '1:05:30')."""
    duration_s = duration_ms // 1000
    minutes, seconds = divmod(duration_s, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"
