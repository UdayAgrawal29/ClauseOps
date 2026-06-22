import re, sys, glob
key = sys.argv[1] if len(sys.argv) > 1 else ""
for p in sorted(glob.glob('scripts/integration/_gt/*.pipeline.md')):
    if key.lower() not in p.lower():
        continue
    t = open(p, encoding='utf-8').read()
    blocks = t.split('### Task ')[1:]
    print(f"{p.split(chr(92))[-1]}  ({len(blocks)} tasks)")
    for b in blocks:
        title = b.splitlines()[0][3:]
        ty = re.search(r'Type: (\w+)', b)
        dt = re.search(r'Date type: (\w+)', b)
        rv = re.search(r'Requires review: (\w+)', b)
        print(f"  [{(ty.group(1) if ty else '?'):11}] dt={(dt.group(1) if dt else '?'):10} rev={(rv.group(1) if rv else '?'):5} {title[:82]}")
