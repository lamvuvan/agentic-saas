"""Vietnamese NLP utilities — pure functions, no LLM calls.

All functions are stateless and side-effect-free for easy unit testing.
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------------------
# Constants (research.md Decision 7)
# ---------------------------------------------------------------------------

VN_NUMBERS: dict[str, int] = {
    "mười hai": 12,
    "mười một": 11,
    "mười": 10,
    "chín": 9,
    "tám": 8,
    "bảy": 7,
    "sáu": 6,
    "năm": 5,
    "bốn": 4,
    "ba": 3,
    "hai": 2,
    "một": 1,
}

HONORIFICS: frozenset[str] = frozenset({"anh", "chị", "em", "bác", "cô", "chú", "ông", "bà"})

# Notes typically introduced by these keywords
_NOTE_KEYWORDS = frozenset({
    "ít", "nhiều", "không", "thêm", "bớt", "nóng", "lạnh", "nước", "đá",
    "đường", "béo", "gầy", "kem", "sữa", "tặng", "trân châu", "ướp",
})


# ---------------------------------------------------------------------------
# normalize_text
# ---------------------------------------------------------------------------


def normalize_text(text: str) -> str:
    """
    Normalize Vietnamese text for embedding/matching:
    1. Unicode NFC normalization
    2. Lowercase
    3. Strip leading/trailing whitespace (including newlines/tabs)
    """
    normalized = unicodedata.normalize("NFC", text)
    return normalized.lower().strip()


# ---------------------------------------------------------------------------
# strip_honorifics
# ---------------------------------------------------------------------------


def strip_honorifics(text: str) -> str:
    """
    Remove Vietnamese honorifics from the start of a name string.
    'anh Lâm' → 'Lâm'
    'chị Mai Anh' → 'Mai Anh'
    """
    stripped = text.strip()
    lower = stripped.lower()
    for honorific in sorted(HONORIFICS, key=len, reverse=True):
        if lower.startswith(honorific + " "):
            return stripped[len(honorific):].strip()
        if lower.startswith(honorific.upper() + " ") or lower == honorific:
            return stripped[len(honorific):].strip()
    return stripped


# ---------------------------------------------------------------------------
# words_to_numbers
# ---------------------------------------------------------------------------


def words_to_numbers(text: str) -> str:
    """
    Convert Vietnamese number words to digits.
    'hai trứng lộn' → '2 trứng lộn'
    'mười hai cái bánh' → '12 cái bánh'

    Multi-word numbers (mười một, mười hai) are replaced first.
    """
    result = text
    # Sort by length descending to match longer phrases first
    for word, digit in sorted(VN_NUMBERS.items(), key=lambda x: len(x[0]), reverse=True):
        # Word boundary replacement (handle spaces around number word)
        pattern = r"(?<![^\s,])(" + re.escape(word) + r")(?![^\s,])"
        result = re.sub(pattern, str(digit), result, flags=re.IGNORECASE)
    return result


# ---------------------------------------------------------------------------
# extract_product_note
# ---------------------------------------------------------------------------


def extract_product_note(text: str) -> tuple[str, str]:
    """
    Split a product description into (product_name, note).
    'cà phê ít đường' → ('cà phê', 'ít đường')
    'trứng lộn' → ('trứng lộn', '')

    Heuristic: note starts when a note keyword appears after the core product name.
    Returns (product, note) tuple.
    """
    if not text.strip():
        return (text, "")

    words = text.split()
    note_start_idx: int | None = None

    for i, word in enumerate(words):
        w_lower = word.lower()
        # Check if this word (or combined with next) is a note keyword
        if w_lower in _NOTE_KEYWORDS and i > 0:
            note_start_idx = i
            break

    if note_start_idx is not None:
        product = " ".join(words[:note_start_idx]).strip()
        note = " ".join(words[note_start_idx:]).strip()
        return (product, note)

    return (text.strip(), "")
