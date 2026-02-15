"""
Web Content Extractor — Pure functions for converting HTML to markdown.

Uses trafilatura for main content extraction with fallbacks for edge cases.
Code blocks are preserved via pre-processing (trafilatura mangles language hints).

No I/O, no API calls — receives WebData, returns content string.
"""

import re
import uuid
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser

import trafilatura

from models import WebData

__all__ = [
    "extract_web_content",
    "extract_title",
    "EXTRACTION_FAILED_CUE",
]

# Cue marker written into stub content when all extraction methods fail.
# tools/fetch/web.py matches this to set cues['extraction_failed'].
# Shared constant so both layers stay in sync.
EXTRACTION_FAILED_CUE = "*Content extraction failed for"


# ============================================================================
# CODE BLOCK PRESERVATION
# ============================================================================
# trafilatura loses language hints from code blocks. We extract them first,
# replace with placeholders, then restore after trafilatura does its thing.

@dataclass
class CodeBlock:
    """A preserved code block with its language hint."""
    language: str  # e.g., "python", "javascript", "" for unknown
    content: str   # The actual code
    placeholder: str  # Unique ID for replacement


# Patterns for finding code blocks with language hints
# Matches: <pre><code class="language-python">, <code class="lang-js">,
#          <pre data-lang="rust">, <pre class="highlight python">, etc.
CODE_BLOCK_PATTERN = re.compile(
    r'<pre[^>]*>(?:\s*<code[^>]*>)?(.*?)(?:</code>\s*)?</pre>',
    re.IGNORECASE | re.DOTALL
)

# Patterns for extracting language from attributes
LANG_PATTERNS = [
    re.compile(r'class="[^"]*(?:language-|lang-)(\w+)', re.IGNORECASE),
    re.compile(r'data-lang="(\w+)"', re.IGNORECASE),
    re.compile(r'class="[^"]*highlight\s+(\w+)', re.IGNORECASE),  # highlight.js
    re.compile(r'class="[^"]*brush:\s*(\w+)', re.IGNORECASE),     # SyntaxHighlighter
    re.compile(r'class="[^"]*prettyprint\s+lang-(\w+)', re.IGNORECASE),  # Google prettify
]


def _extract_language_from_tag(tag_html: str) -> str:
    """
    Extract language hint from a pre/code tag's attributes.

    Args:
        tag_html: The opening tag(s), e.g., '<pre><code class="language-python">'

    Returns:
        Language name (lowercase) or empty string if not found
    """
    for pattern in LANG_PATTERNS:
        match = pattern.search(tag_html)
        if match:
            return match.group(1).lower()
    return ""


def _decode_html_entities(text: str) -> str:
    """Decode HTML entities in code content."""
    # Common entities in code blocks
    text = text.replace('&lt;', '<')
    text = text.replace('&gt;', '>')
    text = text.replace('&amp;', '&')
    text = text.replace('&quot;', '"')
    text = text.replace('&#39;', "'")
    text = text.replace('&nbsp;', ' ')
    return unescape(text)  # Catch any remaining entities


def _strip_html_tags(text: str) -> str:
    """Remove HTML tags from code content (e.g., <span class="keyword">)."""
    return re.sub(r'<[^>]+>', '', text)


def _preserve_code_blocks(html: str) -> tuple[str, list[CodeBlock]]:
    """
    Extract code blocks before trafilatura mangles them.

    Replaces <pre>...</pre> blocks with unique placeholders.
    trafilatura will preserve the placeholder text, then we restore.

    Args:
        html: Raw HTML

    Returns:
        Tuple of (modified HTML with placeholders, list of CodeBlock objects)
    """
    blocks: list[CodeBlock] = []

    def replace_block(match: re.Match[str]) -> str:
        full_match = match.group(0)
        code_content = match.group(1)

        # Extract language from the opening tags
        # Get everything before the content
        pre_start = full_match.find('>') + 1
        opening_tags = full_match[:pre_start]
        # Also check for <code> tag inside
        code_tag_match = re.search(r'<code[^>]*>', full_match[:200], re.IGNORECASE)
        if code_tag_match:
            opening_tags += code_tag_match.group(0)

        language = _extract_language_from_tag(opening_tags)

        # Clean the code content
        code_content = _strip_html_tags(code_content)
        code_content = _decode_html_entities(code_content)
        code_content = code_content.strip()

        # Skip empty blocks
        if not code_content:
            return full_match

        # Create unique placeholder that trafilatura won't mangle
        # Use a format that looks like plain text content
        placeholder_id = f"CODEBLOCK_{uuid.uuid4().hex[:12]}"

        block = CodeBlock(
            language=language,
            content=code_content,
            placeholder=placeholder_id
        )
        blocks.append(block)

        # Replace with a paragraph containing the placeholder
        # trafilatura preserves paragraph text
        return f'<p>{placeholder_id}</p>'

    modified_html = CODE_BLOCK_PATTERN.sub(replace_block, html)
    return modified_html, blocks


def _restore_code_blocks(content: str, blocks: list[CodeBlock]) -> str:
    """
    Restore code blocks in extracted content.

    Replaces placeholders with proper markdown code fences.

    Args:
        content: Extracted markdown with placeholders
        blocks: List of CodeBlock objects from preservation step

    Returns:
        Content with code blocks restored as ```lang ... ```
    """
    for block in blocks:
        # Build the markdown code fence
        fence_open = f"```{block.language}" if block.language else "```"
        code_md = f"{fence_open}\n{block.content}\n```"

        # Replace placeholder (might be on its own line or inline)
        # Handle both cases: standalone paragraph or inline mention
        content = re.sub(
            rf'^{re.escape(block.placeholder)}$',
            code_md,
            content,
            flags=re.MULTILINE
        )
        # Also handle inline case (less common)
        content = content.replace(block.placeholder, code_md)

    return content


class TitleExtractor(HTMLParser):
    """Simple HTML parser to extract <title> content."""

    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data


def extract_title(html: str) -> str:
    """
    Extract page title from HTML.

    Args:
        html: Raw HTML content

    Returns:
        Page title or empty string if not found
    """
    try:
        parser = TitleExtractor()
        parser.feed(html)
        title = parser.title.strip()
        # Clean up common title patterns
        title = re.sub(r'\s+', ' ', title)
        return unescape(title)
    except Exception:
        return ""


def _extract_with_trafilatura(html: str, url: str) -> str | None:
    """
    Extract content using trafilatura.

    Args:
        html: Raw HTML
        url: Original URL (for resolving relative links)

    Returns:
        Markdown content or None if extraction failed
    """
    content = trafilatura.extract(
        html,
        include_links=True,
        include_images=True,
        include_tables=True,
        output_format='markdown',
        favor_precision=False,  # Recall > precision for agents
        url=url,  # For resolving relative links
    )

    return content


def _fallback_extract(html: str) -> str:
    """
    Fallback extraction when trafilatura returns nothing.

    Tries plain text extraction with maximum recall.
    """
    content = trafilatura.extract(
        html,
        output_format='txt',
        favor_recall=True,
        include_links=False,  # Links don't work in plain text
        include_tables=False,  # Tables need markdown
    )

    return content or ""


# ============================================================================
# RAW TEXT HANDLING
# ============================================================================
# Some URLs return raw text (not HTML). trafilatura can't handle these,
# so we detect and pass through directly.

RAW_TEXT_TYPES = {
    'text/plain',
    'text/markdown',
    'text/x-markdown',
    'application/json',
    'application/xml',
    'text/xml',
    'text/csv',
    'text/yaml',
    'application/x-yaml',
    'text/x-python',
    'text/x-java',
    'text/x-c',
    'text/x-script.python',
    'application/javascript',
    'text/javascript',
}

# File extensions that indicate raw text (when Content-Type is generic)
RAW_TEXT_EXTENSIONS = {
    '.md', '.markdown', '.txt', '.json', '.xml', '.yaml', '.yml',
    '.py', '.js', '.ts', '.java', '.c', '.cpp', '.h', '.go', '.rs',
    '.rb', '.php', '.sh', '.bash', '.zsh', '.fish',
    '.css', '.scss', '.less', '.sql', '.graphql',
    '.toml', '.ini', '.cfg', '.conf', '.env',
}

# Language hints for code fencing based on extension
EXTENSION_LANGUAGES = {
    '.py': 'python',
    '.js': 'javascript',
    '.ts': 'typescript',
    '.java': 'java',
    '.c': 'c',
    '.cpp': 'cpp',
    '.h': 'c',
    '.go': 'go',
    '.rs': 'rust',
    '.rb': 'ruby',
    '.php': 'php',
    '.sh': 'bash',
    '.bash': 'bash',
    '.zsh': 'zsh',
    '.sql': 'sql',
    '.json': 'json',
    '.xml': 'xml',
    '.yaml': 'yaml',
    '.yml': 'yaml',
    '.toml': 'toml',
    '.md': 'markdown',
    '.markdown': 'markdown',
    '.css': 'css',
    '.scss': 'scss',
    '.graphql': 'graphql',
}


def _is_raw_text(content_type: str, url: str) -> bool:
    """
    Check if content should be treated as raw text (not HTML).

    Args:
        content_type: HTTP Content-Type header
        url: URL for extension-based detection

    Returns:
        True if content is raw text
    """
    # Check Content-Type
    ct_lower = content_type.lower().split(';')[0].strip()
    if ct_lower in RAW_TEXT_TYPES:
        return True

    # Check URL extension
    from urllib.parse import urlparse
    path = urlparse(url).path.lower()
    for ext in RAW_TEXT_EXTENSIONS:
        if path.endswith(ext):
            return True

    return False


def _get_language_from_url(url: str) -> str:
    """Get code fence language hint from URL extension."""
    from urllib.parse import urlparse
    path = urlparse(url).path.lower()
    for ext, lang in EXTENSION_LANGUAGES.items():
        if path.endswith(ext):
            return lang
    return ""


def _format_raw_text(content: str, url: str, content_type: str) -> str:
    """
    Format raw text content for deposit.

    For code files, wraps in a code fence with language hint.
    For markdown, returns as-is.
    For other text, returns with minimal formatting.
    """
    from urllib.parse import urlparse
    path = urlparse(url).path

    # Extract filename for title
    filename = path.split('/')[-1] if '/' in path else path
    if not filename:
        filename = "raw-content"

    # Markdown files: return as-is (they're already markdown)
    if path.endswith(('.md', '.markdown')):
        # Add filename as title if content doesn't start with heading
        if not content.strip().startswith('#'):
            return f"# {filename}\n\n{content}"
        return content

    # JSON: pretty-print if compact
    if content_type.lower().startswith('application/json') or path.endswith('.json'):
        import json
        try:
            # Try to pretty-print if it's valid JSON
            parsed = json.loads(content)
            content = json.dumps(parsed, indent=2)
        except json.JSONDecodeError:
            pass  # Keep original if not valid JSON
        return f"# {filename}\n\n```json\n{content}\n```"

    # Code files: wrap in fence with language
    lang = _get_language_from_url(url)
    if lang:
        return f"# {filename}\n\n```{lang}\n{content}\n```"

    # Plain text: return with title
    return f"# {filename}\n\n{content}"


def extract_web_content(data: WebData) -> str:
    """
    Extract clean markdown from web page HTML or raw text.

    Pure function: receives WebData, returns content string.
    Populates data.warnings with any extraction issues.

    For raw text (markdown, JSON, code files), returns content directly.
    For HTML, uses trafilatura with code block preservation.

    Args:
        data: WebData from adapter with HTML content

    Returns:
        Clean markdown content ready for deposit
    """
    html = data.html
    url = data.url
    content_type = data.content_type

    # Handle raw text content (not HTML)
    if _is_raw_text(content_type, url):
        data.warnings.append(f"Raw text content ({content_type.split(';')[0]})")
        return _format_raw_text(html, url, content_type)

    # Pre-process: extract code blocks before trafilatura mangles them
    html_with_placeholders, code_blocks = _preserve_code_blocks(html)

    if code_blocks:
        data.warnings.append(f"Preserved {len(code_blocks)} code blocks")

    # Primary extraction with trafilatura (on modified HTML)
    content = _extract_with_trafilatura(html_with_placeholders, url)

    if not content or len(content.strip()) < 50:
        data.warnings.append("trafilatura returned minimal content, trying fallback")
        fallback_content = _fallback_extract(html_with_placeholders)

        if fallback_content and len(fallback_content) > len(content or ""):
            content = fallback_content
            data.warnings.append("Using plain text fallback extraction")

    if not content:
        data.warnings.append("All extraction methods returned empty content")
        # Return a minimal document with metadata
        title = extract_title(html)
        return f"# {title or 'Untitled'}\n\n{EXTRACTION_FAILED_CUE} {url}*\n"

    # Post-process: restore code blocks with proper markdown fencing
    if code_blocks:
        content = _restore_code_blocks(content, code_blocks)

    # Add title header if not already present
    title = extract_title(html)
    if title and not content.startswith('#'):
        content = f"# {title}\n\n{content}"

    return content
