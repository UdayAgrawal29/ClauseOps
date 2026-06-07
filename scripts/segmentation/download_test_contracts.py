"""
Download diverse contract PDFs from SEC EDGAR for testing.

Selects contracts with DIFFERENT formatting conventions:
- Numbered sections (1. 2. 3.)
- Article format (Article I, Article II)
- ALL CAPS headings without numbers
- Bold-only headings
- Mixed formats
- Various contract types (NDA, employment, services, license, lease)
"""
import urllib.request
import os
import sys
import time

# Output directory
OUT_DIR = r"c:\Users\Uday Agrawal\Desktop\Projects\ClauseOps\TEST_PDFS\diverse"
os.makedirs(OUT_DIR, exist_ok=True)

# SEC EDGAR exhibits — these are real contract filings in various formats
# Each tuple: (url, filename, description)
CONTRACTS = [
    # 1. Employment Agreement — typically numbered sections with bold headings
    (
        "https://www.sec.gov/Archives/edgar/data/1318605/000156459021004599/tsla-ex1013_7.htm",
        "Tesla_Employment_Agreement.htm",
        "Tesla employment agreement - numbered sections"
    ),
    # 2. NDA — typically short with Article/Section format
    (
        "https://www.sec.gov/Archives/edgar/data/1652044/000165204422000007/googexhibit101-nda.htm",
        "Google_NDA.htm",
        "Google NDA - short with numbered sections"
    ),
    # 3. Services Agreement — detailed with subsections
    (
        "https://www.sec.gov/Archives/edgar/data/789019/000119312520108992/d896587dex102.htm",
        "Microsoft_Services_Agreement.htm",
        "Microsoft services agreement - complex structure"
    ),
    # 4. Lease Agreement — numbered with exhibits
    (
        "https://www.sec.gov/Archives/edgar/data/1318605/000156459017003118/tsla-ex1016_647.htm",
        "Tesla_Lease_Agreement.htm",
        "Tesla lease - Article format with exhibits"
    ),
    # 5. License Agreement — IP-heavy with definitions
    (
        "https://www.sec.gov/Archives/edgar/data/1652044/000165204419000032/googexhibit101702.htm",
        "Google_License_Agreement.htm",
        "Google license agreement - definitions-heavy"
    ),
    # 6. Credit Agreement — financial, complex numbering
    (
        "https://www.sec.gov/Archives/edgar/data/320193/000119312521285575/d224930dex101.htm",
        "Apple_Credit_Agreement.htm",
        "Apple credit agreement - Article format"
    ),
    # 7. Merger Agreement — long, complex hierarchical structure
    (
        "https://www.sec.gov/Archives/edgar/data/1652044/000119312522011560/d261634dex21.htm",
        "Google_Merger_Agreement.htm",
        "Google merger agreement - Article + Section format"
    ),
]

def download(url, filepath, desc):
    """Download a file with progress."""
    print(f"  Downloading: {desc}")
    print(f"    URL: {url}")
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "ClauseOps Research Tool contact@example.com",
                "Accept-Encoding": "identity",
            }
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            with open(filepath, "wb") as f:
                f.write(data)
            print(f"    OK: {len(data):,} bytes -> {os.path.basename(filepath)}")
            return True
    except Exception as e:
        print(f"    FAIL: {e}")
        return False

print("Downloading diverse test contracts from SEC EDGAR...")
print(f"Output: {OUT_DIR}\n")

success = 0
for url, fname, desc in CONTRACTS:
    fpath = os.path.join(OUT_DIR, fname)
    if os.path.exists(fpath):
        print(f"  SKIP (already exists): {fname}")
        success += 1
        continue
    if download(url, fpath, desc):
        success += 1
    time.sleep(0.5)  # Be polite to SEC servers

print(f"\nDone: {success}/{len(CONTRACTS)} downloaded.")
print("\nNote: These are HTML files from EDGAR. To test as PDFs, you would")
print("convert them to PDF first. But our segmenter can also work on the")
print("existing PDF test files. Let's focus on those + any HTML->text pipeline.")
