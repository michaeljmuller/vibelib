"""
Unit tests for bootstrap/s3_cache.py.
S3 calls are mocked so no real AWS credentials are needed.
"""

import datetime
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from s3_cache import get_cached_file, list_s3_objects


def _make_obj(key, size=1000, etag='abc123'):
    return {
        'Key': key,
        'Size': size,
        'LastModified': datetime.datetime(2024, 1, 1),
        'ETag': f'"{etag}"',
    }


def _paginator_response(*pages):
    """Build a mock paginator that yields the given pages."""
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
        {'Contents': page} for page in pages
    ]
    return mock_paginator


# ---------------------------------------------------------------------------
# list_s3_objects
# ---------------------------------------------------------------------------

@patch('s3_cache.get_s3_client')
def test_list_filters_non_epub_m4b(mock_client):
    mock_s3 = MagicMock()
    mock_client.return_value = mock_s3
    mock_s3.get_paginator.return_value = _paginator_response([
        _make_obj('book.epub'),
        _make_obj('notes.txt'),
        _make_obj('audio.mp3'),
        _make_obj('story.m4b'),
    ])

    results = list(list_s3_objects('my-bucket'))
    keys = [r[0] for r in results]
    assert 'book.epub' in keys
    assert 'story.m4b' in keys
    assert 'notes.txt' not in keys
    assert 'audio.mp3' not in keys


@patch('s3_cache.get_s3_client')
def test_list_epubs_before_m4bs(mock_client):
    mock_s3 = MagicMock()
    mock_client.return_value = mock_s3
    mock_s3.get_paginator.return_value = _paginator_response([
        _make_obj('audio1.m4b'),
        _make_obj('audio2.m4b'),
        _make_obj('book1.epub'),
        _make_obj('book2.epub'),
    ])

    keys = [r[0] for r in list_s3_objects('my-bucket')]
    epub_indices = [i for i, k in enumerate(keys) if k.endswith('.epub')]
    m4b_indices = [i for i, k in enumerate(keys) if k.endswith('.m4b')]
    assert max(epub_indices) < min(m4b_indices)


@patch('s3_cache.get_s3_client')
def test_list_paginates_multiple_pages(mock_client):
    mock_s3 = MagicMock()
    mock_client.return_value = mock_s3
    # Two pages with 3 valid objects each
    mock_s3.get_paginator.return_value = _paginator_response(
        [_make_obj('a.epub'), _make_obj('b.epub'), _make_obj('skip.txt')],
        [_make_obj('c.epub'), _make_obj('d.m4b'), _make_obj('e.m4b')],
    )

    keys = [r[0] for r in list_s3_objects('my-bucket')]
    assert set(keys) == {'a.epub', 'b.epub', 'c.epub', 'd.m4b', 'e.m4b'}


@patch('s3_cache.get_s3_client')
def test_list_limit_keys(mock_client):
    mock_s3 = MagicMock()
    mock_client.return_value = mock_s3
    mock_s3.get_paginator.return_value = _paginator_response([
        _make_obj('a.epub'),
        _make_obj('b.epub'),
        _make_obj('c.m4b'),
    ])

    keys = [r[0] for r in list_s3_objects('my-bucket', limit_keys={'a.epub'})]
    assert keys == ['a.epub']


@patch('s3_cache.get_s3_client')
def test_list_max_files(mock_client):
    mock_s3 = MagicMock()
    mock_client.return_value = mock_s3
    mock_s3.get_paginator.return_value = _paginator_response([
        _make_obj('a.epub'),
        _make_obj('b.epub'),
        _make_obj('c.m4b'),
    ])

    results = list(list_s3_objects('my-bucket', max_files=2))
    assert len(results) == 2


@patch('s3_cache.get_s3_client')
def test_list_returns_correct_tuple_fields(mock_client):
    mock_s3 = MagicMock()
    mock_client.return_value = mock_s3
    ts = datetime.datetime(2024, 6, 15)
    mock_s3.get_paginator.return_value = _paginator_response([
        {'Key': 'book.epub', 'Size': 5000, 'LastModified': ts, 'ETag': '"etag42"'},
    ])

    results = list(list_s3_objects('my-bucket'))
    assert len(results) == 1
    key, size, last_modified, etag = results[0]
    assert key == 'book.epub'
    assert size == 5000
    assert last_modified == ts
    assert etag == 'etag42'  # quotes stripped


# ---------------------------------------------------------------------------
# get_cached_file
# ---------------------------------------------------------------------------

@patch('s3_cache.get_s3_client')
def test_get_cached_file_downloads_on_first_call(mock_client, tmp_path):
    mock_s3 = MagicMock()
    mock_client.return_value = mock_s3
    mock_s3.get_object.return_value = {'Body': MagicMock(read=lambda: b'epub data')}

    result = get_cached_file('book.epub', 'etag1', 'my-bucket', tmp_path)

    mock_s3.get_object.assert_called_once_with(Bucket='my-bucket', Key='book.epub')
    assert result.exists()
    assert result.read_bytes() == b'epub data'


@patch('s3_cache.get_s3_client')
def test_get_cached_file_cache_hit_no_download(mock_client, tmp_path):
    mock_s3 = MagicMock()
    mock_client.return_value = mock_s3
    mock_s3.get_object.return_value = {'Body': MagicMock(read=lambda: b'epub data')}

    # First call — downloads
    get_cached_file('book.epub', 'etag1', 'my-bucket', tmp_path)
    assert mock_s3.get_object.call_count == 1

    # Second call with same ETag — must NOT download again
    get_cached_file('book.epub', 'etag1', 'my-bucket', tmp_path)
    assert mock_s3.get_object.call_count == 1


@patch('s3_cache.get_s3_client')
def test_get_cached_file_redownloads_on_etag_change(mock_client, tmp_path):
    mock_s3 = MagicMock()
    mock_client.return_value = mock_s3
    mock_s3.get_object.return_value = {'Body': MagicMock(read=lambda: b'new data')}

    # First call
    get_cached_file('book.epub', 'etag1', 'my-bucket', tmp_path)
    assert mock_s3.get_object.call_count == 1

    # Second call with a different ETag — must re-download
    get_cached_file('book.epub', 'etag2', 'my-bucket', tmp_path)
    assert mock_s3.get_object.call_count == 2


@patch('s3_cache.get_s3_client')
def test_get_cached_file_returns_path(mock_client, tmp_path):
    mock_s3 = MagicMock()
    mock_client.return_value = mock_s3
    mock_s3.get_object.return_value = {'Body': MagicMock(read=lambda: b'data')}

    result = get_cached_file('archive.m4b', 'etag1', 'my-bucket', tmp_path)
    assert isinstance(result, Path)
    assert result.suffix == '.m4b'
