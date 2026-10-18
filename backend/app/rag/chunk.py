"""Structure-aware parent/child chunking.

Parents (~1200 tokens) are what the LLM reads; children (~300 tokens, 50 overlap) are what gets embedded.
Headings set section paths; tables and code blocks are never split mid-way (a register map split across
chunks is useless). CVs get one parent per section; datasheet tables become their own chunks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.db.models import DocType
from app.rag.parse import ParsedDocument
from app.rag.text import approx_tokens

PARENT_TARGET = 1200
CHILD_TARGET = 300
CHILD_OVERLAP = 50
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")
_CV_SECTION_LEVEL = 6  # every heading starts a new parent in a CV
_GENERIC_SECTION_LEVEL = 2


@dataclass
class Block:
    text: str
    page: int
    section: list[str]
    kind: str  # "text" | "table" | "code"

    @property
    def tokens(self) -> int:
        return approx_tokens(self.text)


@dataclass
class ChildChunk:
    text: str
    page_from: int
    page_to: int
    token_count: int


@dataclass
class ParentChunk:
    text: str
    section_path: list[str]
    page_from: int
    page_to: int
    token_count: int
    children: list[ChildChunk] = field(default_factory=list)


class _BlockBuilder:
    """Turns Markdown lines into text/table/code blocks, tracking the heading stack across pages."""

    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self.stack: list[tuple[int, str]] = []
        self.buffer: list[str] = []
        self.page = 1

    def flush(self, kind: str) -> None:
        text = "\n".join(self.buffer).strip()
        if text:
            self.blocks.append(Block(text, self.page, [title for _, title in self.stack], kind))
        self.buffer.clear()

    def heading(self, level: int, title: str) -> None:
        while self.stack and self.stack[-1][0] >= level:
            self.stack.pop()
        self.stack.append((level, title[:300]))

    def feed_page(self, number: int, markdown: str) -> None:
        self.page = number
        kind = "text"
        in_code = False
        for line in markdown.splitlines():
            if line.strip().startswith("```"):
                if in_code:
                    self.buffer.append(line)
                    self.flush("code")
                    kind = "text"
                else:
                    self.flush(kind)
                    self.buffer.append(line)
                in_code = not in_code
                continue
            if in_code:
                self.buffer.append(line)
                continue
            heading = _HEADING.match(line.strip())
            if heading:
                self.flush(kind)
                self.heading(len(heading.group(1)), heading.group(2))
                kind = "text"
                continue
            is_table_line = line.lstrip().startswith("|")
            if is_table_line != (kind == "table") and self.buffer:
                self.flush(kind)
            kind = "table" if is_table_line else kind if line.strip() else "text"
            if not line.strip() and kind == "text":
                self.flush(kind)
                continue
            self.buffer.append(line)
        self.flush("code" if in_code else kind)


def _blocks(parsed: ParsedDocument) -> list[Block]:
    builder = _BlockBuilder()
    for page in parsed.pages:
        builder.feed_page(page.number, page.markdown)
    blocks: list[Block] = []
    for block in builder.blocks:
        if block.kind == "text" and block.tokens > PARENT_TARGET:
            # One enormous paragraph (common in extracted PDFs) must still fit the parent budget.
            blocks.extend(
                Block(piece, block.page, block.section, "text") for piece in _split_text(block.text, PARENT_TARGET, 0)
            )
        else:
            blocks.append(block)
    return blocks


def _split_text(text: str, target: int, overlap: int) -> list[str]:
    """Split prose into ~target-token pieces on sentence boundaries, carrying `overlap` tokens forward."""
    sentences = _SENTENCE.split(text)
    pieces: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        if current and approx_tokens(" ".join([*current, sentence])) > target:
            pieces.append(" ".join(current))
            carry: list[str] = []
            for previous in reversed(current):
                if approx_tokens(" ".join([previous, *carry])) > overlap:
                    break
                carry.insert(0, previous)
            current = carry
        current.append(sentence)
    if current:
        pieces.append(" ".join(current))
    return [p for p in pieces if p.strip()]


def _split_table(text: str, target: int) -> list[str]:
    """Split an oversized Markdown table into row groups that each repeat the header."""
    rows = text.splitlines()
    header, body = rows[:2], rows[2:]
    pieces: list[str] = []
    current: list[str] = []
    for row in body:
        if current and approx_tokens("\n".join(header + current + [row])) > target:
            pieces.append("\n".join(header + current))
            current = []
        current.append(row)
    if current or not pieces:
        pieces.append("\n".join(header + current))
    return pieces


def _children(blocks: list[Block]) -> list[ChildChunk]:
    children: list[ChildChunk] = []
    pending: list[Block] = []

    def emit_pending() -> None:
        if not pending:
            return
        joined = "\n\n".join(b.text for b in pending)
        for piece in _split_text(joined, CHILD_TARGET, CHILD_OVERLAP):
            children.append(ChildChunk(piece, pending[0].page, pending[-1].page, approx_tokens(piece)))
        pending.clear()

    for block in blocks:
        if block.kind in {"table", "code"}:
            emit_pending()
            pieces = (
                _split_table(block.text, PARENT_TARGET)
                if block.kind == "table" and block.tokens > PARENT_TARGET
                else [block.text]
            )
            children.extend(ChildChunk(p, block.page, block.page, approx_tokens(p)) for p in pieces)
            continue
        if pending and approx_tokens("\n\n".join([*(b.text for b in pending), block.text])) > CHILD_TARGET:
            emit_pending()
        pending.append(block)
    emit_pending()
    return children


def chunk_document(parsed: ParsedDocument, doc_type: DocType) -> list[ParentChunk]:
    blocks = _blocks(parsed)
    section_level = _CV_SECTION_LEVEL if doc_type == DocType.CV else _GENERIC_SECTION_LEVEL
    parents: list[ParentChunk] = []
    group: list[Block] = []

    def emit() -> None:
        if not group:
            return
        text = "\n\n".join(b.text for b in group)
        parents.append(
            ParentChunk(
                text=text,
                section_path=group[0].section,
                page_from=group[0].page,
                page_to=group[-1].page,
                token_count=approx_tokens(text),
                children=_children(group),
            )
        )
        group.clear()

    for block in blocks:
        new_section = bool(group) and block.section[:section_level] != group[0].section[:section_level]
        standalone_table = doc_type == DocType.DATASHEET and block.kind == "table"
        too_big = bool(group) and approx_tokens("\n\n".join([*(b.text for b in group), block.text])) > PARENT_TARGET
        if new_section or too_big or standalone_table:
            emit()
        group.append(block)
        if standalone_table:
            emit()
    emit()
    return [p for p in parents if p.children]


def embedding_text(title: str, section_path: list[str], text: str) -> str:
    """Child text prefixed with where it lives, so retrieval sees the context (cheap contextual retrieval)."""
    location = " > ".join([title, *section_path]) if section_path else title
    return f"{location}\n{text}"
