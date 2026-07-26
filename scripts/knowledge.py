"""Transparent keyword retrieval for ChiefMind's Markdown knowledge base."""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path

from config import KNOWLEDGE_BASE_FILE


# Removing common connective words makes overlap scores reflect the meaningful
# terms in a query. The list is deliberately small and auditable.
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "i",
    "in",
    "is",
    "it",
    "my",
    "of",
    "on",
    "or",
    "the",
    "to",
    "we",
    "what",
    "when",
    "with",
}
TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
SECTION_HEADING_PATTERN = re.compile(r"(?m)^##[ \t]+.+$")


@dataclass(frozen=True)
class KnowledgeSection:
    """One independently retrievable `##` section."""

    heading: str
    content: str
    position: int

    @property
    def text(self) -> str:
        return f"{self.heading}\n{self.content}".rstrip()


def tokenize(text: str) -> set[str]:
    """Normalize text into unique, meaningful keyword variants."""
    tokens: set[str] = set()
    for raw_token in TOKEN_PATTERN.findall(text.lower()):
        if raw_token in STOP_WORDS:
            continue
        tokens.add(raw_token)
        # A tiny plural normalization lets "refunds" match "refund" without
        # adding a stemming library or making the scoring opaque.
        if len(raw_token) > 4 and raw_token.endswith("s") and not raw_token.endswith(
            "ss"
        ):
            tokens.add(raw_token[:-1])
    return tokens


def parse_sections(markdown: str) -> list[KnowledgeSection]:
    """Split Markdown at level-two headings while preserving source text."""
    matches = list(SECTION_HEADING_PATTERN.finditer(markdown))
    sections: list[KnowledgeSection] = []
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(
            markdown
        )
        heading = match.group().strip()
        content = markdown[match.end() : end].strip()
        sections.append(
            KnowledgeSection(
                heading=heading,
                content=content,
                position=position,
            )
        )
    return sections


def load_knowledge_sections(
    knowledge_base_file: Path = KNOWLEDGE_BASE_FILE,
) -> list[KnowledgeSection]:
    """Load and validate the configured knowledge-base document."""
    if not knowledge_base_file.is_file():
        raise FileNotFoundError(
            f"Knowledge base not found: {knowledge_base_file}. "
            "Set KNOWLEDGE_BASE_FILE or create docs/KnowledgeBase.md."
        )

    markdown = knowledge_base_file.read_text(encoding="utf-8")
    sections = parse_sections(markdown)
    if not sections:
        raise ValueError(
            f"Knowledge base {knowledge_base_file} has no `##` sections."
        )
    return sections


def _score_section(
    section: KnowledgeSection,
    query_keywords: set[str],
    normalized_query: str,
) -> tuple[int, int]:
    """Return a transparent heading-weighted overlap score."""
    heading_keywords = tokenize(section.heading)
    body_keywords = tokenize(section.content)

    heading_overlap = len(query_keywords & heading_keywords)
    body_overlap = len(query_keywords & body_keywords)
    phrase_bonus = 2 if normalized_query in section.text.lower() else 0

    # Heading matches are more intentional than incidental body matches.
    score = (heading_overlap * 3) + body_overlap + phrase_bonus
    return score, heading_overlap


def retrieve_relevant_sections(query: str, top_k: int = 3) -> str:
    """Return the highest-scoring knowledge sections as traceable Markdown.

    Sections with zero keyword overlap are excluded so downstream agents can
    distinguish "no grounded answer" from weak or unrelated evidence.
    Ties prefer a heading match and then the document's original order.
    """
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        raise ValueError("top_k must be a positive integer")

    query_keywords = tokenize(query)
    if not query_keywords:
        return ""

    normalized_query = " ".join(TOKEN_PATTERN.findall(query.lower()))
    ranked: list[tuple[int, int, int, KnowledgeSection]] = []
    for section in load_knowledge_sections():
        score, heading_overlap = _score_section(
            section, query_keywords, normalized_query
        )
        if score > 0:
            ranked.append(
                (score, heading_overlap, section.position, section)
            )

    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return "\n\n".join(item[3].text for item in ranked[:top_k])


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Retrieve relevant ChiefMind knowledge-base sections."
    )
    parser.add_argument("query", nargs="+", help="Words describing the request")
    parser.add_argument("--top-k", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = retrieve_relevant_sections(" ".join(args.query), args.top_k)
    if not result:
        print("No grounded knowledge-base section matched the query.")
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
