"""Writing answers back into a test YAML, editing the text so comments and layout survive."""

import json
from pathlib import Path
from typing import Any

import yaml

_ANSWERS_KEY = "answers:"
# Lines that continue a top-level key's value: indented lines and block-sequence items.
_CONTINUATION_PREFIXES = (" ", "\t", "-")


class AnswersNotSavedError(Exception):
    """The file could not be updated without changing more than its answers."""


def save_answers(test_path: Path, answers: list[str]) -> None:
    """Replace (or add) the top-level `answers:` list in `test_path`, leaving the rest as is."""
    original_text = test_path.read_text(encoding="utf-8")
    updated_text = _with_answers_block(original_text, answers)
    expected: dict[str, Any] = {**yaml.safe_load(original_text), "answers": answers}
    if yaml.safe_load(updated_text) != expected:
        raise AnswersNotSavedError(
            f"{test_path}: could not rewrite 'answers:' without changing other fields; "
            f"add these answers by hand: {answers}"
        )
    test_path.write_text(updated_text, encoding="utf-8")


def _with_answers_block(text: str, answers: list[str]) -> str:
    # A JSON string is also a valid double-quoted YAML scalar, so any answer text is safe.
    block = [f"{_ANSWERS_KEY}\n"] + [
        f"  - {json.dumps(answer, ensure_ascii=False)}\n" for answer in answers
    ]
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.startswith(_ANSWERS_KEY)), None)
    if start is None:
        separator = "" if not text or text.endswith("\n") else "\n"
        return text + separator + "".join(block)
    end = start + 1
    while end < len(lines) and lines[end].startswith(_CONTINUATION_PREFIXES):
        end += 1
    return "".join(lines[:start] + block + lines[end:])
