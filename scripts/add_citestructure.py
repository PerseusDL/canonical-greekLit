import re, sys, os
from lxml import etree

CL_ROOT = "/Users/pletcher/code/PerseusCode/corpora/canonical-greekLit"
PD_ROOT = "/Users/pletcher/code/PerseusCode/canonical-greekLit.pdlcode"
URN_NAMESPACE = "greekLit"

def get_parser():
    return etree.XMLParser(recover=True, resolve_entities=False, load_dtd=False,
                            no_network=True, huge_tree=True)

def is_elem(e):
    return isinstance(e.tag, str)   # excludes comments/PIs, which break etree.QName()

def ln(e):
    return etree.QName(e).localname

def find_pd_citestructure(pd_path):
    tree = etree.parse(pd_path, get_parser())
    root = tree.getroot()
    for rd in root.iter():
        if not is_elem(rd) or ln(rd) != "refsDecl":
            continue
        if rd.get("n") == "CTS" or rd.get("{http://www.w3.org/XML/1998/namespace}id") == "CTS":
            for child in rd:
                if is_elem(child) and ln(child) == "citeStructure":
                    return child
    return None

BARE_DIV_RE = re.compile(r"^(?:\.//|/)*div\[@type='([^']+)'\]$")
NUMBERED_DIV_RE = re.compile(r"^(?:\.//|/)*div(\d+)\[@type='([^']+)'\]$")
GENERIC_TYPE_DIV_RE = re.compile(r"^(?:\.//|/)*div\[@type\]$")
ALREADY_GOOD_RE = re.compile(r"subtype=")

def bfs_find(context_list, predicate, max_depth=6):
    """Level-order search below context_list. Returns (depth, matches) at the
    shallowest depth (1-based) with any match, or (None, [])."""
    frontier = context_list
    for depth in range(1, max_depth + 1):
        candidates = [c for p in frontier for c in p if is_elem(c)]
        if not candidates:
            break
        matches = [c for c in candidates if predicate(c)]
        if matches:
            return depth, matches
        frontier = candidates
    return None, []

def descendant_find(context_list, predicate):
    return [el for parent in context_list for el in parent.iter()
            if is_elem(el) and predicate(el)]

def strict_descendant_find(context_list, predicate):
    """Like descendant_find but never matches a context node itself -- only
    its descendants. Needed for 'is there any div below here', which would
    trivially match if the context nodes are themselves divs."""
    return [el for parent in context_list for el in parent.iter()
            if el is not parent and is_elem(el) and predicate(el)]

def bfs_find_multi(context_list, predicate, max_depth=6):
    """Like bfs_find, but skips a depth whose only match is a single element
    (the classic solitary edition/translation wrapper div, which technically
    satisfies a generic predicate like 'has @type' but isn't real structure)
    in favor of a deeper depth with more than one match. Falls back to a
    shallowest lone match if no depth ever has more than one."""
    frontier = context_list
    fallback = None
    for depth in range(1, max_depth + 1):
        candidates = [c for p in frontier for c in p if is_elem(c)]
        if not candidates:
            break
        matches = [c for c in candidates if predicate(c)]
        if len(matches) > 1:
            return depth, matches
        if matches and fallback is None:
            fallback = (depth, matches)
        frontier = candidates
    return fallback if fallback else (None, [])

def resolve_div_match(match_str, context_list, unresolved_log, path_desc):
    """context_list: all real elements matching the parent citeStructure level.
    Returns (new_match_str, next_context_list_or_None, ok_bool, is_terminal_leaf_bool).
    is_terminal_leaf=True means the match resolved onto a milestone/seg fallback:
    those don't contain further structure, so children below them should be
    dropped silently rather than reported as unresolved (established convention
    in this corpus -- see pitfalls)."""
    if not context_list:
        return (match_str, None, False, False)

    if "|" in match_str:
        parts = [p.strip() for p in match_str.split("|")]
        # A compound "X | Y" match needs no rewriting if neither alternative is
        # a bare (unnumbered) div[@type='X'] selector -- that's the only form
        # this translation ever changes. If the match is already fine as-is,
        # just verify it resolves to *something* for context propagation.
        if all(BARE_DIV_RE.match(p) is None for p in parts):
            found_all = []
            for p in parts:
                _, nc, _, _ = resolve_div_match(p, context_list, [], path_desc)
                if nc:
                    found_all.extend(nc)
            return (match_str, found_all if found_all else None, True, False)
        unresolved_log.append((path_desc, match_str, "compound-match"))
        return (match_str, None, False, False)

    m_num = NUMBERED_DIV_RE.match(match_str)
    if m_num:
        num, X = m_num.group(1), m_num.group(2)
        tag = f"div{num}"
        depth, found = bfs_find(context_list, lambda c, tag=tag, X=X: ln(c) == tag and c.get("type") == X)
        if found:
            new_match = "div/" * (depth - 1) + f"{tag}[@type='{X}']"
            return (new_match, found, True, False)
        unresolved_log.append((path_desc, match_str, "numbered-div-not-found"))
        return (match_str, None, False, False)

    m_bare = BARE_DIV_RE.match(match_str)
    if m_bare:
        X = m_bare.group(1)

        def sub_pred(c, X=X):
            return ln(c) == "div" and (c.get("subtype") or "").lower() == X.lower()
        def typ_pred(c, X=X):
            return ln(c) == "div" and (c.get("type") or "").lower() == X.lower()

        depth_sub, found_sub = bfs_find(context_list, sub_pred)
        depth_typ, found_typ = bfs_find(context_list, typ_pred)
        candidates = []
        if found_sub:
            candidates.append((depth_sub, f"div[@subtype='{found_sub[0].get('subtype')}']", found_sub))
        if found_typ:
            candidates.append((depth_typ, f"div[@type='{found_typ[0].get('type')}']", found_typ))
        if candidates:
            candidates.sort(key=lambda t: t[0])
            depth, base_match, found = candidates[0]
            return ("div/" * (depth - 1) + base_match, found, True, False)

        # No div anywhere below matches X (by subtype or type, case-insensitive).
        # Only fall back to milestone/seg leaf-matching -- or allow this level
        # to be spliced out entirely -- when there is NO other div structure
        # anywhere in this context at all. If real (but differently-named) div
        # structure exists here (more than one, to rule out an incidental
        # single edition/translation wrapper), that's a genuine content/template
        # mismatch needing a human decision -- see "letter collections" pitfall.
        other_divs_here = strict_descendant_find(context_list, lambda el: ln(el) == "div")
        if len(other_divs_here) > 1:
            unresolved_log.append((path_desc, match_str, "bare-div-not-found"))
            return (match_str, None, False, False)

        found_ms = descendant_find(context_list, lambda el, X=X:
            ln(el) == "milestone" and (el.get("unit") or "").lower() == X.lower())
        if found_ms:
            return (f".//milestone[@unit='{found_ms[0].get('unit')}']", found_ms, True, True)

        found_seg = descendant_find(context_list, lambda el, X=X:
            ln(el) == "seg" and (el.get("type") or "").lower() == X.lower())
        if found_seg:
            return (f".//seg[@type='{found_seg[0].get('type')}']", found_seg, True, True)

        unresolved_log.append((path_desc, match_str, "bare-div-not-found"))
        return (match_str, None, False, False)

    if GENERIC_TYPE_DIV_RE.match(match_str):
        # match="div[@type]" (any value) -- pdlcode's flat body has these as
        # direct children; here they're usually one level deeper, below a
        # solitary edition/translation wrapper div that *also* has @type and
        # would otherwise satisfy this predicate at depth 1. Prefer the
        # shallowest depth with more than one match (real sibling structure).
        depth, found = bfs_find_multi(context_list, lambda c: ln(c) == "div" and c.get("type") is not None)
        if found:
            new_match = "div/" * (depth - 1) + "div[@type]"
            return (new_match, found, True, False)
        unresolved_log.append((path_desc, match_str, "generic-type-div-not-found"))
        return (match_str, None, False, False)

    if ALREADY_GOOD_RE.search(match_str):
        # Already in subtype form (possibly the wrong div[@type='textpart'][@subtype='X']
        # spelling -- normalize that to div[@subtype='X']).
        m_sub = re.search(r"@subtype='([^']+)'", match_str)
        found = []
        if m_sub:
            X = m_sub.group(1)
            found = descendant_find(context_list, lambda el, X=X: ln(el) == "div" and el.get("subtype") == X)
            new_match = re.sub(r"div\[@type='textpart'\]\[@subtype='([^']+)'\]", r"div[@subtype='\1']", match_str)
            return (new_match, found if found else None, bool(found), False)
        return (match_str, found if found else None, bool(found), False)

    # non-div match (l, pb, lb, milestone[...], p/milestone, etc.) -- unchanged
    return (match_str, context_list, True, False)

def clone_and_resolve(cs_el, context, unresolved_log, path_desc):
    match_orig = cs_el.get("match")
    child_cs_list = [c for c in cs_el if is_elem(c) and ln(c) == "citeStructure"]

    if match_orig is not None:
        local_log = []
        new_match, new_context, ok, is_leaf = resolve_div_match(match_orig, context, local_log, path_desc)
    else:
        new_match, new_context, ok, is_leaf, local_log = None, context, True, False, []

    if not ok:
        # Try splicing this failed level out entirely: if there's exactly one
        # nested citeStructure, see if IT resolves cleanly against the
        # *original* (grandparent) context, i.e. as if this level didn't exist.
        if len(child_cs_list) == 1:
            child = child_cs_list[0]
            trial_log = []
            trial_node = clone_and_resolve(child, context, trial_log, path_desc + f"/{child.get('unit','?')}")
            if not trial_log:
                return trial_node
        unresolved_log.extend(local_log)
        new = etree.Element("citeStructure")
        for k, v in cs_el.attrib.items():
            new.set(k, v)
        for child in child_cs_list:
            new.append(clone_and_resolve(child, None, unresolved_log, path_desc + f"/{child.get('unit','?')}"))
        return new

    new = etree.Element("citeStructure")
    for k, v in cs_el.attrib.items():
        new.set(k, new_match if k == "match" else v)
    for child in child_cs_list:
        child_unit = child.get("unit", "?")
        if is_leaf:
            # milestone/seg leaves don't nest further -- drop any child that
            # can't resolve cleanly here, silently (established convention).
            trial_log = []
            trial_node = clone_and_resolve(child, new_context, trial_log, path_desc + f"/{child_unit}")
            if not trial_log:
                new.append(trial_node)
        else:
            new.append(clone_and_resolve(child, new_context, unresolved_log, path_desc + f"/{child_unit}"))
    return new

def build_top_citestructure(pd_cs, body_elem, unresolved_log):
    top = etree.Element("citeStructure")
    top.set("match", "/TEI/text/body")
    top.set("use", "@xml:base")
    for child in pd_cs:
        if is_elem(child) and ln(child) == "citeStructure":
            top.append(clone_and_resolve(child, [body_elem], unresolved_log, child.get("unit", "?")))
    return top

def render_refsdecl(top_cs, indent):
    wrapper = etree.Element("refsDecl")
    wrapper.set("{http://www.w3.org/XML/1998/namespace}id", "CTS")
    wrapper.append(top_cs)
    raw = etree.tostring(wrapper, pretty_print=True).decode("utf-8")
    return "\n".join(indent + line for line in raw.rstrip("\n").split("\n"))

# Plutarch tlg0007.tlg121 and tlg0007.tlg125: pdlcode's citeStructure expects
# a chapter tier (div[@type='chapter']), but every exemplar in this repo
# (grc/eng alike) uses subtype="section" instead -- a genuine content
# divergence between the two repos, confirmed with the user, who chose to
# name the unit "section" to match this repo's actual markup.
SECTION_OVERRIDE_REL_PATHS = {
    "data/tlg0007/tlg121/tlg0007.tlg121.perseus-eng3.xml",
    "data/tlg0007/tlg121/tlg0007.tlg121.perseus-eng4.xml",
    "data/tlg0007/tlg125/tlg0007.tlg125.perseus-eng2.xml",
}

def make_section_override_pd_cs():
    outer = etree.Element("citeStructure")
    outer.set("match", "/TEI/text/body")
    outer.set("use", "@xml:base")
    inner = etree.SubElement(outer, "citeStructure")
    inner.set("unit", "section")
    inner.set("delim", ":")
    inner.set("match", "div[@type='section']")
    inner.set("use", "@n")
    return outer

def analyze(rel_path):
    cl_path = os.path.join(CL_ROOT, rel_path)
    pd_path = os.path.join(PD_ROOT, rel_path)
    if rel_path in SECTION_OVERRIDE_REL_PATHS:
        pd_cs = make_section_override_pd_cs()
    else:
        if not os.path.isfile(pd_path):
            return ("SKIP-NO-PD", None)
        pd_cs = find_pd_citestructure(pd_path)
        if pd_cs is None:
            return ("SKIP-NO-CITESTRUCT", None)

    tree = etree.parse(cl_path, get_parser())
    root = tree.getroot()
    body = next((el for el in root.iter() if is_elem(el) and ln(el) == "body"), None)
    if body is None:
        return ("SKIP-NO-BODY", None)

    # The base URN is derived from the filename, NOT read from the XML --
    # verified on Latin: where an embedded urn:cts: div/@n existed, it always
    # matched the filename-derived value exactly.
    stem = os.path.basename(rel_path)[:-4]
    urn = f"urn:cts:{URN_NAMESPACE}:{stem}"

    urn_divs = [c for c in body if is_elem(c) and ln(c) == "div" and (c.get("n") or "").strip() == urn]
    if len(urn_divs) > 1:
        return (f"SKIP-URN-DIV-COUNT-{len(urn_divs)}", None)
    has_urn_div = len(urn_divs) == 1

    m = re.search(r"-([a-z]+)\d*$", urn)   # exemplar suffix, e.g. "perseus-grc2" -> "grc"
    if not m:
        return ("SKIP-NO-LANG-MATCH", None)
    lang = m.group(1)

    text_el = next((el for el in root.iter() if is_elem(el) and ln(el) == "text"), None)
    if text_el is None:
        return ("SKIP-NO-TEXT-EL", None)

    unresolved_log = []
    top_cs = build_top_citestructure(pd_cs, body, unresolved_log)
    return {"urn": urn, "lang": lang, "has_urn_div": has_urn_div,
            "top_cs": top_cs, "unresolved": unresolved_log}

def apply_edit(rel_path, info):
    """Surgical string edits on the raw file text -- NOT a full lxml
    re-serialization. This matters: round-tripping the whole document through
    lxml.tostring() collapses multi-line attribute formatting, changes the XML
    declaration's quote style, drops blank lines between leading PIs, and
    strips the final newline -- none of which is worth the noise in a diff
    across ~1600 files. Only touch what needs touching."""
    cl_path = os.path.join(CL_ROOT, rel_path)
    raw = open(cl_path, encoding="utf-8").read()
    urn, lang = info["urn"], info["lang"]

    m = re.search(r"<body\b", raw)
    if not m:
        return ("FAIL-BODY-TAG-NOT-FOUND", None)
    raw = raw[:m.end()] + f' xml:base="{urn}"' + raw[m.end():]

    if info["has_urn_div"]:
        body_pos = m.start()
        div_n_re = re.compile(r'(<div\b[^>]*?)\s+n=(["\'])' + re.escape(urn) + r'\2([^>]*>)', re.DOTALL)
        m2 = div_n_re.search(raw, body_pos)
        if not m2:
            return ("FAIL-DIV-N-NOT-FOUND", None)
        raw = raw[:m2.start()] + m2.group(1) + m2.group(3) + raw[m2.end():]

    m3 = re.search(r"<text\b[^>]*>", raw)
    if not m3:
        return ("FAIL-TEXT-TAG-NOT-FOUND", None)
    text_tag = m3.group(0)
    if re.search(r'\bxml:lang="[^"]*"', text_tag):
        new_text_tag = re.sub(r'xml:lang="[^"]*"', f'xml:lang="{lang}"', text_tag)
    elif re.search(r"\bxml:lang='[^']*'", text_tag):
        new_text_tag = re.sub(r"xml:lang='[^']*'", f'xml:lang="{lang}"', text_tag)
    else:
        new_text_tag = re.sub(r"^<text\b", f'<text xml:lang="{lang}"', text_tag)
    raw = raw[:m3.start()] + new_text_tag + raw[m3.end():]

    m4 = re.search(r"</encodingDesc>", raw)
    if not m4:
        return ("FAIL-ENCODINGDESC-CLOSE-NOT-FOUND", None)
    line_start = raw.rfind("\n", 0, m4.start()) + 1
    indent = raw[line_start:m4.start()]
    indent = indent if indent.strip() == "" else ""
    block = render_refsdecl(info["top_cs"], indent)
    raw = raw[:m4.start()] + block + "\n" + raw[m4.start():]

    with open(cl_path, "w", encoding="utf-8") as f:
        f.write(raw)
    return ("OK", None)

def process(rel_path, dry_run=True):
    info = analyze(rel_path)
    if isinstance(info, tuple):
        return info
    if dry_run:
        return ("WOULD-OK" if not info["unresolved"] else "WOULD-UNRESOLVED", info["unresolved"])
    status, detail = apply_edit(rel_path, info)
    if status == "OK" and info["unresolved"]:
        return ("OK-WITH-UNRESOLVED", info["unresolved"])
    return (status, detail)

if __name__ == "__main__":
    rel = sys.argv[1]
    dry = "--write" not in sys.argv
    print(process(rel, dry_run=dry))
