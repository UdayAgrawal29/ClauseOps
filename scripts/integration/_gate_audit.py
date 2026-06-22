"""Quantify how many obligation-bearing lines the modal gate would MISS.
Approximates clauseops.obligation_detection.deontic_classifier._ANY_MODAL_RE."""
import re, glob

MODAL = re.compile(
    r"\b(?:shall|must|may|will|agrees?\s+to|is\s+required\s+to|covenants?\s+to|"
    r"undertakes?\s+to|is\s+obligated\s+to|is\s+entitled\s+to|has\s+the\s+right\s+to|"
    r"is\s+prohibited\s+from|is\s+permitted\s+to)\b", re.I)

# Lines that look like obligations but have NO modal: infinitive "To <verb>",
# bullets, or numbered items starting with a capitalized verb.
INFINITIVE = re.compile(r"^\s*(?:[\u2022\u25cf\-\*]|\d+\.\d+(?:\.\d+)?)?\s*To\s+[a-z]", )
IMPERATIVE_VERBS = ("Assign", "Provide", "Maintain", "Arrange", "Make", "Complete",
                    "Direct", "Grow", "Promote", "Issue", "Purchase", "Remunerate",
                    "Deliver", "Ensure", "Prepare", "Keep")
BULLET = re.compile(r"^\s*[\u2022\u25cf]\s*")

for p in sorted(glob.glob('scripts/integration/_gt/*.rawtext.txt')):
    txt = open(p, encoding='utf-8').read()
    name = p.split('\\')[-1].split('/')[-1][:26]
    lines = [l.strip() for l in txt.splitlines() if len(l.strip()) > 25]
    modal_lines = [l for l in lines if MODAL.search(l)]
    modal_less_obl = []
    for l in lines:
        if MODAL.search(l):
            continue
        if INFINITIVE.match(l) or BULLET.match(l) or any(
            re.match(rf"^\s*(?:\d+\.\d+(?:\.\d+)?\s+)?{v}\b", l) for v in IMPERATIVE_VERBS):
            modal_less_obl.append(l)
    print(f"\n== {name} ==")
    print(f"   lines>25c: {len(lines)} | modal-bearing: {len(modal_lines)} "
          f"| modal-LESS obligation-like (MISSED by gate): {len(modal_less_obl)}")
    for l in modal_less_obl[:8]:
        print(f"     MISS: {l[:88]}")
