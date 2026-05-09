#!/usr/bin/env python3
"""
kitab_interactive.py — Interactive TUI for book downloading and OCR.

Wraps kitab_cli.py (--json mode) for downloads and ocrmypdf for OCR.
Requires: rich  (pip install rich)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from kitab_transliterate import add_latin_search_layer
except ImportError:
    # Handle missing script gracefully
    add_latin_search_layer = None

try:
    from rich import box
    from rich.align import Align
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import (
        BarColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
    )
    from rich.prompt import Confirm, Prompt
    from rich.rule import Rule
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("Error: 'rich' is required.  pip install rich")
    sys.exit(1)

console = Console()

KITAB_CLI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kitab_cli.py")

OCR_LANGUAGE_PRESETS: dict[str, tuple[str, str]] = {
    "1": ("aze_cyrl+rus",       "Azerbaijani (Cyrillic) + Russian"),
    "2": ("aze+rus",            "Azerbaijani (Latin) + Russian"),
    "3": ("aze_cyrl",           "Azerbaijani (Cyrillic) only"),
    "4": ("aze",                "Azerbaijani (Latin) only"),
    "5": ("rus",                "Russian only"),
    "6": ("eng",                "English only"),
    "7": ("aze_cyrl+rus+eng",   "Azerbaijani (Cyrillic) + Russian + English"),
    "8": ("custom",             "Enter a custom language string…"),
}


# ── UI helpers ────────────────────────────────────────────────────────────────

def print_header() -> None:
    title    = Text("📚  K İ T A B", style="bold cyan", justify="center")
    subtitle = Text("Interactive Book Downloader & OCR Converter",
                    style="dim white", justify="center")
    console.print()
    console.print(Panel(
        Align.center(Text.assemble(title, "\n", subtitle)),
        border_style="cyan",
        padding=(1, 4),
    ))
    console.print()


def rule(label: str, style: str = "bold cyan") -> None:
    console.print(Rule(f"[{style}]{label}[/{style}]"))
    console.print()


# ── Validation ────────────────────────────────────────────────────────────────

def normalize_bibid(raw: str) -> str | None:
    raw = raw.strip()
    if raw.startswith("vtls"):
        digits = "".join(filter(str.isdigit, raw))
        return digits.lstrip("0") or None
    if raw.isdigit():
        return raw.lstrip("0") or raw
    return None


# ── Step 1: gather download params ───────────────────────────────────────────

def gather_download_params(prefill_bibid: str | None = None,
                           prefill_output: str | None = None) -> dict:
    rule("Download Settings")

    # Book ID
    while True:
        default_id = prefill_bibid or ""
        prompt_suffix = f" [dim](default: {prefill_bibid})[/dim]" if prefill_bibid else " [dim](e.g. vtls000233649 or 233649)[/dim]"
        raw = Prompt.ask(f"  [bold]Book ID[/bold]{prompt_suffix}",
                         default=prefill_bibid or "")
        bibid = normalize_bibid(raw)
        if bibid:
            console.print(f"  [green]✓[/green] Normalized ID: [cyan]{bibid}[/cyan]\n")
            break
        console.print("  [red]✗[/red] Invalid ID — must be numeric or start with 'vtls'.\n")

    # Output directory
    default_out = prefill_output or str(Path.home() / "books")
    while True:
        output_dir = os.path.expanduser(
            Prompt.ask("  [bold]Output directory[/bold]", default=default_out)
        )
        # Validate writable
        try:
            os.makedirs(output_dir, exist_ok=True)
            test_file = os.path.join(output_dir, ".kitab_write_test")
            with open(test_file, "w") as f:
                f.write("")
            os.remove(test_file)
            console.print(f"  [green]✓[/green] Output directory is writable.\n")
            break
        except OSError as e:
            console.print(f"  [red]✗[/red] Cannot write to [cyan]{output_dir}[/cyan]: {e}\n"
                          "  Please choose a different directory.\n")
            default_out = default_out  # re-prompt

    # Page range
    console.print("  [dim]Leave end blank to download all pages.[/dim]")
    start_raw = Prompt.ask("  [bold]Start page[/bold]", default="1")
    end_raw   = Prompt.ask("  [bold]End page[/bold]  [dim](blank = last)[/dim]", default="")

    start_page = int(start_raw) if start_raw.strip().isdigit() else 1
    end_page   = int(end_raw)   if end_raw.strip().isdigit()   else None

    delete_images = Confirm.ask(
        "\n  [bold]Delete source images after PDF creation?[/bold]", default=False
    )
    console.print()

    return {
        "bibid":         bibid,
        "bibid_raw":     raw.strip(),
        "output_dir":    output_dir,
        "start_page":    start_page,
        "end_page":      end_page,
        "delete_images": delete_images,
    }


# ── Step 2: download ──────────────────────────────────────────────────────────

def _check_existing_book(bibid: str, output_dir: str) -> str | None:
    """
    Inspect output_dir/book_{bibid}/ and return one of:
      - the existing PDF path  (if PDF present)
      - None                   (continue with download)

    Prompts the user when relevant content already exists.
    """
    book_dir = os.path.join(output_dir, f"book_{bibid}")
    pdf_path = os.path.join(book_dir, f"book_{bibid}.pdf")

    # ── Case 1: finished PDF already present ─────────────────────────────────
    if os.path.exists(pdf_path):
        size_mb = os.path.getsize(pdf_path) / 1_048_576
        console.print(
            f"\n  [yellow]⚠[/yellow]  A PDF for book [cyan]{bibid}[/cyan] already exists:\n"
            f"  [dim]{pdf_path}[/dim]  ({size_mb:.1f} MB)\n"
        )
        if Confirm.ask("  [bold]Skip download and use the existing PDF?[/bold]", default=True):
            console.print(f"  [green]✓[/green] Using existing PDF.\n")
            return pdf_path
        # User chose to re-download — fall through
        console.print("  Proceeding with download (existing pages will be reused).\n")
        return None

    # ── Case 2: page images exist but no PDF yet ──────────────────────────────
    if os.path.isdir(book_dir):
        cached = [
            f for f in Path(book_dir).glob("page_*.jpg")
            if f.stat().st_size >= 1024
        ]
        if cached:
            console.print(
                f"\n  [yellow]⚠[/yellow]  [cyan]{len(cached)}[/cyan] page image(s) already cached "
                f"in [dim]{book_dir}[/dim].\n"
                "  [dim]kitab_cli will skip already-downloaded pages automatically.[/dim]\n"
            )
            if not Confirm.ask("  [bold]Continue download (resume)?[/bold]", default=True):
                console.print("  [dim]Download skipped by user.[/dim]\n")
                return ""   # empty string = user cancelled, no PDF
    return None


def run_download(params: dict) -> str | None:
    bibid      = params["bibid"]
    output_dir = params["output_dir"]

    # Pre-flight: check for existing content
    existing = _check_existing_book(bibid, output_dir)
    if existing is not None:
        # "" means user cancelled; any other string is an existing PDF path
        return existing if existing else None

    cmd = [sys.executable, KITAB_CLI, params["bibid_raw"],
           "-o", output_dir, "--json", "-w", "5",
           "-s", str(params["start_page"])]

    if params["end_page"]:
        cmd += ["-e", str(params["end_page"])]
    if params["delete_images"]:
        cmd.append("-d")

    rule("Downloading")
    messages: list[str] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        TextColumn("[dim]{task.fields[status]}"),
        TimeElapsedColumn(),
        console=console,
        transient=False,
    ) as progress:
        task = progress.add_task("  Connecting…", total=None, status="")
        pdf_from_event: str | None = None  # captured from the pdf event

        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )
            for line in proc.stdout:          # type: ignore[union-attr]
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue

                t = ev.get("type")
                if t == "total":
                    progress.update(task,
                                    total=ev["total"],
                                    description=f"  Downloading {ev['total']} pages…")
                elif t == "progress":
                    progress.update(task,
                                    completed=ev["page"],
                                    status=f"page {ev['page']}/{ev['total']}")
                elif t == "skipped":
                    progress.update(task, advance=1,
                                    status=f"page {ev['page']} (cached)")
                elif t == "failed":
                    messages.append(f"[yellow]⚠[/yellow]  Page {ev['page']} failed")
                elif t == "pdf":
                    pdf_from_event = ev.get("path")
                elif t == "log":
                    msg, level = ev.get("msg", ""), ev.get("level", "info")
                    if level == "success":
                        messages.append(f"[green]✓[/green]  {msg}")
                    elif level in ("warning", "error"):
                        messages.append(f"[yellow]⚠[/yellow]  {msg}")
                    else:
                        progress.update(task, status=msg[:60])
                elif t == "done":
                    failed_count = ev.get("failed", 0)
                    dl_count     = ev.get("downloaded", "?")
                    status_str   = f"[green]done ({dl_count} pages)[/green]"
                    if failed_count:
                        status_str += f" [yellow]({failed_count} failed)[/yellow]"
                    progress.update(task, status=status_str)
            proc.wait()
        except Exception as e:
            console.print(f"\n  [red]✗ Download error:[/red] {e}")
            return None

    console.print()
    for m in messages:
        console.print(f"  {m}")

    # Resolve PDF path: prefer the event-reported path, fall back to heuristic
    pdf_path: str | None = pdf_from_event
    if not pdf_path or not os.path.exists(pdf_path):
        fallback = os.path.join(output_dir, f"book_{bibid}", f"book_{bibid}.pdf")
        if os.path.exists(fallback):
            pdf_path = fallback
        else:
            book_dir = os.path.join(output_dir, f"book_{bibid}")
            found = list(Path(book_dir).glob("*.pdf")) if os.path.isdir(book_dir) else []
            pdf_path = str(found[0]) if found else None

    if pdf_path and os.path.exists(pdf_path):
        size_mb = os.path.getsize(pdf_path) / 1_048_576
        console.print(f"\n  [green]✓[/green] PDF ready: [cyan]{pdf_path}[/cyan] "
                      f"[dim]({size_mb:.1f} MB)[/dim]")
        return pdf_path

    console.print("\n  [red]✗[/red] PDF not found — check above for errors.")
    return None


# ── Step 3: gather OCR params ─────────────────────────────────────────────────

def gather_ocr_params(input_pdf: str) -> dict | None:
    console.print()
    rule("OCR Settings", "bold magenta")

    if not Confirm.ask("  [bold]Apply OCR to the downloaded PDF?[/bold]", default=True):
        return None

    # Language preset table
    console.print("\n  [bold]OCR Language Presets[/bold]")
    tbl = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    tbl.add_column("Key",  style="cyan bold",  width=4)
    tbl.add_column("Name", style="white")
    tbl.add_column("Code", style="dim")
    for key, (code, label) in OCR_LANGUAGE_PRESETS.items():
        tbl.add_row(f"[{key}]", label, f"({code})")
    console.print(tbl)

    choice = Prompt.ask("  [bold]Select preset[/bold]", default="1")
    if choice in OCR_LANGUAGE_PRESETS:
        lang_code, _ = OCR_LANGUAGE_PRESETS[choice]
        if lang_code == "custom":
            lang_code = Prompt.ask(
                "  [bold]Custom language string[/bold] [dim](e.g. aze_cyrl+rus+eng)[/dim]"
            )
    else:
        lang_code = choice  # treat raw input as a tesseract language string

    console.print(f"  [green]✓[/green] Language: [cyan]{lang_code}[/cyan]\n")

    # Output path
    stem       = Path(input_pdf).stem
    default_out = os.path.join(os.path.dirname(input_pdf), f"{stem}_ocr.pdf")
    output_pdf  = os.path.expanduser(
        Prompt.ask("  [bold]Output PDF path[/bold]", default=default_out)
    )

    # Toggles
    console.print()
    deskew       = Confirm.ask("  [bold]Deskew pages?[/bold]      [dim](fix page tilt)[/dim]",     default=True)
    clean        = Confirm.ask("  [bold]Clean pages?[/bold]       [dim](remove noise)[/dim]",       default=True)
    rotate       = Confirm.ask("  [bold]Auto-rotate pages?[/bold] [dim](fix orientation)[/dim]",    default=False)
    pdfa         = Confirm.ask("  [bold]Output as PDF/A?[/bold]   [dim](archival format)[/dim]",    default=True)
    console.print()

    return {
        "lang":       lang_code,
        "input_pdf":  input_pdf,
        "output_pdf": output_pdf,
        "deskew":     deskew,
        "clean":      clean,
        "rotate":     rotate,
        "pdfa":       pdfa,
    }


# ── Step 4: run OCR ───────────────────────────────────────────────────────────

def run_ocr(params: dict) -> bool:
    cmd = ["ocrmypdf", "-l", params["lang"]]
    if params["deskew"]:
        cmd.append("--deskew")
    if params["clean"]:
        cmd.append("--clean")
    if params["rotate"]:
        cmd.append("--rotate-pages")
    if not params["pdfa"]:
        cmd += ["--output-type", "pdf"]
    cmd += [params["input_pdf"], params["output_pdf"]]

    rule("Running OCR", "bold magenta")
    console.print(f"  [dim]$ {' '.join(cmd)}[/dim]\n")

    try:
        # Let ocrmypdf inherit the real terminal so its own Rich progress bars
        # render correctly. Piping stdout/stderr makes Rich think it's not a TTY
        # and suppresses the animated progress output.
        proc = subprocess.Popen(cmd)
        proc.wait()
    except FileNotFoundError:
        console.print("  [red]✗[/red] [bold]ocrmypdf[/bold] not found.\n"
                      "  Install with:  [dim]pip install ocrmypdf[/dim]")
        return False
    except Exception as e:
        console.print(f"  [red]✗ OCR error:[/red] {e}")
        return False

    if proc.returncode == 0:
        console.print(f"\n  [green]✓[/green] OCR complete → [cyan]{params['output_pdf']}[/cyan]")
        return True
    console.print(f"\n  [red]✗[/red] ocrmypdf exited with code {proc.returncode}")
    return False


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(
    dl: dict,
    pdf_path: str | None,
    ocr: dict | None,
    ocr_ok: bool,
    latin_added: bool = False,
) -> None:
    console.print()
    rule("Summary", "bold green")

    tbl = Table(box=box.ROUNDED, show_header=False, padding=(0, 2), border_style="green")
    tbl.add_column("Key",   style="bold dim", width=22)
    tbl.add_column("Value", style="white")

    tbl.add_row("Book ID",          f"[cyan]{dl['bibid_raw']}[/cyan]")
    tbl.add_row("Output directory", dl["output_dir"])

    if pdf_path and os.path.exists(pdf_path):
        size_mb = os.path.getsize(pdf_path) / 1_048_576
        tbl.add_row("Downloaded PDF", f"[green]{pdf_path}[/green]  [dim]{size_mb:.1f} MB[/dim]")
    elif pdf_path:
        tbl.add_row("Downloaded PDF", f"[yellow]{pdf_path}[/yellow]  [dim](file missing)[/dim]")
    else:
        tbl.add_row("Downloaded PDF", "[red]failed[/red]")

    if ocr:
        status = "[green]✓ done[/green]" if ocr_ok else "[red]✗ failed[/red]"
        ocr_size = ""
        if ocr_ok and os.path.exists(ocr["output_pdf"]):
            ocr_mb = os.path.getsize(ocr["output_pdf"]) / 1_048_576
            ocr_size = f"  [dim]{ocr_mb:.1f} MB[/dim]"
        tbl.add_row("OCR output",   f"{ocr['output_pdf']}{ocr_size}  {status}")
        tbl.add_row("OCR language", ocr["lang"])
        
        if latin_added:
            tbl.add_row("Latin search", "[green]✓ added (invisible layer)[/green]")
        elif ocr_ok and add_latin_search_layer is not None:
            tbl.add_row("Latin search", "[dim]skipped[/dim]")
    else:
        tbl.add_row("OCR", "[dim]skipped[/dim]")

    console.print(tbl)
    console.print()


# ── Entry point ───────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="kitab_interactive",
        description="Interactive book downloader & OCR converter.",
    )
    parser.add_argument(
        "bibid", nargs="?", default=None,
        help="Book ID to pre-fill (e.g. vtls000233649 or 233649). "
             "Still asks for confirmation and all other options interactively.",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="Pre-fill the output directory prompt.",
    )
    return parser.parse_args()


def run_once(prefill_bibid: str | None, prefill_output: str | None) -> None:
    """Run one full download + OCR cycle."""
    dl_params = gather_download_params(prefill_bibid, prefill_output)
    pdf_path  = run_download(dl_params)

    ocr_params: dict | None = None
    ocr_ok = False
    latin_added = False

    if pdf_path:
        ocr_params = gather_ocr_params(pdf_path)
        if ocr_params:
            ocr_ok = run_ocr(ocr_params)
            
            if ocr_ok and add_latin_search_layer is not None:
                console.print()
                if Confirm.ask("  [bold]Add Latin search layer?[/bold] [dim](makes Cyrillic text searchable via Latin)[/dim]", default=True):
                    
                    with Progress(
                        SpinnerColumn(),
                        TextColumn("[bold cyan]Transliterating…"),
                        BarColumn(bar_width=40),
                        TaskProgressColumn(),
                        TextColumn("[dim]{task.fields[status]}"),
                        console=console,
                        transient=False,
                    ) as progress:
                        task = progress.add_task("  Processing", total=100, status="")
                        
                        def update_progress(current, total):
                            progress.update(task, completed=current, total=total, status=f"page {current}/{total}")
                            
                        success = add_latin_search_layer(ocr_params["output_pdf"], progress_callback=update_progress)

                    if success:
                        console.print("  [green]✓[/green] Latin search layer added successfully.")
                        latin_added = True
                    else:
                        console.print("  [red]✗[/red] Failed to add Latin search layer.")
    else:
        console.print("\n  [yellow]⚠[/yellow]  No PDF produced — skipping OCR.\n")

    print_summary(dl_params, pdf_path, ocr_params, ocr_ok, latin_added)


def main() -> None:
    args = parse_args()
    print_header()

    try:
        # First run — use CLI pre-fills if provided
        run_once(args.bibid, args.output)

        # Loop: offer to download another book
        while Confirm.ask("  [bold]Download another book?[/bold]", default=False):
            console.print()
            run_once(None, args.output)  # no pre-fill on subsequent runs

        console.print("  [dim]Goodbye.[/dim]\n")

    except KeyboardInterrupt:
        console.print("\n\n  [yellow]Interrupted.[/yellow]\n")
        sys.exit(0)


if __name__ == "__main__":
    main()
