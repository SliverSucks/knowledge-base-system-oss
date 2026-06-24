from __future__ import annotations

import re

_LATIN_RE = re.compile(r"[a-z0-9]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")


def tokenize_for_retrieval(text: str) -> list[str]:
    """Tokenize mixed-language text for retrieval/rerank/embedding.

    - Latin/digit tokens keep contiguous sequences with len >= 2
    - Chinese sequences are expanded into bi-grams, plus the full sequence when short
    """
    if not text:
        return []

    out: list[str] = []

    lowered = text.lower()
    out.extend(t for t in _LATIN_RE.findall(lowered) if len(t) >= 2)

    for seg in _CJK_RE.findall(text):
        if len(seg) == 1:
            out.append(seg)
            continue
        for idx in range(len(seg) - 1):
            out.append(seg[idx : idx + 2])
        if len(seg) <= 8:
            out.append(seg)

    return out


def query_terms_for_like(query: str, max_terms: int = 12) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []

    terms: list[str] = []
    seen: set[str] = set()

    def _push(term: str) -> None:
        t = term.strip()
        if not t:
            return
        key = t.lower()
        if key in seen:
            return
        seen.add(key)
        terms.append(t)

    _push(q)
    for tok in tokenize_for_retrieval(q):
        _push(tok)
        if len(terms) >= max_terms:
            break

    return terms[:max_terms]


def token_set(text: str) -> set[str]:
    return set(tokenize_for_retrieval(text))
