import re, os, sys, glob

CL_ROOT = "/Users/pletcher/code/PerseusCode/corpora/canonical-greekLit"
WHO = "Charles Pletcher"
WHEN = "2026-08-26"

def add_change(rel_path, dry_run=True):
    path = os.path.join(CL_ROOT, rel_path)
    raw = open(path, encoding="utf-8").read()
    change_el = f'<change who="{WHO}" when="{WHEN}">Add citeStructure refsDecl</change>'

    def line_indent(raw, pos):
        line_start = raw.rfind("\n", 0, pos) + 1
        indent = raw[line_start:pos]
        return indent if indent.strip() == "" else ""

    m = re.search(r"<revisionDesc(?:\s[^>]*)?>", raw)
    if m:
        after_nl = raw.find("\n", m.end())
        child_indent = None
        if after_nl != -1:
            candidate = line_indent(raw, after_nl + 1)
            if raw[after_nl + 1:after_nl + 1 + 200].lstrip().startswith("<"):
                child_indent = candidate
        if not child_indent:
            child_indent = line_indent(raw, m.start()) + "  "
        new_raw = raw[:m.end()] + f"\n{child_indent}{change_el}" + raw[m.end():]
    else:
        m2 = re.search(r"</teiHeader>", raw)
        if not m2:
            return ("FAIL-NO-TEIHEADER-CLOSE", None)
        base_indent = line_indent(raw, m2.start())
        child_indent = base_indent + "  "
        block = f"{base_indent}<revisionDesc>\n{child_indent}{change_el}\n{base_indent}</revisionDesc>\n"
        new_raw = raw[:m2.start()] + block + raw[m2.start():]

    if not dry_run:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_raw)
    return ("OK", None)

if __name__ == "__main__":
    write = "--write" in sys.argv
    files = [f for f in glob.glob("data/**/*.xml", recursive=True)]
    touched = []
    for f in files:
        raw = open(f, encoding="utf-8").read()
        if 'refsDecl xml:id="CTS"' in raw:
            touched.append(f)
    print(len(touched), "files to log")
    from collections import Counter
    c = Counter()
    for f in touched:
        status, detail = add_change(f, dry_run=not write)
        c[status] += 1
    for s, n in c.most_common():
        print(s, n)
