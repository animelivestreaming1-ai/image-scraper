import os
import re
import json
import time
import uuid
import base64
import logging
import requests
import threading
import cloudscraper
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, render_template, request, jsonify, send_file, abort
from bs4 import BeautifulSoup
from PIL import Image
import io

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TEMP_DIR = os.path.join(os.path.dirname(__file__), 'temp')
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'output')
os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

BROWSER_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Connection': 'keep-alive',
}

DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 3


def get_session(referer=None):
    session = cloudscraper.create_scraper()
    headers = dict(BROWSER_HEADERS)
    if referer:
        headers['Referer'] = referer
    session.headers.update(headers)
    return session


def fetch_response(session, method, url, headers=None, timeout=DEFAULT_TIMEOUT, retries=DEFAULT_RETRIES, allow_redirects=True, **kwargs):
    for attempt in range(1, retries + 1):
        try:
            request_headers = dict(headers or {})
            if 'Referer' in session.headers and 'Referer' not in request_headers:
                request_headers['Referer'] = session.headers['Referer']
            response = getattr(session, method)(
                url,
                headers=request_headers,
                timeout=timeout,
                allow_redirects=allow_redirects,
                **kwargs,
            )

            if response.status_code in (403, 429, 500, 502, 503, 504):
                raise requests.exceptions.HTTPError(
                    f'Retryable status code {response.status_code}', response=response
                )

            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as exc:
            if attempt == retries:
                raise
            wait = 2 * attempt
            logger.info(
                f'Request failed ({attempt}/{retries}) for {url}: {exc}. Retrying in {wait}s...'
            )
            time.sleep(wait)

SUPPORTED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}

jobs = {}
jobs_lock = threading.Lock()

PAGE_PATTERN = re.compile(r'(/p/)(\d+)(/?)', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def is_supported_image_url(url):
    path = urlparse(url).path.lower()
    ext = os.path.splitext(path)[1]
    return ext in SUPPORTED_EXTENSIONS


def extract_number_from_string(s):
    numbers = re.findall(r'\d+', s)
    return int(numbers[-1]) if numbers else 0


def get_page_title(soup, url):
    title_tag = soup.find('title')
    if title_tag and title_tag.string:
        title = re.sub(r'[\\/*?:"<>|]', '', title_tag.string.strip())
        title = re.sub(r'\s+', ' ', title).strip()[:80]
        if title:
            return title
    return urlparse(url).netloc or 'output'


def fetch_soup(session, url, timeout=20):
    resp = fetch_response(session, 'get', url, timeout=timeout)
    return BeautifulSoup(resp.text, 'html.parser'), resp.url, resp.text


# ---------------------------------------------------------------------------
# Image URL extraction from JavaScript (for JS-rendered readers)
# ---------------------------------------------------------------------------

def extract_images_from_scripts(html_text, base_url):
    """
    Search all <script> tags and inline JS for arrays of image URLs.
    Handles multiple patterns including base64-encoded JSON chapter data.
    Returns list of absolute image URLs in order, or empty list.
    """
    found_urls = []
    seen = set()

    # -----------------------------------------------------------------------
    # Pattern 0: Base64-encoded JSON in window.VARNAME = '...' (e.g. hentairead.com)
    # Decodes to JSON with structure: {data: {chapter: {images: [{src, width, height}]}}}
    # The baseUrl is stored separately in chapterExtraData.baseUrl
    # -----------------------------------------------------------------------
    b64_var_pattern = re.compile(r"window\.\w+\s*=\s*'([A-Za-z0-9+/\s]{50,})'", re.DOTALL)
    base_url_match = re.search(r'"baseUrl"\s*:\s*"([^"]+)"', html_text)
    cdn_base = base_url_match.group(1).rstrip('/') if base_url_match else ''

    for b64_match in b64_var_pattern.finditer(html_text):
        b64_raw = b64_match.group(1).strip().replace('\n', '').replace(' ', '')
        b64_raw += '=' * (-len(b64_raw) % 4)  # fix padding
        try:
            decoded = base64.b64decode(b64_raw).decode('utf-8')
            data = json.loads(decoded)
            # Navigate {data: {chapter: {images: [{src, width, height}]}}}
            chapter_data = data.get('data', data)
            chapter = chapter_data.get('chapter', {})
            images = chapter.get('images', [])
            if images and cdn_base:
                for img in images:
                    src = img.get('src', '')
                    if src:
                        full_url = cdn_base + '/' + src.lstrip('/')
                        if full_url not in seen:
                            seen.add(full_url)
                            found_urls.append(full_url)
                if found_urls:
                    logger.info(f"Found {len(found_urls)} images from base64 chapter data")
                    return found_urls
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Pattern 1: Plain JSON array of image URL strings
    # e.g. ["https://cdn.example.com/001.jpg", "...002.jpg"]
    # -----------------------------------------------------------------------
    array_pattern = re.compile(
        r'\[\s*(?:"[^"]*\.(?:jpg|jpeg|png|webp)[^"]*"\s*,?\s*)+\]',
        re.IGNORECASE
    )
    for match in array_pattern.finditer(html_text):
        try:
            arr = json.loads(match.group())
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, str) and is_supported_image_url(item):
                        abs_url = urljoin(base_url, item)
                        if abs_url not in seen:
                            seen.add(abs_url)
                            found_urls.append(abs_url)
        except (json.JSONDecodeError, Exception):
            pass
    if found_urls:
        logger.info(f"Found {len(found_urls)} image URLs in JS array")
        return found_urls

    # -----------------------------------------------------------------------
    # Pattern 2: JSON array of objects with url/src/image/path key
    # e.g. [{"url":"https://...jpg","width":800}]
    # -----------------------------------------------------------------------
    obj_array_pattern = re.compile(
        r'\[\s*\{[^[\]]{0,2000}\.(?:jpg|jpeg|png|webp)[^[\]]{0,2000}\}\s*\]',
        re.IGNORECASE | re.DOTALL
    )
    for match in obj_array_pattern.finditer(html_text):
        try:
            arr = json.loads(match.group())
            if isinstance(arr, list):
                for item in arr:
                    if isinstance(item, dict):
                        for key in ('url', 'src', 'image', 'path', 'file', 'img'):
                            val = item.get(key, '')
                            if val and is_supported_image_url(str(val)):
                                abs_url = urljoin(base_url, str(val))
                                if abs_url not in seen:
                                    seen.add(abs_url)
                                    found_urls.append(abs_url)
                                break
        except (json.JSONDecodeError, Exception):
            pass
    if found_urls:
        logger.info(f"Found {len(found_urls)} image URLs in JS object array")
        return found_urls

    # -----------------------------------------------------------------------
    # Pattern 3: Absolute image URLs in quotes anywhere in the script
    # -----------------------------------------------------------------------
    url_pattern = re.compile(
        r'"(https?://[^"]+\.(?:jpg|jpeg|png|webp)(?:\?[^"]*)?)"',
        re.IGNORECASE
    )
    for match in url_pattern.finditer(html_text):
        url = match.group(1)
        if url not in seen:
            seen.add(url)
            found_urls.append(url)
    if found_urls:
        logger.info(f"Found {len(found_urls)} image URLs via URL pattern search")
        return found_urls

    return []


def deduplicate_and_sort_urls(urls):
    """Remove duplicates while preserving order, then sort by trailing number."""
    seen = set()
    unique = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            unique.append(u)
    unique.sort(key=lambda u: extract_number_from_string(urlparse(u).path))
    return unique


# ---------------------------------------------------------------------------
# Paginated reader scraping
# ---------------------------------------------------------------------------

def detect_paginated(url):
    m = PAGE_PATTERN.search(url)
    if m:
        prefix = url[:m.start(1)]
        suffix = url[m.end(3):]
        current_page = int(m.group(2))
        return prefix, suffix, current_page
    return None


def build_page_url(prefix, suffix, page_num):
    return f"{prefix}/p/{page_num}/{suffix}".rstrip('/') + '/'


def find_total_pages(soup, html_text, prefix, suffix, session):
    # Strategy 1: <select> with page number options
    for sel in soup.find_all('select'):
        options = sel.find_all('option')
        if len(options) > 1:
            nums = [int(m.group()) for opt in options
                    for m in [re.search(r'\d+', opt.get('value', '') or opt.get_text())]
                    if m]
            if nums:
                return max(nums)

    # Strategy 2: Highest /p/N/ link on page
    max_page = 1
    for a in soup.find_all('a', href=True):
        m = PAGE_PATTERN.search(a['href'])
        if m:
            n = int(m.group(2))
            if n > max_page:
                max_page = n
    if max_page > 1:
        return max_page

    # Strategy 3: text like "1 / 50" or "of 50"
    text = soup.get_text()
    for pat in [r'(\d+)\s*/\s*(\d+)', r'of\s+(\d+)\s+pages?', r'total[:\s]+(\d+)']:
        for m in re.finditer(pat, text, re.IGNORECASE):
            nums = [int(x) for x in m.groups() if x and x.isdigit()]
            if nums and max(nums) > 1:
                max_page = max(max_page, max(nums))
    if max_page > 1:
        return max_page

    # Strategy 4: Search JS for total/count variables
    for pat in [r'"total"\s*:\s*(\d+)', r'"pages"\s*:\s*(\d+)', r'totalPages\s*=\s*(\d+)',
                r'page_count\s*=\s*(\d+)', r'"count"\s*:\s*(\d+)']:
        m = re.search(pat, html_text, re.IGNORECASE)
        if m:
            n = int(m.group(1))
            if n > 1:
                return n

    # Strategy 5: Probe by requesting pages until 404
    probe = 2
    while probe <= 256:
        test_url = build_page_url(prefix, suffix, probe)
        try:
            resp = fetch_response(session, 'head', test_url, timeout=8, allow_redirects=True)
            if resp.status_code >= 400:
                return probe - 1
            probe *= 2
        except Exception:
            return probe - 1
    return max_page


def scrape_paginated(url, job_id, session, site_origin):
    paginated = detect_paginated(url)
    prefix, suffix, _ = paginated

    with jobs_lock:
        jobs[job_id]['status'] = 'Fetching page 1 to detect structure...'

    page1_url = build_page_url(prefix, suffix, 1)
    soup1, final_url, html_text = fetch_soup(session, page1_url)
    page_title = get_page_title(soup1, page1_url)

    # Try to get ALL images from JavaScript on page 1 first (most efficient)
    js_images = extract_images_from_scripts(html_text, final_url)
    # Filter out UI/navigation images — keep only content images
    js_images = [u for u in js_images if _looks_like_content_image(u, site_origin)]

    if js_images:
        sorted_images = deduplicate_and_sort_urls(js_images)
        logger.info(f"Got {len(sorted_images)} images from JS on page 1 — skipping page iteration")
        with jobs_lock:
            jobs[job_id]['status'] = f'Found {len(sorted_images)} images in page data'
            jobs[job_id]['total'] = len(sorted_images)
            jobs[job_id]['completed'] = len(sorted_images)
        return sorted_images, page_title

    # Fallback: detect total pages and fetch each individually
    total = find_total_pages(soup1, html_text, prefix, suffix, session)
    logger.info(f"Detected {total} pages, will fetch each individually")

    with jobs_lock:
        jobs[job_id]['status'] = f'Found {total} pages. Fetching all pages...'
        jobs[job_id]['total'] = total
        jobs[job_id]['completed'] = 0

    page_image_map = {}
    completed = 0

    def fetch_page_image(page_num):
        purl = build_page_url(prefix, suffix, page_num)
        try:
            page_session = get_session(referer=site_origin)
            s, final, html = fetch_soup(page_session, purl)
            # Try JS extraction first on each page
            imgs = extract_images_from_scripts(html, final)
            imgs = [u for u in imgs if _looks_like_content_image(u, site_origin)]
            if imgs:
                return page_num, imgs[0]
            # Fallback: pick largest img tag
            img_url = pick_largest_img(s, final)
            return page_num, img_url
        except Exception as e:
            logger.warning(f"Failed page {page_num}: {e}")
            return page_num, None

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_page_image, n): n for n in range(1, total + 1)}
        for future in as_completed(futures):
            page_num, img_url = future.result()
            if img_url:
                page_image_map[page_num] = img_url
            completed += 1
            with jobs_lock:
                jobs[job_id]['completed'] = completed
                jobs[job_id]['status'] = f'Fetching pages... ({completed}/{total})'

    ordered = []
    seen = set()
    for n in sorted(page_image_map.keys()):
        u = page_image_map[n]
        if u and u not in seen:
            seen.add(u)
            ordered.append(u)

    logger.info(f"Collected {len(ordered)} images from {total} pages")
    return ordered, page_title


def _looks_like_content_image(url, site_origin):
    """Filter out known UI/theme/nav image paths."""
    path = urlparse(url).path.lower()
    blocklist = [
        '/themes/', '/theme/', '/assets/ui', '/icons/', '/logo',
        '/banner/', '/ads/', '/static/img/ui', 'favicon',
        'avatar', 'thumbnail', 'thumb', 'placeholder',
    ]
    for block in blocklist:
        if block in path:
            return False
    return True


def pick_largest_img(soup, page_url, min_dim=300):
    """Pick the visually largest <img> from the page by dimension hints."""
    candidates = []
    for img in soup.find_all('img'):
        src = None
        for attr in ['src', 'data-src', 'data-lazy-src', 'data-original', 'data-url']:
            val = (img.get(attr) or '').strip()
            if val and not val.startswith('data:'):
                src = val
                break
        if not src:
            continue
        abs_url = urljoin(page_url, src)
        if not is_supported_image_url(abs_url):
            continue
        try:
            w = int(img.get('width', 0) or 0)
            h = int(img.get('height', 0) or 0)
        except (ValueError, TypeError):
            w = h = 0
        candidates.append((w * h, w, h, abs_url))

    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][3]


# ---------------------------------------------------------------------------
# Single-page gallery scraper
# ---------------------------------------------------------------------------

def scrape_single_page(url, session, site_origin):
    logger.info(f"Gallery scrape: {url}")
    resp = fetch_response(session, 'get', url, timeout=15)
    soup = BeautifulSoup(resp.text, 'html.parser')
    page_title = get_page_title(soup, url)

    # Try JS extraction first
    js_images = extract_images_from_scripts(resp.text, url)
    js_images = [u for u in js_images if _looks_like_content_image(u, site_origin)]
    if js_images:
        return deduplicate_and_sort_urls(js_images), page_title

    # Fallback: img tags
    seen = set()
    valid_urls = []
    for img in soup.find_all('img'):
        for attr in ['src', 'data-src', 'data-lazy-src', 'data-original', 'data-url']:
            val = (img.get(attr) or '').strip()
            if val and not val.startswith('data:'):
                abs_url = urljoin(url, val)
                if is_supported_image_url(abs_url) and abs_url not in seen:
                    seen.add(abs_url)
                    valid_urls.append(abs_url)
                break

    valid_urls.sort(key=lambda u: extract_number_from_string(u))
    return valid_urls, page_title


# ---------------------------------------------------------------------------
# Image downloading
# ---------------------------------------------------------------------------

def download_image(img_url, index, temp_session_dir, site_referer):
    """Download one image with site Referer header set."""
    headers = dict(BROWSER_HEADERS)
    headers['Referer'] = site_referer
    headers['Accept'] = 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
    session = get_session(referer=site_referer)

    try:
        resp = fetch_response(
            session,
            'get',
            img_url,
            headers=headers,
            timeout=30,
            stream=True,
            allow_redirects=True,
        )

        raw = resp.content
        img = Image.open(io.BytesIO(raw))

        if img.mode in ('RGBA', 'LA', 'P'):
            bg = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            mask = img.split()[-1] if img.mode in ('RGBA', 'LA') else None
            bg.paste(img, mask=mask)
            img = bg
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        img_path = os.path.join(temp_session_dir, f'{index:04d}.jpg')
        img.save(img_path, 'JPEG', quality=90, optimize=True)
        logger.info(f"OK image {index}: {img_url}")
        return img_path

    except Exception as e:
        logger.warning(f"FAIL image {index} ({img_url}): {e}")
        return None


def download_images_parallel(image_urls, temp_session_dir, job_id, site_referer):
    total = len(image_urls)
    downloaded_paths = [None] * total
    completed = 0

    with jobs_lock:
        jobs[job_id]['total'] = total
        jobs[job_id]['completed'] = 0

    with ThreadPoolExecutor(max_workers=6) as executor:
        future_to_index = {
            executor.submit(download_image, url, i, temp_session_dir, site_referer): i
            for i, url in enumerate(image_urls)
        }
        for future in as_completed(future_to_index):
            idx = future_to_index[future]
            result = future.result()
            downloaded_paths[idx] = result
            completed += 1
            with jobs_lock:
                jobs[job_id]['completed'] = completed
                jobs[job_id]['status'] = f'Downloading images... ({completed}/{total})'

    return [p for p in downloaded_paths if p is not None]


# ---------------------------------------------------------------------------
# PDF builder
# ---------------------------------------------------------------------------

def build_pdf(image_paths, output_path):
    if not image_paths:
        raise ValueError("No images to build PDF from")

    images = []
    for path in image_paths:
        try:
            images.append(Image.open(path).convert('RGB'))
        except Exception as e:
            logger.warning(f"Skipping corrupt image {path}: {e}")

    if not images:
        raise ValueError("All images failed to load")

    images[0].save(
        output_path, 'PDF',
        save_all=True,
        append_images=images[1:],
        resolution=150,
        optimize=True,
    )
    logger.info(f"PDF saved: {output_path}")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup_temp(temp_session_dir):
    try:
        for f in os.listdir(temp_session_dir):
            os.remove(os.path.join(temp_session_dir, f))
        os.rmdir(temp_session_dir)
    except Exception as e:
        logger.warning(f"Cleanup error: {e}")


# ---------------------------------------------------------------------------
# Job runner
# ---------------------------------------------------------------------------

def run_job(job_id, url):
    temp_session_dir = os.path.join(TEMP_DIR, job_id)
    os.makedirs(temp_session_dir, exist_ok=True)

    parsed = urlparse(url)
    site_origin = f"{parsed.scheme}://{parsed.netloc}"
    # Use the manga page itself as referer when downloading images
    site_referer = url

    session = get_session(referer=site_origin)

    try:
        with jobs_lock:
            jobs[job_id]['status'] = 'Analyzing URL...'

        is_paginated = detect_paginated(url) is not None

        if is_paginated:
            image_urls, page_title = scrape_paginated(url, job_id, session, site_origin)
        else:
            with jobs_lock:
                jobs[job_id]['status'] = 'Scraping image URLs...'
            image_urls, page_title = scrape_single_page(url, session, site_origin)

        if not image_urls:
            with jobs_lock:
                jobs[job_id]['status'] = 'error'
                jobs[job_id]['error'] = (
                    'Tidak ada gambar yang ditemukan. '
                    'Coba URL halaman pertama (contoh: .../p/1/).'
                )
            return

        logger.info(f"Total images to download: {len(image_urls)}")
        with jobs_lock:
            jobs[job_id]['status'] = f'Downloading {len(image_urls)} images...'

        image_paths = download_images_parallel(
            image_urls, temp_session_dir, job_id, site_referer
        )

        if not image_paths:
            with jobs_lock:
                jobs[job_id]['status'] = 'error'
                jobs[job_id]['error'] = (
                    'Semua gambar gagal didownload. '
                    'Website mungkin memblokir akses langsung ke gambar.'
                )
            return

        with jobs_lock:
            jobs[job_id]['status'] = 'Building PDF...'

        safe_title = re.sub(r'[\\/*?:"<>|]', '', page_title)
        safe_title = re.sub(r'\s+', '_', safe_title.strip())[:60]
        pdf_filename = f"{safe_title}_{job_id[:8]}.pdf"
        output_path = os.path.join(OUTPUT_DIR, pdf_filename)

        build_pdf(image_paths, output_path)

        with jobs_lock:
            jobs[job_id]['status'] = 'done'
            jobs[job_id]['pdf_filename'] = pdf_filename
            jobs[job_id]['image_count'] = len(image_paths)

    except Exception as e:
        logger.error(f"Job {job_id} failed: {e}", exc_info=True)
        with jobs_lock:
            jobs[job_id]['status'] = 'error'
            jobs[job_id]['error'] = str(e)

    finally:
        cleanup_temp(temp_session_dir)


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/convert', methods=['POST'])
def convert():
    data = request.get_json()
    url = (data or {}).get('url', '').strip()
    if not url:
        return jsonify({'error': 'URL is required.'}), 400
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    job_id = str(uuid.uuid4())
    mode = 'paginated' if detect_paginated(url) else 'gallery'
    with jobs_lock:
        jobs[job_id] = {
            'status': 'starting', 'total': 0, 'completed': 0,
            'pdf_filename': None, 'error': None, 'image_count': 0, 'mode': mode,
        }

    threading.Thread(target=run_job, args=(job_id, url), daemon=True).start()
    return jsonify({'job_id': job_id, 'mode': mode})


@app.route('/status/<job_id>')
def status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None:
        return jsonify({'error': 'Job not found.'}), 404
    return jsonify(job)


@app.route('/download/<job_id>')
def download(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
    if job is None or job['status'] != 'done':
        abort(404)
    pdf_path = os.path.join(OUTPUT_DIR, job['pdf_filename'])
    if not os.path.exists(pdf_path):
        abort(404)
    return send_file(
        pdf_path, as_attachment=True,
        download_name=job['pdf_filename'],
        mimetype='application/pdf',
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
