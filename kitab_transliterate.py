#!/usr/bin/env python3
"""
kitab_transliterate.py — Adds a Latin search layer to Azerbaijani Cyrillic PDFs.

Iterates over text spans in an OCR'd PDF, transliterates Cyrillic text to Latin,
and inserts the Latin text as an invisible layer (render_mode=3) directly over
the original text, saving the modifications in-place.
"""

import sys
import os

try:
    import fitz
except ImportError:
    fitz = None

# Comprehensive Cyrillic to Latin mapping (Reverse of cy.md rules)
CYRILLIC_TO_LATIN = {
    # 1-to-1 Azerbaijani Cyrillic Mappings
    'А': 'A', 'а': 'a',
    'Б': 'B', 'б': 'b',
    'В': 'V', 'в': 'v',
    'Г': 'Q', 'г': 'q',
    'Д': 'D', 'д': 'd',
    'Е': 'E', 'е': 'e',
    'Ә': 'Ə', 'ә': 'ə',
    'Ж': 'J', 'ж': 'j',
    'З': 'Z', 'з': 'z',
    'И': 'İ', 'и': 'i',
    'Ј': 'Y', 'ј': 'y',
    'К': 'K', 'к': 'k',
    'Ҝ': 'G', 'ҝ': 'g',
    'Л': 'L', 'л': 'l',
    'М': 'M', 'м': 'm',
    'Н': 'N', 'н': 'n',
    'О': 'O', 'о': 'o',
    'Ө': 'Ö', 'ө': 'ö',
    'П': 'P', 'п': 'p',
    'Р': 'R', 'р': 'r',
    'С': 'S', 'с': 's',
    'Т': 'T', 'т': 't',
    'У': 'U', 'у': 'u',
    'Ү': 'Ü', 'ү': 'ü',
    'Ф': 'F', 'ф': 'f',
    'Х': 'X', 'х': 'x',
    'Һ': 'H', 'һ': 'h',
    'Ч': 'Ç', 'ч': 'ç',
    'Ҹ': 'C', 'ҹ': 'c',
    'Ш': 'Ş', 'ш': 'ş',
    'Ы': 'I', 'ы': 'ı',
    'Ғ': 'Ğ', 'ғ': 'ğ',
    
    # Smart/Historical/Russian Mappings
    'Ц': 'Ts', 'ц': 'ts',
    'Ю': 'Yu', 'ю': 'yu',
    'Я': 'Ya', 'я': 'ya',
    'Ё': 'Yo', 'ё': 'yo',
    'Э': 'E',  'э': 'e',
    
    # Rare Russian letters (phonetic fallback)
    'Щ': 'Şç', 'щ': 'şç',
    'Ъ': '',   'ъ': '',
    'Ь': '',   'ь': '',
    'Й': 'Y',  'й': 'y'
}

def transliterate(text: str) -> str:
    """Convert Cyrillic string to Latin using the mapping."""
    result = []
    for char in text:
        result.append(CYRILLIC_TO_LATIN.get(char, char))
    return "".join(result)

def add_latin_search_layer(pdf_path: str) -> bool:
    """
    Opens the PDF, extracts text, transliterates to Latin, and injects
    an invisible text layer. Saves the PDF in-place.
    """
    if fitz is None:
        print("Error: PyMuPDF (fitz) is not installed. Cannot add search layer.")
        return False
        
    if not os.path.exists(pdf_path):
        print(f"Error: File not found: {pdf_path}")
        return False

    try:
        doc = fitz.open(pdf_path)
        
        for page in doc:
            # Extract text blocks as a dictionary
            text_data = page.get_text("dict")
            
            for block in text_data.get("blocks", []):
                if block.get("type") != 0:
                    continue  # Skip non-text blocks (like images)
                
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        original_text = span.get("text", "")
                        
                        # Only inject if there's text and it contains Cyrillic
                        # (A simple heuristic: if transliteration changes the text, we inject)
                        latin_text = transliterate(original_text)
                        
                        if latin_text != original_text and latin_text.strip():
                            origin = span.get("origin")
                            size = span.get("size", 10)
                            
                            # Insert invisible text (render_mode=3)
                            # We use "helv" as it's a standard built-in font that supports most Latin chars
                            page.insert_text(
                                origin,
                                latin_text,
                                fontsize=size,
                                fontname="helv",
                                render_mode=3,
                                color=(0, 0, 0)
                            )
        
        # Save in-place
        doc.saveIncr()
        doc.close()
        return True
        
    except Exception as e:
        print(f"Error adding Latin search layer: {e}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 kitab_transliterate.py <input.pdf>")
        sys.exit(1)
        
    target_pdf = sys.argv[1]
    print(f"Adding Latin search layer to {target_pdf}...")
    success = add_latin_search_layer(target_pdf)
    if success:
        print("Done! PDF updated in-place.")
    else:
        print("Failed to update PDF.")
        sys.exit(1)
