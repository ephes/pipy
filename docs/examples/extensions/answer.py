"""Q&A extraction extension — a Python port of Pi's `answer.ts`.

Copy this file to `<workspace>/.pipy/extensions/answer.py` (or the global
extension dir). The `/answer` command (and the `ctrl+.` shortcut) take the last
assistant message, ask the model to extract the open questions as JSON, then
open an interactive Q&A overlay to answer them; submitting sends the compiled
answers back as a new turn.

This mirrors the "prompt generator with custom TUI" pattern of the TypeScript
original using pipy's extension API:

1. `/answer` reads `ctx.conversation.last_assistant_message()`.
2. `ctx.complete(SYSTEM_PROMPT, text)` extracts questions as structured JSON.
3. `ctx.ui.custom(...)` runs the `QnAComponent` overlay (navigate / type /
   confirm).
4. `api.send_user_message(...)` submits the compiled answers and triggers a
   turn.

Notes on pipy adaptations: extraction uses the session's active provider (a
synchronous `ctx.complete`) rather than Pi's model-registry preference list, so
a static "Extracting…" notice replaces Pi's animated loader. Answers are
single-line (Pi's Shift+Enter newline needs a kitty-protocol terminal); the
`ctrl+.` shortcut likewise only fires on terminals that decode it (the `/answer`
command always works).
"""

from __future__ import annotations

import json
import re

from pipy_harness.extensions import ExtensionCapabilityError

SYSTEM_PROMPT = """You are a question extractor. Given text from a conversation, extract any questions that need answering.

Output a JSON object with this structure:
{
  "questions": [
    {
      "question": "The question text",
      "context": "Optional context that helps answer the question"
    }
  ]
}

Rules:
- Extract all questions that require user input
- Keep questions in the order they appeared
- Be concise with question text
- Include context only when it provides essential information for answering
- If no questions are found, return {"questions": []}"""


# -- ANSI styling (the component owns its own styling; trusted local code) ----

def _dim(s: str) -> str:
    return f"\x1b[2m{s}\x1b[0m"


def _bold(s: str) -> str:
    return f"\x1b[1m{s}\x1b[0m"


def _cyan(s: str) -> str:
    return f"\x1b[36m{s}\x1b[0m"


def _green(s: str) -> str:
    return f"\x1b[32m{s}\x1b[0m"


def _yellow(s: str) -> str:
    return f"\x1b[33m{s}\x1b[0m"


def _gray(s: str) -> str:
    return f"\x1b[90m{s}\x1b[0m"


def parse_extraction_result(text: str) -> list[dict] | None:
    """Parse the model's JSON response into a list of question dicts.

    Tolerates a ```json fenced block. Returns the questions list (possibly
    empty), or ``None`` when the text is not the expected shape.
    """

    json_str = text
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        json_str = match.group(1).strip()
    try:
        parsed = json.loads(json_str)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    questions = parsed.get("questions")
    if not isinstance(questions, list):
        return None
    clean: list[dict] = []
    for item in questions:
        if isinstance(item, dict) and isinstance(item.get("question"), str):
            entry: dict = {"question": item["question"]}
            context = item.get("context")
            if isinstance(context, str) and context:
                entry["context"] = context
            clean.append(entry)
    return clean


class QnAComponent:
    """Interactive Q&A overlay: navigate questions and type single-line answers.

    Key handling mirrors `answer.ts`: Tab / Enter advance (Enter on the last
    question asks to confirm), Shift+Tab goes back, Up/Down navigate when the
    answer is empty, Backspace edits, Esc cancels. On submit the answers are
    compiled into the same `Q:/>/A:` block the TypeScript version produces.
    """

    def __init__(self, questions: list[dict], done) -> None:
        self._questions = questions
        self._answers = ["" for _ in questions]
        self._index = 0
        self._done = done
        self._confirming = False

    # -- helpers ----------------------------------------------------------

    def _save(self) -> None:
        self._answers[self._index] = self._buffer

    @property
    def _buffer(self) -> str:
        return self._answers[self._index]

    @_buffer.setter
    def _buffer(self, value: str) -> None:
        self._answers[self._index] = value

    def _navigate_to(self, index: int) -> None:
        if 0 <= index < len(self._questions):
            self._index = index

    def _compile(self) -> str:
        parts: list[str] = []
        for question, answer in zip(self._questions, self._answers, strict=True):
            parts.append(f"Q: {question['question']}")
            if question.get("context"):
                parts.append(f"> {question['context']}")
            parts.append(f"A: {answer.strip() or '(no answer)'}")
            parts.append("")
        return "\n".join(parts).strip()

    # -- input ------------------------------------------------------------

    def handle_input(self, key: str) -> None:
        if self._confirming:
            if key in ("enter", "y"):
                self._done(self._compile())
            elif key in ("esc", "ctrl-c", "n"):
                self._confirming = False
            return

        if key in ("esc", "ctrl-c"):
            self._done(None)
            return
        if key == "tab":
            self._navigate_to(self._index + 1)
            return
        if key == "shift-tab":
            self._navigate_to(self._index - 1)
            return
        if key == "up" and self._buffer == "":
            self._navigate_to(self._index - 1)
            return
        if key == "down" and self._buffer == "":
            self._navigate_to(self._index + 1)
            return
        if key == "enter":
            if self._index < len(self._questions) - 1:
                self._navigate_to(self._index + 1)
            else:
                self._confirming = True
            return
        if key == "backspace":
            self._buffer = self._buffer[:-1]
            return
        if len(key) == 1 and key.isprintable():
            self._buffer += key

    # -- render -----------------------------------------------------------

    def render(self, width: int) -> list[str]:
        box_width = min(width - 4, 120)
        content_width = box_width - 4
        lines: list[str] = []

        def pad(line: str) -> str:
            return line + " " * max(0, width - _visible_len(line))

        def box_line(content: str, left_pad: int = 2) -> str:
            padded = " " * left_pad + content
            right = max(0, box_width - _visible_len(padded) - 2)
            return _dim("│") + padded + " " * right + _dim("│")

        def empty_box() -> str:
            return _dim("│") + " " * (box_width - 2) + _dim("│")

        def hline(left: str, right: str) -> str:
            return _dim(left + "─" * (box_width - 2) + right)

        lines.append(pad(hline("╭", "╮")))
        title = (
            f"{_bold(_cyan('Questions'))} "
            f"{_dim(f'({self._index + 1}/{len(self._questions)})')}"
        )
        lines.append(pad(box_line(title)))
        lines.append(pad(hline("├", "┤")))

        dots: list[str] = []
        for i in range(len(self._questions)):
            if i == self._index:
                dots.append(_cyan("●"))
            elif self._answers[i].strip():
                dots.append(_green("●"))
            else:
                dots.append(_dim("○"))
        lines.append(pad(box_line(" ".join(dots))))
        lines.append(pad(empty_box()))

        question = self._questions[self._index]
        for chunk in _wrap(f"{_bold('Q:')} {question['question']}", content_width):
            lines.append(pad(box_line(chunk)))
        if question.get("context"):
            lines.append(pad(empty_box()))
            for chunk in _wrap(_gray(f"> {question['context']}"), content_width - 2):
                lines.append(pad(box_line(chunk)))

        lines.append(pad(empty_box()))
        answer = self._buffer
        lines.append(pad(box_line(f"{_bold('A:')} {answer}▏")))
        lines.append(pad(empty_box()))

        lines.append(pad(hline("├", "┤")))
        if self._confirming:
            msg = (
                f"{_yellow('Submit all answers?')} "
                f"{_dim('(Enter/y to confirm, Esc/n to cancel)')}"
            )
            lines.append(pad(box_line(_truncate(msg, content_width))))
        else:
            controls = (
                f"{_dim('Tab/Enter')} next · {_dim('Shift+Tab')} prev · "
                f"{_dim('Esc')} cancel"
            )
            lines.append(pad(box_line(_truncate(controls, content_width))))
        lines.append(pad(hline("╰", "╯")))
        return lines


# -- visible-width helpers (ANSI-aware) ---------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def _truncate(text: str, width: int) -> str:
    if _visible_len(text) <= width:
        return text
    # Strip ANSI then hard-truncate (used only for single-line footers).
    plain = _ANSI_RE.sub("", text)
    return plain[: max(0, width - 1)] + "…"


def _wrap(text: str, width: int) -> list[str]:
    """Wrap on spaces by visible width, keeping any leading ANSI prefix.

    Simple greedy word wrap; ANSI color codes are treated as zero width. Good
    enough for question/context text in the overlay.
    """

    if width <= 0:
        return [text]
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if _visible_len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current or not lines:
        lines.append(current)
    return lines


def activate(api) -> None:
    def answer_handler(ctx, _args) -> None:
        if not ctx.has_ui:
            ctx.ui.notify("answer requires interactive mode", "error")
            return
        message = ctx.conversation.last_assistant_message()
        if message is None or not message.text.strip():
            ctx.ui.notify("No assistant messages found", "error")
            return
        if not message.complete:
            ctx.ui.notify("Last assistant message is incomplete", "error")
            return

        ctx.ui.notify("Extracting questions…")
        try:
            raw = ctx.complete(SYSTEM_PROMPT, message.text)
        except ExtensionCapabilityError as exc:
            ctx.ui.notify(f"Extraction failed: {exc}", "error")
            return

        questions = parse_extraction_result(raw)
        if questions is None:
            ctx.ui.notify("Could not parse extracted questions", "error")
            return
        if not questions:
            ctx.ui.notify("No questions found in the last message", "info")
            return

        answers = ctx.ui.custom(lambda done: QnAComponent(questions, done))
        if answers is None:
            ctx.ui.notify("Cancelled", "info")
            return

        api.send_user_message(
            "I answered your questions in the following way:\n\n" + answers
        )

    api.register_command(
        "answer",
        "Extract questions from last assistant message into interactive Q&A",
        answer_handler,
    )
    api.register_shortcut("ctrl+.", answer_handler)
