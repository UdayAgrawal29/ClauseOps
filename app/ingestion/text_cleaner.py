import re

def clean_text(raw: str) -> str:
    """
    Cleans text to prepare it for the Legal Segmenter.
    1. Fixes broken words split across lines.
    2. Merges multi-line headers (ARTICLE X \n TITLE -> ARTICLE X TITLE).
    3. Joins sentence fragments.
    4. Forces 'ARTICLE' and 'SECTION' to be on their own lines.
    """
    if raw is None:
        return ""

    # 1. Normalize line endings
    text = raw.replace("\r", "\n")

    # 2. Fix hyphenated words (word-\nbreak -> wordbreak)
    text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)
    
    # 3. Normalize spaces (collapse multiple spaces/tabs to one)
    text = re.sub(r'[ \t]+', ' ', text)

    # Compile regex for detecting if a line IS a header start (e.g., "ARTICLE 1")
    # We use this inside the loop to decide if we should merge the NEXT line.
    # header_start_pattern = re.compile(r"^(ARTICLE\s+[IVX0-9]+|SECTION\s+[0-9\.]+)", re.IGNORECASE)
    header_start_pattern = re.compile(
        r"^(ARTICLE\s+[IVX0-9\.]+|SECTION\s+[0-9\.]+|[0-9]+\.\s)", 
        re.IGNORECASE
    )
    lines = text.splitlines()
    fixed_lines = []

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue # Skip empty lines

        # Logic to determine if we should MERGE this line UP to the previous line
        should_merge = False
        
        if fixed_lines:
            prev_line = fixed_lines[-1]
            
            # Condition A: Standard sentence wrap (current line starts lowercase)
            # e.g., "The Tenant shall" \n "pay rent."
            if not line[0].isupper() and not prev_line.endswith(('.', ':', ';', '!', '?')):
                should_merge = True
            
            # Condition B: Multi-line UPPERCASE HEADERS (Your specific fix)
            # Check if previous line started with "ARTICLE/SECTION" AND current line is ALL CAPS
            # e.g., "ARTICLE 1." \n "BACKGROUND AND DEFINITIONS"
            elif header_start_pattern.match(prev_line) and line.isupper():
                should_merge = True

        if should_merge:
            fixed_lines[-1] = fixed_lines[-1] + " " + line
        else:
            fixed_lines.append(line)
            
    text = "\n".join(fixed_lines)

    # 4. FORCE HEADERS TO NEW LINES (The "Sticky Header" Fix)
    # This ensures "ARTICLE 1" is never buried at the end of a paragraph.
    # It adds double newlines \n\n before any "ARTICLE X" or "SECTION X".
    # We run this AFTER merging, so the full header "ARTICLE 1 TITLE" stays together.
    # pattern = r"(?i)(\.|:|;)?\s*(ARTICLE\s+[IVX0-9]+|SECTION\s+[0-9\.]+)"
    # text = re.sub(pattern, r"\1\n\n\2", text)
    pattern = r"(?i)(\.|:|;)?\s*(ARTICLE\s+[IVX0-9\.]+|SECTION\s+[0-9\.]+|[0-9]+\.\s)"
    text = re.sub(pattern, r"\1\n\n\2", text)
    
    # 5. Collapse excessive newlines
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    # 6. Safe Control Char Removal
    # Remove non-printable chars but keep newlines (\x0a) and tabs (\x09)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)

    return text.strip()