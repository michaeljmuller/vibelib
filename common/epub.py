import logging
import re
import warnings

warnings.filterwarnings('ignore', category=FutureWarning, module='ebooklib')
from ebooklib import epub

logger = logging.getLogger(__name__)


def extract_isbn_from_content(book):
    """
    Extract ISBN from copyright page content.
    Returns a single ISBN string, preferring ebook ISBN if multiple found.
    """
    isbn_pattern = re.compile(
        r'(?:ISBN[-:\s]*)?'
        r'(97[89](?:[-\s]?\d){10}|'
        r'(?:\d[-\s]?){9}[\dXx])',
        re.IGNORECASE
    )

    copyright_keywords = ['copyright', 'colophon', 'imprint', 'legal', 'rights']
    ebook_keywords = ['ebook', 'e-book', 'epub', 'digital', 'electronic']

    found_isbns = []

    for item in book.get_items():
        if item.media_type not in ['application/xhtml+xml', 'text/html']:
            continue

        item_name = (item.get_name() or '').lower()
        item_id = (item.get_id() or '').lower()

        is_copyright_page = any(
            kw in item_name or kw in item_id
            for kw in copyright_keywords
        )

        try:
            content = item.get_content().decode('utf-8', errors='ignore')
        except Exception:
            continue

        content_lower = content.lower()
        if not is_copyright_page:
            is_copyright_page = 'copyright' in content_lower or '©' in content

        if not is_copyright_page:
            continue

        for match in isbn_pattern.finditer(content):
            isbn_raw = match.group(1)
            isbn = re.sub(r'[-\s]', '', isbn_raw)

            after_pos = match.end()
            end_pos = after_pos + 50
            after_text = content[after_pos:end_pos]
            for delim in ['\n', '<', '\r']:
                delim_pos = after_text.find(delim)
                if delim_pos != -1:
                    after_text = after_text[:delim_pos]
            after_isbn = after_text.lower()

            is_ebook = any(kw in after_isbn for kw in ebook_keywords)
            found_isbns.append((isbn, is_ebook))

    if not found_isbns:
        return None

    ebook_isbns = [isbn for isbn, is_ebook in found_isbns if is_ebook]
    if ebook_isbns:
        return ebook_isbns[0]

    return found_isbns[0][0]


def format_isbn(isbn):
    """Format ISBN with hyphens (3-1-4-4-1 for ISBN-13, 1-4-4-1 for ISBN-10)."""
    isbn = re.sub(r'[-\s]', '', isbn)

    if len(isbn) == 13:
        return f'{isbn[:3]}-{isbn[3]}-{isbn[4:8]}-{isbn[8:12]}-{isbn[12]}'
    elif len(isbn) == 10:
        return f'{isbn[0]}-{isbn[1:5]}-{isbn[5:9]}-{isbn[9]}'
    else:
        return isbn


def get_epub_asin(epub_path):
    """Extract ASIN from an EPUB file without full metadata extraction."""
    book = epub.read_epub(str(epub_path), options={'ignore_ncx': True})
    for identifier in book.get_metadata('DC', 'identifier'):
        value = identifier[0]
        attrs = identifier[1] if len(identifier) > 1 else {}
        scheme = attrs.get('scheme', attrs.get('{http://www.idpf.org/2007/opf}scheme'))
        if not scheme and value and value.startswith('urn:'):
            parts = value.split(':', 2)
            if len(parts) >= 3:
                scheme, value = parts[1], parts[2]
        if scheme and scheme.lower() in ('asin', 'mobi-asin'):
            if re.match(r'^B[A-Z0-9]{9}$', value, re.IGNORECASE):
                return value
    return None


def extract_epub_metadata(epub_path):
    """Extract metadata from an EPUB file."""
    book = epub.read_epub(str(epub_path), options={'ignore_ncx': True})

    metadata = {
        'title': None,
        'authors': [],
        'description': None,
        'language': None,
        'publisher': None,
        'date': None,
        'identifiers': {},
        'subjects': [],
        'rights': None,
    }

    titles = book.get_metadata('DC', 'title')
    if titles:
        metadata['title'] = titles[0][0]

    creators = book.get_metadata('DC', 'creator')
    for creator in creators:
        metadata['authors'].append(creator[0])

    descriptions = book.get_metadata('DC', 'description')
    if descriptions:
        metadata['description'] = descriptions[0][0]

    languages = book.get_metadata('DC', 'language')
    if languages:
        metadata['language'] = languages[0][0]

    publishers = book.get_metadata('DC', 'publisher')
    if publishers:
        metadata['publisher'] = publishers[0][0]

    dates = book.get_metadata('DC', 'date')
    if dates:
        metadata['date'] = dates[0][0]

    identifiers = book.get_metadata('DC', 'identifier')
    for identifier in identifiers:
        value = identifier[0]
        attrs = identifier[1] if len(identifier) > 1 else {}
        scheme = attrs.get('scheme', attrs.get('{http://www.idpf.org/2007/opf}scheme'))

        if not scheme and value:
            if value.startswith('urn:'):
                parts = value.split(':', 2)
                if len(parts) >= 3:
                    scheme = parts[1]
                    value = parts[2]

        if scheme:
            metadata['identifiers'][scheme.lower()] = value
        else:
            metadata['identifiers']['unknown'] = value

    subjects = book.get_metadata('DC', 'subject')
    for subject in subjects:
        metadata['subjects'].append(subject[0])

    rights = book.get_metadata('DC', 'rights')
    if rights:
        metadata['rights'] = rights[0][0]

    content_isbn = extract_isbn_from_content(book)
    if content_isbn:
        metadata['isbn'] = format_isbn(content_isbn)
    elif 'isbn' in metadata['identifiers']:
        metadata['isbn'] = format_isbn(metadata['identifiers']['isbn'])

    logger.info(
        'EPUB metadata extracted: title=%r, authors=%s, publisher=%r, '
        'date=%r, isbn=%r, language=%r, subjects=%d',
        metadata['title'],
        metadata['authors'],
        metadata['publisher'],
        metadata['date'],
        metadata.get('isbn'),
        metadata['language'],
        len(metadata['subjects']),
    )

    return metadata


def extract_epub_cover(epub_path):
    """Extract cover image from an EPUB file. Returns (image_data, content_type) or (None, None)."""
    book = epub.read_epub(str(epub_path), options={'ignore_ncx': True})

    cover_id = None

    meta_covers = book.get_metadata('OPF', 'cover')
    if meta_covers:
        if meta_covers[0][0]:
            cover_id = meta_covers[0][0]
        elif isinstance(meta_covers[0][1], dict):
            cover_id = meta_covers[0][1].get('content')

    if cover_id:
        for item in book.get_items():
            if item.get_id() == cover_id or item.get_name() == cover_id:
                if item.media_type and item.media_type.startswith('image/'):
                    return item.get_content(), item.media_type

    for item in book.get_items():
        item_id = item.get_id().lower() if item.get_id() else ''
        item_name = item.get_name().lower() if item.get_name() else ''

        if any(x in item_id for x in ['cover', 'cover-image']) or \
           any(x in item_name for x in ['cover', 'cover-image']):
            if item.media_type and item.media_type.startswith('image/'):
                return item.get_content(), item.media_type

    for item in book.get_items():
        if item.media_type and item.media_type.startswith('image/'):
            name = item.get_name().lower() if item.get_name() else ''
            if 'cover' in name:
                return item.get_content(), item.media_type

    return None, None
