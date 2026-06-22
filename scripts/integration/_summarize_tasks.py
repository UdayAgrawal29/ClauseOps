import glob, re, sys

for p in sorted(glob.glob('scripts/integration/_gt/*.pipeline.md')):
    t = open(p, encoding='utf-8').read()
    name = p.split('\\')[-1].split('/')[-1][:24]
    blocks = t.split('### Task ')[1:]
    print(f"\n===== {name}  ({len(blocks)} tasks) =====")
    for blk in blocks:
        title = blk.splitlines()[0]
        ty = re.search(r'Type: (\w+)', blk)
        dd = re.search(r'Due date: (\S+)', blk)
        rv = re.search(r'Requires review: (\w+)', blk)
        dt = re.search(r'Date type: (\w+)', blk)
        ty = ty.group(1) if ty else '?'
        dd = dd.group(1) if dd else '?'
        rv = rv.group(1) if rv else '?'
        dt = dt.group(1) if dt else '?'
        print(f"  [{ty:11}] due={dd:11} dtype={dt:10} rev={rv:5} | {title[3:][:95]}")
