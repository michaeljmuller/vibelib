"""
Title and author normalization and fuzzy matching for the bootstrap ingestion pipeline.
"""

import re

from rapidfuzz import fuzz


def normalize_title(title):
    """
    Normalize a book title for comparison.
    Steps: lowercase, strip punctuation, strip leading articles, collapse whitespace.
    """
    if not title:
        return ''
    title = title.lower()
    title = re.sub(r'[^\w\s]', '', title)
    title = re.sub(r'\s+', ' ', title).strip()  # collapse whitespace before article check
    title = re.sub(r'^(the|a|an)\s+', '', title)
    return title


def normalize_author(name):
    """
    Normalize an author name for comparison.
    Steps: convert Last, First to First Last; remove periods (handles P.G. -> PG);
    collapse whitespace; lowercase.
    """
    if not name:
        return ''
    if ',' in name:
        parts = name.split(',', 1)
        name = f'{parts[1].strip()} {parts[0].strip()}'
    name = name.replace('.', '')
    name = re.sub(r'\s+', ' ', name).strip().lower()
    # Join single-letter initials that were separated by periods (e.g. "p g" -> "pg")
    name = re.sub(r'(?<=[a-z]) (?=[a-z]\b)', '', name)
    return name


def match_title(candidate, existing_titles, threshold=85):
    """
    Find the best matching title in existing_titles for candidate.

    Returns (original_title, score) if a match at or above threshold is found,
    or None if nothing clears the threshold. Exact normalized matches score 100.

    Args:
        candidate: Title string to match.
        existing_titles: Iterable of original title strings.
        threshold: Minimum rapidfuzz score (0-100) to accept a match.
    """
    norm_candidate = normalize_title(candidate)
    if not norm_candidate:
        return None

    best_match = None
    best_score = -1

    for original in existing_titles:
        norm_existing = normalize_title(original)
        if norm_candidate == norm_existing:
            return original, 100
        score = fuzz.ratio(norm_candidate, norm_existing)
        if score > best_score:
            best_score = score
            best_match = original

    if best_score >= threshold:
        return best_match, best_score
    return None


def match_author(candidate, existing_authors, threshold=85):
    """
    Find the best matching author in existing_authors for candidate.

    Returns (original_name, score) if a match at or above threshold is found,
    or None if nothing clears the threshold. Exact normalized matches score 100.

    Args:
        candidate: Author name string to match.
        existing_authors: Iterable of original author name strings.
        threshold: Minimum rapidfuzz score (0-100) to accept a match.
    """
    norm_candidate = normalize_author(candidate)
    if not norm_candidate:
        return None

    best_match = None
    best_score = -1

    for original in existing_authors:
        norm_existing = normalize_author(original)
        if norm_candidate == norm_existing:
            return original, 100
        score = fuzz.ratio(norm_candidate, norm_existing)
        if score > best_score:
            best_score = score
            best_match = original

    if best_score >= threshold:
        return best_match, best_score
    return None


def prefer_longer_name(name1, name2):
    """Return the longer of two author name forms (used to pick the primary_name)."""
    return name1 if len(name1) >= len(name2) else name2
