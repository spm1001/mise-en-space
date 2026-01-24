"""
Security regression tests.

These tests verify that security patterns are maintained as the codebase evolves.
They scan source files for potential vulnerabilities.
"""

import re
from pathlib import Path

# Project root
PROJECT_ROOT = Path(__file__).parent.parent.parent


class TestQueryEscapingUsage:
    """Verify that query escaping is used where needed."""

    def test_search_tool_uses_drive_escaping(self):
        """search.py must import and use escape_drive_query."""
        search_py = PROJECT_ROOT / "tools" / "search.py"
        content = search_py.read_text()

        # Must import the function
        assert "from validation import" in content
        assert "escape_drive_query" in content, (
            "search.py must import escape_drive_query from validation"
        )

        # Must actually use it before constructing Drive queries
        assert "escape_drive_query(query)" in content or "escape_drive_query(q" in content, (
            "search.py must call escape_drive_query() on user input"
        )

    def test_search_tool_uses_gmail_sanitization(self):
        """search.py must import and use sanitize_gmail_query."""
        search_py = PROJECT_ROOT / "tools" / "search.py"
        content = search_py.read_text()

        assert "sanitize_gmail_query" in content, (
            "search.py must import sanitize_gmail_query from validation"
        )

        assert "sanitize_gmail_query(query)" in content or "sanitize_gmail_query(q" in content, (
            "search.py must call sanitize_gmail_query() on user input"
        )


class TestNoRawQueryInterpolation:
    """
    Scan for dangerous patterns where user input might be interpolated into queries.

    This is a heuristic check - it may have false positives that need to be
    allowlisted, but it catches common mistakes.
    """

    # Pattern: f-string with single quotes around a variable
    # e.g., f"fullText contains '{query}'" is dangerous
    # But f"fullText contains '{escaped_query}'" after escaping is safe
    DANGEROUS_FSTRING_PATTERN = re.compile(
        r'''f["'].*\{(?!escaped_|sanitized_)(\w+)\}.*["']''',
        re.MULTILINE
    )

    # Files to scan (relative to project root)
    FILES_TO_SCAN = [
        "tools/search.py",
        "tools/fetch.py",
        "tools/create.py",
        "adapters/drive.py",
        "adapters/gmail.py",
    ]

    # Known safe patterns (variable names that are OK to interpolate)
    SAFE_VARIABLES = {
        # These come from API responses, not user input
        "name", "title", "file_id", "thread_id", "mime_type",
        "e", "str(e)",  # Exception messages
        "doc_type",  # Validated against allowlist
        "result",  # API response
        # Internal/computed values
        "temp_name", "temp_id", "char_count", "min_chars_threshold",
    }

    def test_no_raw_query_interpolation_in_search(self):
        """
        search.py should not interpolate raw 'query' variable into f-strings.

        The variable should be named 'escaped_query' or 'sanitized_query' after
        passing through the escaping functions.
        """
        search_py = PROJECT_ROOT / "tools" / "search.py"
        content = search_py.read_text()

        # Find all f-strings that interpolate variables
        # Look specifically for patterns like '{query}' (raw) vs '{escaped_query}' (safe)
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            # Skip comments
            if line.strip().startswith('#'):
                continue

            # Check for dangerous pattern: interpolating 'query' directly
            if "'{query}'" in line or '"{query}"' in line:
                raise AssertionError(
                    f"tools/search.py:{i} interpolates raw 'query' variable. "
                    f"Use escape_drive_query() or sanitize_gmail_query() first.\n"
                    f"Line: {line.strip()}"
                )

    def test_scan_for_suspicious_query_patterns(self):
        """
        Scan key files for patterns that might indicate unescaped query construction.

        This is a broader heuristic check. If it flags something that's actually
        safe, add the variable name to SAFE_VARIABLES.
        """
        issues = []

        for file_path in self.FILES_TO_SCAN:
            full_path = PROJECT_ROOT / file_path
            if not full_path.exists():
                continue

            content = full_path.read_text()
            lines = content.split('\n')

            for i, line in enumerate(lines, 1):
                # Skip comments and empty lines
                stripped = line.strip()
                if not stripped or stripped.startswith('#'):
                    continue

                # Look for f-strings containing "contains '" which is Drive query syntax
                if "contains '" in line and "f\"" in line or "f'" in line:
                    # Check if the variable being interpolated is safe
                    match = re.search(r"\{(\w+)\}", line)
                    if match:
                        var_name = match.group(1)
                        if var_name not in self.SAFE_VARIABLES and not var_name.startswith(('escaped_', 'sanitized_')):
                            issues.append(
                                f"{file_path}:{i} - Suspicious query interpolation with '{var_name}':\n"
                                f"  {stripped}"
                            )

        if issues:
            raise AssertionError(
                "Found potentially unsafe query interpolation:\n\n" +
                "\n\n".join(issues) +
                "\n\nIf these are safe, add the variable name to SAFE_VARIABLES in test_security.py"
            )
