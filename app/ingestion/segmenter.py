import re
from typing import List, Dict

class LegalSegmenter:
    def __init__(self):
        # UPDATED REGEX:
        # 1. ARTICLE/SECTION with Roman numerals or numbers (e.g., "ARTICLE I", "SECTION 2.1")
        # 2. Decimal Clause Numbers (e.g., "1.1", "2.1.3")
        # 3. Top-level Numbered Headers (e.g., "1.", "5.") <--- THIS WAS MISSING
        self.header_pattern = re.compile(
            r"(^ARTICLE\s+[IVX0-9\.]+|"  # Matches: ARTICLE 1, ARTICLE I, ARTICLE 1.
            r"^SECTION\s+[0-9\.]+|"       # Matches: SECTION 1, SECTION 2.1
            r"^\d+\.\d+(\.\d+)?\.?|"      # Matches: 1.1, 1.1.1
            r"^\d+\.)\s",                 # Matches: 1. (Like in your 2nd screenshot)
            re.IGNORECASE | re.MULTILINE
        )

    def segment(self, raw_text: str) -> List[Dict[str, str]]:
        clauses = []
        current_header = "PREAMBLE"
        current_text = []

        lines = raw_text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line: continue

            match = self.header_pattern.match(line)
            if match:
                # Save previous clause
                if current_text:
                    full_text = " ".join(current_text)
                    # Only save if there is actual text
                    if full_text.strip(): 
                        clauses.append({
                            "header": current_header,
                            "text": full_text
                        })
                
                # Start new clause
                # We want the header to include the text that follows it on the same line
                # e.g., if line is "1. BACKGROUND", header becomes "1. BACKGROUND"
                # But typically we split header ID from body.
                # For this specific segmenter, let's treat the match as the ID 
                # and the rest of the line as the start of the body IF the cleaner didn't merge them.
                
                # However, your cleaner merges "ARTICLE 1" and "BACKGROUND" into one line.
                # So we should treat the WHOLE LINE as the header if it's short, 
                # or just split based on the regex.
                
                # Simpler approach that matches your output format:
                # Header = The Regex Match (e.g., "1.") 
                # Text = The rest of the line (e.g., "BACKGROUND") + following lines
                
                # BUT, based on your cleaner logic which creates "ARTICLE 1. BACKGROUND...",
                # we usually want the header to be "ARTICLE 1. BACKGROUND..." 
                
                # Let's stick to the previous logic: Header is the ID, text is the content.
                # If the line is "ARTICLE 1. BACKGROUND", match is "ARTICLE 1. "
                
                current_header = line  # Capture the full line as the header (better for "ARTICLE 1. BACKGROUND")
                
                # However, we usually want body text separate. 
                # Let's assume the cleaner puts the Title on the same line as the ID.
                # If the line is "ARTICLE 1. BACKGROUND", let's use that as the Header.
                # And the text starts empty (waiting for next lines).
                
                current_text = [] 
                
                # If there is content AFTER the match on the same line, and it looks like body text (not title),
                # we might want to split it. But for now, let's treat the header line as just the header.
                
            else:
                current_text.append(line)

        # Flush last clause
        if current_text:
            clauses.append({
                "header": current_header,
                "text": " ".join(current_text)
            })
            
        return clauses

def segment_text(text: str):
    seg = LegalSegmenter()
    return seg.segment(text)