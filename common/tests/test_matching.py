import pytest

from common.matching import (
    match_author,
    match_title,
    normalize_author,
    normalize_title,
    prefer_longer_name,
)

THRESHOLD = 85


# ---------------------------------------------------------------------------
# normalize_title
# ---------------------------------------------------------------------------

def test_normalize_title_strips_leading_the():
    assert normalize_title('The Dark Tower') == 'dark tower'


def test_normalize_title_strips_leading_a():
    assert normalize_title('A Game of Thrones') == 'game of thrones'


def test_normalize_title_strips_leading_an():
    assert normalize_title('An Unexpected Journey') == 'unexpected journey'


def test_normalize_title_does_not_strip_mid_article():
    # "the" in the middle should not be stripped
    assert normalize_title('Lord of the Rings') == 'lord of the rings'


def test_normalize_title_strips_punctuation():
    assert normalize_title('11/22/63') == '112263'


def test_normalize_title_collapses_whitespace():
    assert normalize_title('  The   Dark  Tower  ') == 'dark tower'


def test_normalize_title_lowercase():
    assert normalize_title('DUNE') == 'dune'


def test_normalize_title_empty():
    assert normalize_title('') == ''
    assert normalize_title(None) == ''


# ---------------------------------------------------------------------------
# normalize_author
# ---------------------------------------------------------------------------

def test_normalize_author_last_first():
    assert normalize_author('King, Stephen') == 'stephen king'


def test_normalize_author_last_first_with_initials():
    assert normalize_author('Wodehouse, P.G.') == 'pg wodehouse'


def test_normalize_author_initials_with_spaces():
    # P. G. Wodehouse and P.G. Wodehouse should normalize to the same thing
    assert normalize_author('P. G. Wodehouse') == normalize_author('P.G. Wodehouse')


def test_normalize_author_initials_result():
    assert normalize_author('P.G. Wodehouse') == 'pg wodehouse'


def test_normalize_author_plain_name():
    assert normalize_author('Stephen King') == 'stephen king'


def test_normalize_author_empty():
    assert normalize_author('') == ''
    assert normalize_author(None) == ''


# ---------------------------------------------------------------------------
# match_title
# ---------------------------------------------------------------------------

def test_match_title_exact_after_normalization():
    titles = ['The Dark Tower', 'A Game of Thrones']
    result = match_title('dark tower', titles, THRESHOLD)
    assert result is not None
    assert result[0] == 'The Dark Tower'
    assert result[1] == 100


def test_match_title_near_match():
    titles = ['The Shining']
    result = match_title('The Shinning', titles, THRESHOLD)
    assert result is not None
    assert result[0] == 'The Shining'


def test_match_title_below_threshold():
    titles = ['Dune']
    result = match_title('The Stand', titles, THRESHOLD)
    assert result is None


def test_match_title_none_when_empty_candidate():
    result = match_title('', ['Dune'], THRESHOLD)
    assert result is None


def test_match_title_leading_article_match():
    titles = ['The Lord of the Rings']
    result = match_title('Lord of the Rings', titles, THRESHOLD)
    assert result is not None
    assert result[0] == 'The Lord of the Rings'


# ---------------------------------------------------------------------------
# match_author
# ---------------------------------------------------------------------------

def test_match_author_exact_after_normalization():
    authors = ['Stephen King']
    result = match_author('King, Stephen', authors, THRESHOLD)
    assert result is not None
    assert result[0] == 'Stephen King'
    assert result[1] == 100


def test_match_author_initials_variant():
    authors = ['P.G. Wodehouse']
    result = match_author('P. G. Wodehouse', authors, THRESHOLD)
    assert result is not None
    assert result[0] == 'P.G. Wodehouse'


def test_match_author_below_threshold():
    authors = ['Stephen King']
    result = match_author('J.R.R. Tolkien', authors, THRESHOLD)
    assert result is None


def test_match_author_none_when_empty_candidate():
    result = match_author('', ['Stephen King'], THRESHOLD)
    assert result is None


# ---------------------------------------------------------------------------
# prefer_longer_name
# ---------------------------------------------------------------------------

def test_prefer_longer_name():
    assert prefer_longer_name('Stephen King', 'King S') == 'Stephen King'


def test_prefer_longer_name_equal_length():
    assert prefer_longer_name('King, S.', 'S. King') == 'King, S.'
