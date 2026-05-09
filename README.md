# 📚 Kitab — Azerbaijan National Library Downloader & OCR Converter

**Kitab** (کتاب — "book" in Azerbaijani) is a pair of Python scripts for downloading books from the [Azerbaijan National Library](http://web2.anl.az:81/read) and optionally applying OCR to produce searchable PDFs.

| Script | Purpose |
|---|---|
| `kitab_cli.py` | Headless downloader — scriptable, emits JSON events |
| `kitab_interactive.py` | Interactive TUI — guided prompts, live progress, OCR wizard |

---

## Requirements

### Python packages
```bash
pip install requests Pillow rich
# Optional but recommended — faster PDF creation and metadata support:
pip install pymupdf
```

### System tools
- **[ocrmypdf](https://ocrmypdf.readthedocs.io/)** — required only if you want OCR
- **[Tesseract](https://github.com/tesseract-ocr/tesseract)** with relevant language packs

```bash
# Debian / Ubuntu
sudo apt install ocrmypdf tesseract-ocr tesseract-ocr-aze tesseract-ocr-rus
```

---

## Quick start

### Interactive mode (recommended)
```bash
python3 kitab_interactive.py
```

Pre-fill the book ID and/or output directory to skip the first prompt:
```bash
python3 kitab_interactive.py vtls000233649
python3 kitab_interactive.py vtls000233649 -o ~/books
```

### Headless / scripting mode
```bash
python3 kitab_cli.py vtls000233649 -o ~/books -s 1 -e 60 -d
```

---

## `kitab_cli.py` reference

```
usage: kitab_cli.py [-h] [-o OUTPUT] [-s START] [-e END] [-d] [-D DELAY] [-r RETRIES] [--json] bibid
```

| Flag | Default | Description |
|---|---|---|
| `bibid` | *(required)* | Book ID — numeric (`233649`) or `vtls` form (`vtls000233649`) |
| `-o`, `--output` | `.` | Output directory |
| `-s`, `--start` | `1` | First page to download |
| `-e`, `--end` | *(last)* | Last page to download |
| `-d`, `--delete` | off | Delete source images after PDF is created |
| `-D`, `--delay` | `2` | Seconds to wait between page requests |
| `-r`, `--retries` | `3` | Retry attempts per failed page (exponential back-off) |
| `--json` | off | Emit newline-delimited JSON events instead of plain text |

### Output structure
```
~/books/
└── book_233649/
    ├── page_1.jpg … page_60.jpg   ← deleted if -d is used
    └── book_233649.pdf
```

### JSON event protocol (`--json`)

Each line on stdout is a JSON object. Useful for driving an Electron UI or other host.

| Event | Fields |
|---|---|
| `total` | `total` — number of pages to download |
| `progress` | `page`, `total` |
| `skipped` | `page` — already downloaded, cache hit |
| `failed` | `page` |
| `pdf` | `path` — absolute path to the created PDF |
| `log` | `level` (`info`/`warning`/`success`/`error`), `msg` |
| `done` | `downloaded`, `failed` |

---

## `kitab_interactive.py` reference

```
usage: kitab_interactive.py [-h] [-o OUTPUT] [bibid]
```

| Argument | Description |
|---|---|
| `bibid` | *(optional)* Pre-fill the Book ID prompt |
| `-o`, `--output` | Pre-fill the output directory prompt |

The script walks you through:

1. **Book ID** — validates and normalises the ID
2. **Output directory** — verifies it is writable before proceeding
3. **Page range** — start / end page (blank = all pages)
4. **Delete images?** — clean up JPEGs after PDF creation
5. **Live download progress** — spinner, progress bar, elapsed time
6. **Apply OCR?** — yes/no
7. **Language preset** — 8 presets or a custom Tesseract language string
8. **OCR options** — deskew, clean, auto-rotate, PDF/A output
9. **Summary table** — paths and file sizes for both PDFs
10. **Download another book?** — loop without restarting

### OCR language presets

| # | Code | Description |
|---|---|---|
| 1 | `aze_cyrl+rus` | Azerbaijani (Cyrillic) + Russian *(default)* |
| 2 | `aze+rus` | Azerbaijani (Latin) + Russian |
| 3 | `aze_cyrl` | Azerbaijani (Cyrillic) only |
| 4 | `aze` | Azerbaijani (Latin) only |
| 5 | `rus` | Russian only |
| 6 | `eng` | English only |
| 7 | `aze_cyrl+rus+eng` | Azerbaijani (Cyrillic) + Russian + English |
| 8 | *(custom)* | Enter any Tesseract language string |

---

## Examples

```bash
# Download pages 1–60, delete JPEGs, emit JSON for scripting
python3 kitab_cli.py vtls000233649 -o ~/books -s 1 -e 60 -d --json

# Faster (1 s delay) with more retries on a flaky connection
python3 kitab_cli.py 233649 -o ~/books -D 1 -r 5

# Manual OCR after a previous download
ocrmypdf -l aze_cyrl+rus --deskew --clean \
    ~/books/book_233649/book_233649.pdf \
    ~/books/book_233649/book_233649_ocr.pdf
```

---

## Notes

- **PDF creation** — uses PyMuPDF (`fitz`) when available for faster, smaller PDFs with embedded metadata; falls back to Pillow automatically.
- **Metadata** — the script attempts to fetch title, author, and date from the library catalogue. This request sometimes fails due to SSL issues on the library server; the PDF is still created successfully.
- **Resume support** — already-downloaded pages are detected by file size and skipped automatically, so interrupted downloads can be safely restarted.
- **Rate limiting** — a configurable delay (default 2 s) is inserted between page requests to avoid overloading the server.

---

## Credits

`kitab_cli.py` is based on the original work by **[@cavidaga](https://github.com/cavidaga/kitab)**. This repository uses an improved version with retry logic, configurable delay, PyMuPDF PDF creation, a richer JSON event protocol, and bug fixes.
