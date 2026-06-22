import re
t = open('scripts/integration/_gt/NOVOINTEGRATEDSCIENCES,INC_12_23_2019-EX.pipeline.md', encoding='utf-8').read()
blocks = t.split('### Task ')[1:]
print('NOVO TOTAL TASKS:', len(blocks))
for b in blocks:
    title = b.splitlines()[0][3:]
    cl = re.search(r'Clause: (clause_\d+)', b)
    rv = re.search(r'Requires review: (\w+)', b)
    cid = cl.group(1) if cl else '?'
    print(f"  {cid:10} rev={rv.group(1) if rv else '?':5} {title[:80]}")
