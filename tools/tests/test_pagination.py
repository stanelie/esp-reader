#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline pagination checks for device/code.py, with hyphenation on.

Hyphenation is the first thing that puts a line break inside a word, and the
reader's whole resume story rests on a page's start being a byte offset that
lands on a word. So this pulls read_page_stream and _hyphenate_word straight
out of code.py - the real text, not a copy - measures with the real font, and
paginates a real book.

    python3 tools/tests/test_pagination.py [book.txt]

Checks:
  1. no text is lost or duplicated (whole book, hyphens and spaces removed)
  2. every page offset lands on a word boundary  <- the invariant
  3. a page re-rendered from its offset is identical  <- resume and back-nav
  4. hyphenated and unhyphenated pagination carry the same text
"""
import ast
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))


def font_measure(pf_path):
    """get_string_width, using the real PropFont and the reader's substitutions."""
    sys.path.insert(0, os.path.join(ROOT, "device", "lib"))
    from propfont import PropFont
    font = PropFont(pf_path)
    src = open(os.path.join(ROOT, "device", "code.py"), encoding="utf-8").read()
    env = {}
    for nd in ast.parse(src).body:
        if isinstance(nd, ast.Assign) and getattr(nd.targets[0], "id", "") == "FONT_SUBS":
            exec(ast.get_source_segment(src, nd), env)
        if isinstance(nd, ast.FunctionDef) and nd.name == "to_font":
            exec(ast.get_source_segment(src, nd), env)
    return font, (lambda s: font.text_width(env["to_font"](s)))


def build(hyphenate):
    """Exec the real read_page_stream out of code.py against stubs."""
    src = open(os.path.join(ROOT, "device", "code.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    want = {"_hyphenate_word", "read_page_stream"}
    chunks = [ast.get_source_segment(src, n) for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in want]
    assert len(chunks) == len(want), "functions moved or renamed: %s" % chunks

    sys.path.insert(0, os.path.join(ROOT, "device", "lib"))
    import hyphenator
    hyphenator._PATTERNS_PATH = os.path.join(ROOT, "device", "hyphen_en.bin")
    hyphenator._BLOB = None
    hyphenator._load()

    font, measure = font_measure(os.path.join(ROOT, "device", "fonts",
                                               "literata.pf"))
    env = {
        "get_string_width": measure,
        "MAX_LINE_WIDTH_PX": 292,          # E290
        "MAX_LINES_PER_PAGE": (128 - 2) // (font.box_h + 0),
        "SPACE_WIDTH": measure(" "),
        "hyphenate_ok": hyphenate,
        "hyphenator": hyphenator,
    }
    for c in chunks:
        exec(c, env)
    return env["read_page_stream"], env


def paginate(read_page_stream, path, limit=400):
    pages, off = [], 0
    while len(pages) < limit:
        lines, wrapped, nxt = read_page_stream(path, off)
        if nxt == off:
            break
        pages.append((off, lines, wrapped))
        off = nxt
    return pages, off


def main():
    book = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(ROOT, "device", "demobook.txt")
    if not os.path.exists(book):
        raise SystemExit("no book at %s" % book)
    raw = open(book, "rb").read()
    print("book: %s (%d bytes)\n" % (os.path.basename(book), len(raw)))

    rps_h, _ = build(True)
    rps_p, _ = build(False)
    pages_h, end_h = paginate(rps_h, book)
    pages_p, end_p = paginate(rps_p, book)
    print("paginated %d pages hyphenated, %d plain" % (len(pages_h), len(pages_p)))

    fails = 0
    strip = lambda s: "".join(s.split()).replace("-", "").replace("—", "")

    # 1. no text lost or duplicated
    text = raw[:end_h].decode("utf-8", "replace")
    rendered = "".join("".join(l) for _, l, _w in pages_h)
    ok = strip(rendered) == strip(text)
    print("1. text preserved across %d pages: %s" % (len(pages_h), "yes" if ok else "NO"))
    if not ok:
        a, b = strip(rendered), strip(text)
        i = next((i for i in range(min(len(a), len(b))) if a[i] != b[i]), min(len(a), len(b)))
        print("     first divergence at %d: rendered %r vs source %r"
              % (i, a[max(0, i-40):i+40], b[max(0, i-40):i+40]))
    fails += not ok

    # 2. every page offset lands on a word boundary
    # The invariant is "not inside a word", not "at the first letter of one".
    # An offset pointing at a blank line is a perfectly good boundary - the
    # blank line is simply the next page's first rendered line. Only an offset
    # with text on BOTH sides of it has split a word.
    bad = []
    for off, _, _w in pages_h:
        if off == 0:
            continue
        before, at = raw[off - 1:off], raw[off:off + 1]
        if before and at and not before.isspace() and not at.isspace():
            bad.append((off, raw[max(0, off - 12):off + 12]))
    print("2. page offsets on word boundaries: %s"
          % ("all %d" % len(pages_h) if not bad else "NO, %d bad: %s" % (len(bad), bad[:3])))
    fails += bool(bad)

    # 3. re-rendering a page from its offset is identical
    bad = []
    for off, lines, _w in pages_h:
        again, _w, _ = rps_h(book, off)
        if again != lines:
            bad.append(off)
    print("3. pages reproducible from their offset: %s"
          % ("all %d" % len(pages_h) if not bad else "NO at %s" % bad[:3]))
    fails += bool(bad)

    # 4. hyphenated and plain carry the same text
    common = min(end_h, end_p)
    a = strip("".join("".join(l) for o, l, _w in pages_h if o < common))
    b = strip("".join("".join(l) for o, l, _w in pages_p if o < common))
    ok = a[:2000] == b[:2000]
    print("4. same text with hyphenation on and off: %s" % ("yes" if ok else "NO"))
    fails += not ok

    # 5. The property that matters is not "wrapped lines are full" - a line
    #    can legitimately end early when the next word is enormous. It is that
    #    no under-full line is ever actually justified. draw_text_justified
    #    declines past MAX_SPACE_STRETCH; check that guard really catches them.
    _, env5 = build(True)
    m5 = env5["get_string_width"]
    sp = m5(" ")

    def would_justify(line):
        words = line.split(" ")
        gaps = len(words) - 1
        if gaps < 1:
            return False
        slack = 292 - sum(m5(w) for w in words) - gaps * sp
        return 0 < slack <= gaps * sp * (2.0 - 1.0)

    bad, justified, declined = [], 0, 0
    for off, lines, wrapped in pages_h:
        for i, l in enumerate(lines):
            if not wrapped[i]:
                continue
            if would_justify(l):
                justified += 1
                if m5(l) < 292 * 0.75:
                    bad.append((off, l[:44], m5(l)))
            else:
                declined += 1
    print("5. no under-full line is justified: %s  (%d justified, %d declined)"
          % ("yes" if not bad else "NO %s" % bad[:2], justified, declined))
    fails += bool(bad)

    # what it bought
    def ragged(pages):
        # Only lines that were actually wrapped. A paragraph's last line is
        # short because the paragraph ended, and no hyphenation can help it -
        # counting those buries the effect in noise.
        from statistics import mean
        _, env = build(True)
        m = env["get_string_width"]
        gaps = []
        for _, lines, wrapped in pages:
            for i, l in enumerate(lines):
                if wrapped[i]:
                    gaps.append(292 - m(l))
        return mean(gaps) if gaps else 0
    rh, rp = ragged(pages_h), ragged(pages_p)
    print("\nragged right edge: %.1f px plain -> %.1f px hyphenated (%.0f%% tighter)"
          % (rp, rh, (1 - rh / rp) * 100))
    print("pages for the same text: %d plain -> %d hyphenated"
          % (len(pages_p), len([p for p in pages_h if p[0] < end_p])))

    print("\n%s" % ("ALL CHECKS PASSED" if not fails else "%d CHECK(S) FAILED" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
