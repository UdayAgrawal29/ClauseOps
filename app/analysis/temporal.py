import re
from datetime import datetime, timedelta
import dateparser

class TemporalNormalizer:
    def __init__(self):
        # Regex to capture "X days", "Y months", "Z years"
        self.duration_pattern = re.compile(
            r"(?P<value>\d+)\s*(?P<unit>day|month|year)s?", 
            re.IGNORECASE
        )

    def normalize_date(self, date_text: str):
        """
        Converts absolute dates (e.g., "September 29, 2009") to YYYY-MM-DD.
        Returns None if it's not a specific date.
        """
        # Skip generic days like "Sunday" or "Business Day"
        if len(date_text) < 5 or "day" in date_text.lower():
            return None
            
        dt = dateparser.parse(date_text)
        if dt:
            return dt.strftime("%Y-%m-%d")
        return None

    def extract_duration(self, text: str):
        """
        Converts durations (e.g., "30 days") into a dictionary {days: 30}.
        """
        # Convert text words to numbers (simple mapping)
        text = text.lower().replace("one", "1").replace("two", "2").replace("ten", "10").replace("thirty", "30").replace("twelve", "12")
        
        match = self.duration_pattern.search(text)
        if match:
            val = int(match.group("value"))
            unit = match.group("unit").lower()
            
            if "day" in unit:
                return timedelta(days=val)
            elif "month" in unit:
                return timedelta(days=val * 30) # Approximation
            elif "year" in unit:
                return timedelta(days=val * 365)
                
        return None

# Simple test block
if __name__ == "__main__":
    tn = TemporalNormalizer()
    print(f"Date: {tn.normalize_date('September 29, 2009')}")
    print(f"Duration: {tn.extract_duration('thirty (30) days')}")
    print(f"Duration: {tn.extract_duration('ten (10) years')}")