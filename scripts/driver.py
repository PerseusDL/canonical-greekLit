import sys; sys.path.insert(0, ".")
from add_citestructure import process
from collections import Counter
rels = [l.strip().lstrip("./") for l in open("/tmp/all_tei_greek.txt")]
write = "--write" in sys.argv
c = Counter(); notable = []
for rel in rels:
    try:
        status, detail = process(rel, dry_run=not write)
    except Exception as e:
        status, detail = ("EXCEPTION", str(e))
    c[status] += 1
    if status not in ("OK", "WOULD-OK"):
        notable.append((rel, status, detail))
for s, n in c.most_common(): print(s, n)
print("---notable---")
for n in notable: print(n)
