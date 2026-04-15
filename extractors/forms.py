"""
Forms extractor — pure function converting Forms API JSON to markdown.

No I/O, no API calls. Takes the dict from forms.get and returns readable markdown.
"""

from typing import Any


def extract_form_content(form_data: dict[str, Any]) -> str:
    """Convert Forms API response to markdown."""
    lines: list[str] = []
    info = form_data.get("info", {})

    lines.append(f"# {info.get('title', 'Untitled Form')}")
    if info.get("description"):
        lines.append("")
        lines.append(info["description"])

    settings = form_data.get("settings", {})
    is_quiz = settings.get("quizSettings", {}).get("isQuiz", False)
    if is_quiz:
        lines.append("")
        lines.append("*This form is a quiz with scored answers.*")

    question_number = 0
    for item in form_data.get("items", []):
        lines.append("")
        question_number = _render_item(lines, item, question_number, is_quiz)

    lines.append("")
    return "\n".join(lines)


def _render_item(
    lines: list[str],
    item: dict[str, Any],
    question_number: int,
    is_quiz: bool,
) -> int:
    """Render a single form item. Returns updated question number."""
    if "pageBreakItem" in item:
        lines.append(f"## {item.get('title', 'Section')}")
        if item.get("description"):
            lines.append("")
            lines.append(item["description"])
        return question_number

    if "textItem" in item:
        if item.get("title"):
            lines.append(f"**{item['title']}**")
        if item.get("description"):
            lines.append("")
            lines.append(item["description"])
        return question_number

    if "imageItem" in item:
        alt = item.get("imageItem", {}).get("image", {}).get("altText", "image")
        lines.append(f"*[Image: {alt}]*")
        return question_number

    if "videoItem" in item:
        caption = item.get("videoItem", {}).get("caption", "video")
        lines.append(f"*[Video: {caption}]*")
        return question_number

    if "questionGroupItem" in item:
        question_number += 1
        _render_grid(lines, item, question_number, is_quiz)
        return question_number

    if "questionItem" in item:
        question_number += 1
        _render_question(lines, item, question_number, is_quiz)
        return question_number

    return question_number


def _render_question(
    lines: list[str],
    item: dict[str, Any],
    number: int,
    is_quiz: bool,
) -> None:
    """Render a single-question item."""
    question = item["questionItem"]["question"]
    required = " *(required)*" if question.get("required") else ""
    lines.append(f"**{number}. {item.get('title', 'Question')}**{required}")

    if item.get("description"):
        lines.append("")
        lines.append(item["description"])

    _render_question_body(lines, question, is_quiz)


def _render_question_body(
    lines: list[str],
    question: dict[str, Any],
    is_quiz: bool,
) -> None:
    """Render the question type details."""
    if "choiceQuestion" in question:
        cq = question["choiceQuestion"]
        choice_type = cq.get("type", "RADIO")
        marker = "- [ ]" if choice_type == "CHECKBOX" else "-"
        lines.append("")
        for opt in cq.get("options", []):
            label = opt.get("value", "")
            if opt.get("isOther"):
                label = "Other…"
            suffix = ""
            if opt.get("goToAction"):
                suffix = f" → {opt['goToAction'].replace('_', ' ').lower()}"
            elif opt.get("goToSectionId"):
                suffix = f" → section {opt['goToSectionId']}"
            lines.append(f"  {marker} {label}{suffix}")
        if choice_type == "DROP_DOWN":
            lines.append("")
            lines.append("  *(dropdown)*")

    elif "textQuestion" in question:
        kind = "paragraph" if question["textQuestion"].get("paragraph") else "short answer"
        lines.append("")
        lines.append(f"  *[{kind}]*")

    elif "scaleQuestion" in question:
        sq = question["scaleQuestion"]
        low_label = sq.get("lowLabel", str(sq.get("low", 1)))
        high_label = sq.get("highLabel", str(sq.get("high", 5)))
        lines.append("")
        lines.append(f"  *[{sq.get('low', 1)}–{sq.get('high', 5)}: {low_label} → {high_label}]*")

    elif "dateQuestion" in question:
        dq = question["dateQuestion"]
        parts = []
        if dq.get("includeYear"):
            parts.append("year")
        if dq.get("includeTime"):
            parts.append("time")
        qualifier = f" (includes {', '.join(parts)})" if parts else ""
        lines.append("")
        lines.append(f"  *[date{qualifier}]*")

    elif "timeQuestion" in question:
        kind = "duration" if question["timeQuestion"].get("duration") else "time"
        lines.append("")
        lines.append(f"  *[{kind}]*")

    elif "ratingQuestion" in question:
        rq = question["ratingQuestion"]
        icon = rq.get("iconType", "STAR").lower()
        level = rq.get("ratingScaleLevel", 5)
        lines.append("")
        lines.append(f"  *[{level}-{icon} rating]*")

    elif "fileUploadQuestion" in question:
        fu = question["fileUploadQuestion"]
        types = ", ".join(t.lower() for t in fu.get("types", ["any"]))
        max_files = fu.get("maxFiles", 1)
        lines.append("")
        lines.append(f"  *[file upload: {types}, max {max_files}]*")

    if is_quiz and "grading" in question:
        grading = question["grading"]
        points = grading.get("pointValue", 0)
        lines.append(f"  *({points} points)*")


def _render_grid(
    lines: list[str],
    item: dict[str, Any],
    number: int,
    is_quiz: bool,
) -> None:
    """Render a grid/checkbox-grid question as a markdown table."""
    group = item["questionGroupItem"]
    grid = group.get("grid", {})
    columns = grid.get("columns", {})
    col_options = [opt.get("value", "") for opt in columns.get("options", [])]
    questions = group.get("questions", [])
    grid_type = columns.get("type", "RADIO")

    any_required = any(q.get("required") for q in questions)
    required = " *(required)*" if any_required else ""
    lines.append(f"**{number}. {item.get('title', 'Grid')}**{required}")

    if item.get("description"):
        lines.append("")
        lines.append(item["description"])

    if grid_type == "CHECKBOX":
        lines.append("")
        lines.append("*(select all that apply per row)*")

    # Table header
    lines.append("")
    header = "| |" + "|".join(f" {c} " for c in col_options) + "|"
    separator = "|---|" + "|".join("---" for _ in col_options) + "|"
    lines.append(header)
    lines.append(separator)

    for q in questions:
        row_title = q.get("rowQuestion", {}).get("title", "")
        cells = "|".join(" " for _ in col_options)
        lines.append(f"| {row_title} |{cells}|")
