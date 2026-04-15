"""Tests for the Forms extractor."""

import json
from pathlib import Path

from extractors.forms import extract_form_content


FIXTURES = Path(__file__).parent.parent.parent / "fixtures" / "forms"


def test_real_tp360_form():
    """Test extraction from a real TP360 form fixture."""
    data = json.loads((FIXTURES / "tp360_2023.json").read_text())
    md = extract_form_content(data)

    assert md.startswith("# Talking Performance 360 feedback")
    assert "*(required)*" in md
    assert "- [ ] Option 1" in md
    assert "Other…" in md
    assert "*[paragraph]*" in md
    # Last question is not required
    assert "**6. Anything else" in md
    lines = md.split("\n")
    q6_line = next(l for l in lines if "Anything else" in l)
    assert "*(required)*" not in q6_line


def test_choice_question_types():
    """Test radio, checkbox, and dropdown rendering."""
    data = {
        "info": {"title": "Test"},
        "items": [
            {
                "itemId": "1",
                "title": "Pick one",
                "questionItem": {
                    "question": {
                        "questionId": "q1",
                        "choiceQuestion": {
                            "type": "RADIO",
                            "options": [
                                {"value": "Alpha"},
                                {"value": "Beta"},
                            ],
                        },
                    }
                },
            },
            {
                "itemId": "2",
                "title": "Pick many",
                "questionItem": {
                    "question": {
                        "questionId": "q2",
                        "choiceQuestion": {
                            "type": "CHECKBOX",
                            "options": [
                                {"value": "X"},
                                {"value": "Y"},
                            ],
                        },
                    }
                },
            },
            {
                "itemId": "3",
                "title": "Dropdown",
                "questionItem": {
                    "question": {
                        "questionId": "q3",
                        "choiceQuestion": {
                            "type": "DROP_DOWN",
                            "options": [
                                {"value": "A"},
                                {"value": "B"},
                            ],
                        },
                    }
                },
            },
        ],
    }
    md = extract_form_content(data)

    assert "  - Alpha" in md
    assert "  - [ ] X" in md
    assert "*(dropdown)*" in md


def test_scale_question():
    data = {
        "info": {"title": "Test"},
        "items": [
            {
                "itemId": "1",
                "title": "Rate us",
                "questionItem": {
                    "question": {
                        "questionId": "q1",
                        "scaleQuestion": {
                            "low": 1,
                            "high": 10,
                            "lowLabel": "Bad",
                            "highLabel": "Great",
                        },
                    }
                },
            }
        ],
    }
    md = extract_form_content(data)
    assert "1–10" in md
    assert "Bad → Great" in md


def test_grid_question():
    data = {
        "info": {"title": "Test"},
        "items": [
            {
                "itemId": "1",
                "title": "Rate aspects",
                "questionGroupItem": {
                    "grid": {
                        "columns": {
                            "type": "RADIO",
                            "options": [
                                {"value": "Good"},
                                {"value": "OK"},
                                {"value": "Bad"},
                            ],
                        },
                    },
                    "questions": [
                        {"questionId": "r1", "required": True, "rowQuestion": {"title": "Food"}},
                        {"questionId": "r2", "required": True, "rowQuestion": {"title": "Service"}},
                    ],
                },
            }
        ],
    }
    md = extract_form_content(data)
    assert "| Good |" in md
    assert "| Food |" in md
    assert "| Service |" in md


def test_page_break_renders_as_heading():
    data = {
        "info": {"title": "Test"},
        "items": [
            {
                "itemId": "1",
                "title": "Short answer",
                "questionItem": {
                    "question": {
                        "questionId": "q1",
                        "textQuestion": {"paragraph": False},
                    }
                },
            },
            {
                "itemId": "2",
                "title": "Section Two",
                "description": "More questions",
                "pageBreakItem": {},
            },
        ],
    }
    md = extract_form_content(data)
    assert "## Section Two" in md
    assert "More questions" in md
    assert "*[short answer]*" in md


def test_text_item():
    data = {
        "info": {"title": "Test"},
        "items": [
            {
                "itemId": "1",
                "title": "Important Note",
                "description": "Read carefully",
                "textItem": {},
            }
        ],
    }
    md = extract_form_content(data)
    assert "**Important Note**" in md
    assert "Read carefully" in md


def test_quiz_mode():
    data = {
        "info": {"title": "Quiz"},
        "settings": {"quizSettings": {"isQuiz": True}},
        "items": [
            {
                "itemId": "1",
                "title": "What is 2+2?",
                "questionItem": {
                    "question": {
                        "questionId": "q1",
                        "choiceQuestion": {
                            "type": "RADIO",
                            "options": [{"value": "3"}, {"value": "4"}],
                        },
                        "grading": {"pointValue": 5},
                    }
                },
            }
        ],
    }
    md = extract_form_content(data)
    assert "*This form is a quiz" in md
    assert "*(5 points)*" in md


def test_rating_question():
    data = {
        "info": {"title": "Test"},
        "items": [
            {
                "itemId": "1",
                "title": "How was it?",
                "questionItem": {
                    "question": {
                        "questionId": "q1",
                        "ratingQuestion": {
                            "ratingScaleLevel": 5,
                            "iconType": "STAR",
                        },
                    }
                },
            }
        ],
    }
    md = extract_form_content(data)
    assert "*[5-star rating]*" in md


def test_empty_form():
    data = {"info": {"title": "Empty"}, "items": []}
    md = extract_form_content(data)
    assert md.strip() == "# Empty"
