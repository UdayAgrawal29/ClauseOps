import logging
logging.disable(logging.CRITICAL)
from clauseops.obligation_detection import qa_extractor as q

print("available:", q.is_qa_available())

tests = [
    ("OBLIGATION", "ESSI shall feature the following disclaimer in close proximity to said endorsement."),
    ("PROHIBITION", "Neither Party may transfer or assign any of its rights without the prior written consent of the other Party."),
    ("PERMISSION", "Either party may terminate this Agreement upon thirty days written notice."),
    ("OBLIGATION", "This Agreement shall be governed by the laws of the State of New York."),  # expect abstain
]
for m, s in tests:
    r = q.extract_agent_action(s, m)
    grounded = (r["agent"] is None or r["agent"] in s) and (r["action"] is None or r["action"] in s)
    print(f"\n[{m}] {s[:70]}")
    print(f"   agent : {r['agent']!r} ({r['agent_score']:.2f})")
    print(f"   action: {r['action']!r} ({r['action_score']:.2f})")
    print(f"   grounded={grounded}")
