"""Human-likeness rules enforced in code (ARCHITECTURE §13.4), applied sentence by sentence while streaming.

- one question per turn: everything after the first question sentence is dropped (and the stream is stopped);
- word limit per mode; no markdown, lists or emojis; no flattery ("Great answer!");
- the control block of single-call turns is split off before anything reaches the candidate.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.interviews.schemas import TurnControl

WORD_LIMIT = {"TEXT": 90, "VOICE": 45}
ACKNOWLEDGEMENTS = (
    "Okay.",
    "Got it.",
    "Right, thanks.",
    "Understood.",
    "Alright.",
    "Thanks.",
    "I see.",
    "Fair enough.",
)
ACK_MEMORY = 5

_PRAISE = re.compile(
    r"^\s*(great|excellent|perfect|amazing|awesome|fantastic|wonderful|brilliant|impressive|nice|good)"
    r"( (answer|point|question|job|work|explanation|stuff))?[.!]*\s*$",
    re.IGNORECASE,
)
_PRAISE_PREFIX = re.compile(
    r"^\s*(that'?s |that is |what )?(a |an )?(really |very )?"
    r"(great|excellent|perfect|amazing|awesome|fantastic|wonderful|brilliant|impressive)"
    r"( (answer|point|question|explanation))?[,.!]\s*",
    re.IGNORECASE,
)
# The interviewer never accuses or mentions monitoring (L0 rule 9); a sentence that does is dropped in code too.
ACCUSATION = re.compile(
    r"\b(cheat\w*|dishonest\w*|lying|lied|plagiari\w*|cop(y|ied)[- ]past\w*|pasted|you (copied|looked it up)|"
    r"switch(ed|ing)? (tabs?|windows?)|looked away|someone else|outside help|being monitored|monitoring|"
    r"flagged|suspicious|suspect\w*|integrity (signal|event|probe)s?)\b",
    re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]*_]*\s+")
_MARKDOWN = re.compile(r"(\*\*|__|`|^#+\s*|^\s*[-*•]\s+|^\s*\d+[.)]\s+)", re.MULTILINE)
_EMOJI = re.compile("[\U0001f300-\U0001faff☀-➿]")
_CONTROL = re.compile(r"<control>(.*?)</control>", re.DOTALL | re.IGNORECASE)
CONTROL_SEARCH_LIMIT = 4000


def clean_sentence(sentence: str) -> str:
    text = _EMOJI.sub("", _MARKDOWN.sub("", sentence)).strip()
    if _PRAISE.match(text) or ACCUSATION.search(text):
        return ""
    text = _PRAISE_PREFIX.sub("", text).lstrip("-\u2013\u2014\u2022*:; ").strip()
    return text[:1].upper() + text[1:] if text else ""


def split_sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text) if s.strip()]


@dataclass
class SentenceGate:
    """Feed streamed text; get back approved sentences. `done` turns true after a question or the word limit."""

    word_limit: int
    buffer: str = ""
    words: int = 0
    done: bool = False
    emitted: list[str] = field(default_factory=list)

    def _accept(self, sentence: str) -> list[str]:
        cleaned = clean_sentence(sentence)
        if not cleaned or self.done:
            return []
        self.words += len(cleaned.split())
        self.emitted.append(cleaned)
        if cleaned.rstrip().endswith("?") or self.words >= self.word_limit:
            self.done = True
        return [cleaned]

    def feed(self, delta: str) -> list[str]:
        if self.done:
            return []
        self.buffer += delta
        parts = _SENTENCE_END.split(self.buffer)
        self.buffer = parts.pop()  # the last part may be an unfinished sentence
        out: list[str] = []
        for part in parts:
            out += self._accept(part)
            if self.done:
                break
        return out

    def flush(self) -> list[str]:
        rest, self.buffer = self.buffer, ""
        return self._accept(rest) if rest.strip() else []

    @property
    def text(self) -> str:
        return " ".join(self.emitted)


def enforce(text: str, word_limit: int) -> str:
    gate = SentenceGate(word_limit)
    for sentence in split_sentences(text):
        gate._accept(sentence)
        if gate.done:
            break
    return gate.text


class ControlSplitter:
    """Single-call turns start with <control>{json}</control>; buffer until it is complete, then pass text through."""

    def __init__(self) -> None:
        self._buffer = ""
        self.control: TurnControl | None = None
        self.failed = False
        self._passthrough = False

    def feed(self, delta: str) -> str:
        if self._passthrough:
            return delta
        self._buffer += delta
        match = _CONTROL.search(self._buffer)
        if match:
            self.control = parse_control(match.group(1))
            self.failed = self.control is None
            rest = self._buffer[match.end() :]
            self._passthrough, self._buffer = True, ""
            return rest.lstrip()
        if len(self._buffer) > CONTROL_SEARCH_LIMIT or (
            "<control" not in self._buffer.lower() and len(self._buffer) > 200  # noqa: PLR2004
        ):
            # No control block: treat everything as the spoken reply.
            self.failed, self._passthrough = True, True
            rest, self._buffer = self._buffer, ""
            return rest
        return ""

    def finish(self) -> str:
        if self._passthrough:
            return ""
        self.failed = True
        rest, self._buffer = _CONTROL.sub("", self._buffer), ""
        return re.sub(r"<control>.*", "", rest, flags=re.DOTALL | re.IGNORECASE)


def parse_control(raw: str) -> TurnControl | None:
    try:
        return TurnControl.model_validate(json.loads(raw.strip()))
    except (json.JSONDecodeError, ValidationError, TypeError):
        try:
            import json_repair  # noqa: PLC0415

            return TurnControl.model_validate(json_repair.loads(raw))
        except (ValidationError, ValueError, TypeError):
            return None


def next_acknowledgement(recent: Iterable[str]) -> str:
    used = list(recent)[-ACK_MEMORY:]
    return next((a for a in ACKNOWLEDGEMENTS if a not in used), ACKNOWLEDGEMENTS[0])
