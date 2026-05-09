# 📚 Kitab — Azerbaijan National Library Downloader & OCR Converter

**Kitab** ("book" in Azerbaijani) is a pair of Python scripts for downloading books from the [Azerbaijan National Library](https://ek.anl.az/search/query?theme=e-kataloq) and optionally applying OCR to produce searchable PDFs.

| Script | Purpose |
|---|---|
| `kitab_cli.py` | Headless downloader — scriptable, emits JSON events |
| `kitab_interactive.py` | Interactive TUI — guided prompts, live progress, OCR wizard |

---

## Requirements

### Python packages
```bash
pip install requests pillow rich
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

---

## `kitab_cli.py`

This repo uses an improved fork of the original downloader. For full flag documentation, output structure, and the JSON event protocol, refer to the **[upstream docs at cavidaga/kitab](https://github.com/cavidaga/kitab)**.

Additional flags introduced in this fork:

| Flag | Default | Description |
|---|---|---|
| `-w`, `--workers` | `5` | Concurrent download workers |
| `-D`, `--delay` | `2` | Seconds to wait between each worker's page request |
| `-r`, `--retries` | `3` | Retry attempts per failed page (exponential back-off) |

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
5. **Live download progress** — spinner, progress bar, elapsed time (uses 5 concurrent workers)
6. **Apply OCR?** — yes/no
7. **Language preset** — 8 presets or a custom Tesseract language string
8. **OCR options** — deskew, clean, auto-rotate, PDF/A output
9. **Latin search layer** — optionally injects an invisible Latin transliteration over Cyrillic OCR text
10. **Summary table** — paths and file sizes for both PDFs
11. **Download another book?** — loop without restarting

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
# Interactive — pre-fill book ID and output directory
python3 kitab_interactive.py vtls000233649 -o ~/books

# Manual OCR after a previous download
ocrmypdf -l aze_cyrl+rus --deskew --clean \
    ~/books/book_233649/book_233649.pdf \
    ~/books/book_233649/book_233649_ocr.pdf
```


---

## Notes

- **PDF creation** — uses PyMuPDF (`fitz`) when available for faster, smaller PDFs with embedded metadata; falls back to Pillow automatically.
- **Concurrent downloads** — uses a thread pool (default 5 workers) to fetch multiple pages simultaneously, drastically reducing download time.
- **Auto-repair** — automatically detects and fixes truncated JPEGs caused by dropped connections, preventing `ocrmypdf` crashes later in the pipeline.
- **Latin search layer** — via `kitab_transliterate.py`, converts Azerbaijani Cyrillic text to modern Latin and injects it as a parallel invisible text layer into the same PDF.
- **Metadata** — the script attempts to fetch title, author, and date from the library catalogue. This request sometimes fails due to SSL issues on the library server; the PDF is still created successfully.
- **Resume support** — already-downloaded pages are detected by file size and skipped automatically, so interrupted downloads can be safely restarted.

---

## Credits

`kitab_cli.py` is based on the original work by **[@cavidaga](https://github.com/cavidaga/kitab)**. This repository uses an improved version with retry logic, configurable delay, PyMuPDF PDF creation, a richer JSON event protocol, and bug fixes.
