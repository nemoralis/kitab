#!/usr/bin/env python3
"""
kitab_cli.py — Headless downloader for the Azerbaijan National Library.

Supports a --json flag that switches all progress output to newline-delimited
JSON so Electron (or any other host) can parse structured events.

JSON event types emitted on stdout:
  {"type":"total",    "total": <int>}
  {"type":"progress", "page":  <int>, "total": <int>}
  {"type":"skipped",  "page":  <int>}
  {"type":"failed",   "page":  <int>}
  {"type":"pdf",      "path":  "<str>"}
  {"type":"log",      "level": "info|warning|success|error", "msg": "<str>"}
  {"type":"done",     "downloaded": <int>, "failed": <int>}
"""
import os
import sys
import time
import json
import requests
import urllib3
from PIL import Image
try:
    import fitz
except ImportError:
    fitz = None
import re
import argparse

# The ANL metadata server uses a self-signed / untrusted certificate.
# Suppress the warning so it doesn't pollute --json output.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "http://web2.anl.az:81/read"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
    "Referer": BASE_URL,
}
DELAY = 2
RETRY_COUNT = 3
MIN_IMAGE_SIZE = 1024

# ── Output helpers ────────────────────────────────────────────────────────────
_json_mode = False

def emit(obj):
    """Emit a JSON event line (--json mode) or plain text."""
    if _json_mode:
        print(json.dumps(obj, ensure_ascii=False), flush=True)
    else:
        msg = obj.get("msg", "")
        if not msg:
            # Synthesise a human-readable line from non-log events
            t = obj.get("type", "")
            if t == "total":
                msg = f"Total pages: {obj['total']}"
            elif t == "progress":
                msg = f"Page {obj['page']} downloaded."
            elif t == "skipped":
                msg = f"Page {obj['page']} already exists. Skipping..."
            elif t == "failed":
                msg = f"Page {obj['page']} failed."
            elif t == "pdf":
                msg = f"PDF saved: {obj['path']}"
            elif t == "done":
                dl, fl = obj.get('downloaded', '?'), obj.get('failed', 0)
                msg = f"Download complete! Pages downloaded: {dl}, failed: {fl}"
        if msg:
            print(msg, flush=True)

def log(msg, level="info"):
    emit({"type": "log", "level": level, "msg": msg})

# ── Core functions ────────────────────────────────────────────────────────────
def normalize_bibid(bibid):
    if bibid.startswith("vtls"):
        numeric_part = ''.join(filter(str.isdigit, bibid))
        if numeric_part:
            return numeric_part.lstrip("0")
    elif bibid.isdigit():
        return bibid.lstrip("0")
    raise ValueError("Invalid Book ID format. Must be numeric or start with 'vtls'.")

def get_total_pages(bibid):
    page_url = f"{BASE_URL}/page.php?bibid={bibid}&pno=1"
    response = requests.get(page_url, headers=HEADERS, timeout=10)
    response.raise_for_status()
    match = re.search(r'last_page_params="\?bibid=\d+&pno=(\d+)"', response.text)
    if match:
        return int(match.group(1))
    raise ValueError("Failed to fetch the total number of pages.")

def download_page(session, bibid, page_no, output_file, delay=DELAY, retries=RETRY_COUNT):
    preload_url = f"{BASE_URL}/page.php?bibid={bibid}&pno={page_no}"
    image_url   = f"{BASE_URL}/img.php?bibid={bibid}&pno={page_no}"
    last_error  = None
    for attempt in range(1, retries + 1):
        try:
            session.get(preload_url, headers=HEADERS, timeout=10).raise_for_status()
            time.sleep(delay)
            response = session.get(image_url, headers=HEADERS, stream=True, timeout=10)
            response.raise_for_status()
            with open(output_file, "wb") as f:
                f.write(response.content)
            if os.path.getsize(output_file) < MIN_IMAGE_SIZE:
                raise ValueError("File size too small. The image may be invalid.")
            return True
        except Exception as e:
            last_error = e
            if attempt < retries:
                log(f"Page {page_no}: attempt {attempt} failed ({e}), retrying…", "warning")
                time.sleep(delay * attempt)  # back-off
    log(f"Failed to download page {page_no} after {retries} attempts: {last_error}", "warning")
    return False

def fetch_metadata(bibid):
    url = f"https://ek.anl.az/lib/item?id=chamo:{bibid}&theme=e-kataloq"
    try:
        # verify=False — the library's cert cannot be validated; metadata is non-sensitive
        response = requests.get(url, timeout=10, verify=False)
        if response.status_code == 200:
            html = response.text
            metadata = {}
            title_match = re.search(r'<h1 class="title">(.*?)</h1>', html, re.DOTALL | re.IGNORECASE)
            if title_match:
                metadata['title'] = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()
            author_match = re.search(r'class="author">(.*?)</a>', html, re.DOTALL | re.IGNORECASE)
            if author_match:
                metadata['author'] = re.sub(r'<[^>]+>', '', author_match.group(1)).strip()
            date_match = re.search(r'\$c\s+(\d{4})\s*</div>', html)
            if date_match:
                metadata['creationDate'] = date_match.group(1).strip()
            return metadata
    except Exception as e:
        log(f"Metadata fetch failed: {e}", "warning")
    return {}

def create_pdf(book_dir, bibid, start_page, end_page, delete_images):
    pdf_path = os.path.join(book_dir, f"book_{bibid}.pdf")
    image_files = [
        os.path.join(book_dir, f"page_{i}.jpg")
        for i in range(start_page, end_page + 1)
        if os.path.exists(os.path.join(book_dir, f"page_{i}.jpg"))
    ]

    if not image_files:
        log("No valid images to create a PDF.", "error")
        return

    meta = fetch_metadata(bibid)

    use_fitz = fitz is not None
    if use_fitz:
        # ── Fast path: build PDF with PyMuPDF ────────────────────────────────
        try:
            doc = fitz.open()
            for img_file in image_files:
                try:
                    img_doc = fitz.open(img_file)
                    pdfbytes = img_doc.convert_to_pdf()
                    img_doc.close()
                    img_pdf = fitz.open("pdf", pdfbytes)
                    doc.insert_pdf(img_pdf)
                    img_pdf.close()
                except Exception as e:
                    log(f"Skipping image {img_file}: {e}", "warning")
            if meta:
                doc.set_metadata(meta)
                log(f"Applied metadata — Title: {meta.get('title', 'Unknown')}", "info")
            doc.save(pdf_path, garbage=4, deflate=True)
            doc.close()
        except Exception as e:
            log(f"PyMuPDF PDF creation failed, falling back to Pillow: {e}", "warning")
            use_fitz = False  # fall through to Pillow path below

    if not use_fitz:
        # ── Fallback path: build PDF with Pillow ──────────────────────────────
        images = []
        for img_file in image_files:
            try:
                img = Image.open(img_file)
                if img.mode != "RGB":
                    img = img.convert("RGB")
                images.append(img)
            except Exception as e:
                log(f"Skipping image {img_file}: {e}", "warning")
        if not images:
            log("No valid images to create a PDF.", "error")
            return
        images[0].save(pdf_path, save_all=True, append_images=images[1:])

    log(f"PDF saved: {pdf_path}", "success")
    emit({"type": "pdf", "path": pdf_path})

    if delete_images:
        for img_file in image_files:
            try:
                os.remove(img_file)
            except Exception as e:
                log(f"Failed to delete {img_file}: {e}", "warning")

def download_book(bibid, output_dir, delete_images, start_page=1, end_page=None,
                  delay=DELAY, retries=RETRY_COUNT):
    book_dir = os.path.join(output_dir, f"book_{bibid}")
    os.makedirs(book_dir, exist_ok=True)

    log(f"Fetching info for book ID: {bibid}…", "info")
    try:
        total_pages = get_total_pages(bibid)
    except Exception as e:
        log(f"Error fetching total pages: {e}", "error")
        return False

    if end_page is None or end_page > total_pages:
        end_page = total_pages
    if start_page < 1 or start_page > total_pages:
        start_page = 1
    if end_page < start_page:
        end_page = start_page

    emit({"type": "total", "total": end_page - start_page + 1,
          "msg": f"Downloading pages {start_page}–{end_page} of {total_pages}."})

    pages_downloaded = 0
    pages_failed = 0

    with requests.Session() as session:
        session.headers.update(HEADERS)
        for page_no in range(start_page, end_page + 1):
            output_file = os.path.join(book_dir, f"page_{page_no}.jpg")

            if os.path.exists(output_file) and os.path.getsize(output_file) >= MIN_IMAGE_SIZE:
                pages_downloaded += 1
                emit({"type": "skipped",  "page": page_no})          # ← actual page_no, not counter
                emit({"type": "progress", "page": pages_downloaded,
                      "total": end_page - start_page + 1})
                continue

            if download_page(session, bibid, page_no, output_file, delay=delay, retries=retries):
                pages_downloaded += 1
                emit({"type": "progress", "page": pages_downloaded,
                      "total": end_page - start_page + 1,
                      "msg": f"Page {page_no} downloaded successfully.", "level": "success"})
            else:
                pages_failed += 1
                emit({"type": "failed", "page": page_no,
                      "msg": f"Page {page_no} failed.", "level": "warning"})

    log("Combining images into a PDF…", "info")
    create_pdf(book_dir, bibid, start_page, end_page, delete_images)
    emit({"type": "done", "downloaded": pages_downloaded, "failed": pages_failed,
          "msg": f"Download complete! Pages downloaded: {pages_downloaded}, failed: {pages_failed}",
          "level": "success" if pages_failed == 0 else "warning"})
    return True

# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    global _json_mode

    parser = argparse.ArgumentParser(description="Kitab — Azerbaijan National Library Downloader")
    parser.add_argument("bibid",               help="Book ID (e.g. vtls000000004 or 4)")
    parser.add_argument("-o", "--output",       default=".", help="Output directory (default: current directory)")
    parser.add_argument("-s", "--start",        type=int, default=1,    help="Start page (default: 1)")
    parser.add_argument("-e", "--end",          type=int, default=None, help="End page (default: last page)")
    parser.add_argument("-d", "--delete",       action="store_true",    help="Delete images after PDF creation")
    parser.add_argument("-D", "--delay",        type=float, default=DELAY,        help=f"Seconds between page requests (default: {DELAY})")
    parser.add_argument("-r", "--retries",      type=int,   default=RETRY_COUNT,  help=f"Retry attempts per page (default: {RETRY_COUNT})")
    parser.add_argument("--json",               action="store_true",    help="Emit newline-delimited JSON events")

    args = parser.parse_args()
    _json_mode = args.json

    if fitz is None and _json_mode:
        log("PyMuPDF not found. PDF metadata will not be applied.", "warning")

    try:
        bibid = normalize_bibid(args.bibid)
    except ValueError as err:
        log(str(err), "error")
        sys.exit(1)

    download_book(
        bibid=bibid,
        output_dir=args.output,
        delete_images=args.delete,
        start_page=args.start,
        end_page=args.end,
        delay=args.delay,
        retries=args.retries,
    )

if __name__ == "__main__":
    main()
