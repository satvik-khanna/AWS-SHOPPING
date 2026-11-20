"""Text helpers that work for Japanese without a tokenizer."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata

_WS = re.compile(r"\s+")
_URL = re.compile(r"https?://[^\s<>\"'）)】」』]+")


def nfkc(s: str) -> str:
    """Normalize full-width digits/letters and compatibility forms."""
    return unicodedata.normalize("NFKC", s or "")


def squash(s: str) -> str:
    return _WS.sub(" ", s or "").strip()


def find_urls(text: str) -> list[str]:
    out: list[str] = []
    for u in _URL.findall(text or ""):
        u = u.rstrip(".,、。!！?？")
        if u not in out:
            out.append(u)
    return out


def strip_urls(text: str) -> str:
    return _URL.sub("", text or "")


def char_ngrams(s: str, sizes: tuple[int, ...] = (2, 3)) -> list[str]:
    t = re.sub(r"\s+", "", nfkc(s).lower())
    grams: list[str] = []
    for n in sizes:
        grams.extend(t[i : i + n] for i in range(max(0, len(t) - n + 1)))
    return grams


def hash_embed(s: str, dims: int = 512) -> list[float]:
    """Deterministic hashed char-n-gram vector. A lexical stand-in used only
    when Superlinked SIE is unavailable; it is not a semantic embedding."""
    vec = [0.0] * dims
    for g in char_ngrams(s):
        h = int.from_bytes(hashlib.blake2b(g.encode("utf-8"), digest_size=8).digest(), "big")
        vec[h % dims] += 1.0 if (h >> 63) & 1 else -1.0
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def ngram_jaccard(a: str, b: str) -> float:
    sa, sb = set(char_ngrams(a)), set(char_ngrams(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def ngram_containment(needle: str, haystack: str) -> float:
    """Share of the needle's n-grams that also occur in the haystack."""
    sn, sh = set(char_ngrams(needle)), set(char_ngrams(haystack))
    if not sn:
        return 0.0
    return len(sn & sh) / len(sn)
