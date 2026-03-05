import logging
import re
import time

from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# In-memory cache: {asin: (data, expiry_time)}
_AMAZON_CACHE = {}
AMAZON_CACHE_TTL = 3600  # 1 hour


def scrape_amazon_metadata(asin):
    """
    Scrape Kindle metadata from Amazon for a given ASIN using Playwright.
    Returns a dict with rating, num_ratings, pages, publication_date, series,
    or raises an exception if scraping fails.
    Results are cached in memory for AMAZON_CACHE_TTL seconds.
    """
    now = time.time()
    if asin in _AMAZON_CACHE:
        data, expiry = _AMAZON_CACHE[asin]
        if now < expiry:
            logger.info('Amazon cache hit for ASIN %s', asin)
            return data

    url = f'https://www.amazon.com/dp/{asin}'

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            viewport={'width': 1280, 'height': 800},
            locale='en-US',
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until='networkidle', timeout=30000)

            if 'robot' in page.url or 'captcha' in page.url.lower():
                raise RuntimeError(f'Amazon returned CAPTCHA for ASIN {asin}')
            if page.query_selector("form[action='/errors/validateCaptcha']"):
                raise RuntimeError(f'Amazon returned CAPTCHA for ASIN {asin}')

            result = {
                'asin': asin,
                'url': url,
                'rating': None,
                'num_ratings': None,
                'pages': None,
                'publication_date': None,
                'series': None,
            }

            rating_el = page.query_selector("span[data-hook='rating-out-of-text']")
            if not rating_el:
                rating_el = page.query_selector('#acrPopover')
            if rating_el:
                rating_text = rating_el.get_attribute('title') or rating_el.inner_text()
                m = re.search(r'([\d.]+)\s*out of', rating_text)
                if m:
                    result['rating'] = float(m.group(1))

            review_el = page.query_selector("span[data-hook='total-review-count']")
            if not review_el:
                review_el = page.query_selector('#acrCustomerReviewText')
            if review_el:
                review_text = review_el.inner_text()
                m = re.search(r'([\d,]+)', review_text)
                if m:
                    result['num_ratings'] = int(m.group(1).replace(',', ''))

            series_el = page.query_selector('#seriesBulletWidget_feature_div a')
            if series_el:
                result['series'] = series_el.inner_text().strip()

            bullets = page.query_selector_all('#detailBullets_feature_div li .a-list-item')
            for bullet in bullets:
                text = bullet.inner_text()
                if 'Print length' in text or 'File size' in text:
                    m = re.search(r'([\d,]+)\s*pages', text, re.IGNORECASE)
                    if m and not result['pages']:
                        result['pages'] = int(m.group(1).replace(',', ''))
                elif 'Publication date' in text:
                    parts = text.split(':')
                    if len(parts) > 1:
                        result['publication_date'] = parts[-1].strip()

            if not result['pages'] or not result['publication_date']:
                rows = page.query_selector_all(
                    '#productDetails_detailBullets_sections1 tr, '
                    '#productDetails_techSpecs_section_1 tr'
                )
                for row in rows:
                    th = row.query_selector('th')
                    td = row.query_selector('td')
                    if not th or not td:
                        continue
                    label = th.inner_text().strip()
                    value = td.inner_text().strip()
                    if 'Print length' in label and not result['pages']:
                        m = re.search(r'([\d,]+)', value)
                        if m:
                            result['pages'] = int(m.group(1).replace(',', ''))
                    elif 'Publication date' in label and not result['publication_date']:
                        result['publication_date'] = value

        finally:
            browser.close()

    logger.info(
        'Amazon metadata scraped for ASIN %s: rating=%s, num_ratings=%s, '
        'pages=%s, publication_date=%r, series=%r',
        asin,
        result['rating'],
        result['num_ratings'],
        result['pages'],
        result['publication_date'],
        result['series'],
    )

    _AMAZON_CACHE[asin] = (result, now + AMAZON_CACHE_TTL)
    return result
