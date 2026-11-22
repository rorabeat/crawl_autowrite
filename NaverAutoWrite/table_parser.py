"""Identify simple Markdown tables without changing the surrounding body text."""
from __future__ import annotations

from dataclasses import dataclass
import html
import re
from typing import Iterator

import mistune


@dataclass(frozen=True)
class MarkdownTable:
    rows: list[list[str]]
    source_lines: tuple[str, ...]


_markdown = mistune.create_markdown(renderer=None, plugins=['table'])
_fence = re.compile(r'^ {0,3}(`{3,}|~{3,})')


def _cell_text(tokens: list[dict]) -> str:
    parts = []
    for token in tokens:
        kind = token.get('type')
        if kind == 'link':
            label = _cell_text(token.get('children', []))
            url = token.get('attrs', {}).get('url', '')
            parts.append(f'{label} ({url})' if url and url != label else label)
        elif 'children' in token:
            parts.append(_cell_text(token['children']))
        elif kind in ('softbreak', 'linebreak'):
            parts.append(' ')
        elif kind == 'text':
            parts.append(html.unescape(token.get('raw', '')))
        else:
            parts.append(token.get('raw', ''))
    return ''.join(parts)


def _parse_table(lines: list[str]) -> MarkdownTable | None:
    tokens = _markdown('\n'.join(lines) + '\n')
    if len(tokens) != 1 or tokens[0].get('type') != 'table':
        return None
    children = tokens[0].get('children', [])
    if not children or children[0].get('type') != 'table_head':
        return None
    rows = [[_cell_text(cell.get('children', [])) for cell in children[0].get('children', [])]]
    for part in children[1:]:
        if part.get('type') == 'table_body':
            for row in part.get('children', []):
                rows.append([_cell_text(cell.get('children', [])) for cell in row.get('children', [])])
    if not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        return None
    return MarkdownTable(rows, tuple(lines))


def split_body_blocks(lines: list[str]) -> Iterator[str | MarkdownTable]:
    """Yield body lines and rectangular tables in document order; ignore code fences."""
    index = 0
    fence_char = None
    fence_length = 0
    while index < len(lines):
        line = lines[index]
        match = _fence.match(line)
        if match:
            marker = match.group(1)
            if fence_char is None:
                fence_char, fence_length = marker[0], len(marker)
            elif marker[0] == fence_char and len(marker) >= fence_length and not line[match.end():].strip():
                fence_char = None
            yield line
            index += 1
            continue
        if fence_char is None and index + 1 < len(lines):
            source = lines[index:index + 2]
            table = _parse_table(source)
            if table is not None:
                end = index + 2
                while end < len(lines) and lines[end].strip():
                    candidate = _parse_table(source + [lines[end]])
                    if candidate is None:
                        break
                    source.append(lines[end])
                    table = candidate
                    end += 1
                yield table
                index = end
                continue
        yield line
        index += 1
