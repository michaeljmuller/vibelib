# Shared library package.  Import from the specific submodule you need:
#   common.s3       — get_s3_client, get_cached_epub
#   common.epub     — extract_epub_metadata, extract_epub_cover, get_epub_asin,
#                     extract_isbn_from_content, format_isbn
#   common.amazon   — scrape_amazon_metadata
#   common.matching — normalize_title, normalize_author, match_title, match_author,
#                     prefer_longer_name
