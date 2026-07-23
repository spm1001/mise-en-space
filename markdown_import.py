"""
Markdown preprocessing for Drive's markdown → Google Doc import.

Google's import engine mangles code BLOCKS: every whitespace-delimited token
becomes its own code-styled run (per-word pills in the UI, per-word backtick
spans on re-export). Bare ``` fences, language-tagged fences, ~~~ fences and
4-space indented blocks all mangle identically; inline code spans import as
ONE clean monospace run (probed live 2026-07-23, mise-sejule).

So before import, fenced blocks are rewritten into the form that imports
cleanly: each code line becomes an inline-code span, lines joined with
backslash hard breaks (which import as in-paragraph line breaks, \\x0b) so a
block lands as a single tight paragraph of monospace lines. Blank lines
within a block split it into sibling paragraphs. Lines containing a backtick
are wrapped ``like this`` (double-delimiter with space padding, CommonMark's
own escape — verified to import as one run with the literal backtick inside).

Indented (4-space) code blocks are left alone: reliably distinguishing them
from lazy list continuations needs full CommonMark context, and fences are
what real callers write.
"""

import re

# Opening fence: optional indent, ``` or ~~~ (3+), optional info string.
# CommonMark forbids backticks in the info string of a backtick fence —
# that's what keeps `` `inline` `` usage from matching here.
_FENCE_OPEN_RE = re.compile(r"^(\s*)(`{3,}|~{3,})[ \t]*([^`\n]*)$")


def _wrap_line(line: str) -> str:
    """Wrap one code line as an inline-code span that survives import."""
    if "`" not in line:
        return f"`{line}`"
    # CommonMark escape for backtick-bearing content: a delimiter run longer
    # than any run inside, with space padding.
    longest = max(len(m.group(0)) for m in re.finditer(r"`+", line))
    delim = "`" * (longest + 1)
    return f"{delim} {line} {delim}"


def _render_block(code_lines: list[str]) -> list[str]:
    """Render a fenced block's lines as paragraphs of hard-broken code spans."""
    # Split into groups at blank lines — each group becomes one paragraph.
    groups: list[list[str]] = [[]]
    for line in code_lines:
        if line.strip():
            groups[-1].append(line)
        elif groups[-1]:
            groups.append([])
    groups = [g for g in groups if g]

    out: list[str] = []
    for i, group in enumerate(groups):
        if i:
            out.append("")
        wrapped = [_wrap_line(line) for line in group]
        # Backslash hard break on every line but the last keeps the group as
        # one paragraph with in-paragraph line breaks after import.
        out.extend(w + "\\" for w in wrapped[:-1])
        out.append(wrapped[-1])
    return out


def convert_fenced_blocks(markdown: str) -> str:
    """
    Rewrite fenced code blocks into per-line inline-code spans.

    Idempotent on markdown without fences; the output contains no fences, so
    a second pass is a no-op. An unclosed fence runs to end of input
    (CommonMark semantics).
    """
    if "```" not in markdown and "~~~" not in markdown:
        return markdown

    lines = markdown.split("\n")
    out: list[str] = []
    in_block = False
    close_re: re.Pattern[str] | None = None
    code_lines: list[str] = []

    for line in lines:
        if not in_block:
            m = _FENCE_OPEN_RE.match(line)
            # A backtick fence may not have an info string containing "`";
            # tilde fences allow anything. Both are satisfied by the regex.
            if m:
                in_block = True
                fence = m.group(2)
                close_re = re.compile(rf"^\s*{re.escape(fence[0])}{{{len(fence)},}}\s*$")
                code_lines = []
            else:
                out.append(line)
        else:
            if close_re and close_re.match(line):
                in_block = False
                # Blank padding where the fences were — otherwise the first
                # code span soft-wraps into a preceding paragraph line.
                out.append("")
                out.extend(_render_block(code_lines))
                out.append("")
            else:
                code_lines.append(line)

    if in_block:  # unclosed fence — treat collected lines as the block
        out.append("")
        out.extend(_render_block(code_lines))

    return "\n".join(out)
