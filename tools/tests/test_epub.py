#!/usr/bin/env python3
"""Offline check that device/lib/epub_xtract.py converts a real EPUB.

Runs the device modules under CPython against a sandbox directory, so nothing
touches the real filesystem root. Then feeds the result through the reader's
own pagination to prove the output is a book the reader can actually read.

    python3 tools/tests/test_epub.py path/to/book.epub
"""
import importlib.util
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
LIB = os.path.join(ROOT, "device", "lib")


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    epub = os.path.abspath(sys.argv[1])
    sandbox = tempfile.mkdtemp(prefix="epubtest-")
    books = os.path.join(sandbox, "books")
    os.makedirs(books)

    sys.path.insert(0, LIB)
    import epub_xtract

    # The module builds absolute paths as "/" + TARGET_DIR + "/name". Pointing
    # TARGET_DIR at the sandbox keeps every write inside it.
    epub_xtract.TARGET_DIR = books.lstrip("/")
    # On the device these decide whether the board owns the filesystem; on the
    # host we simply do.
    epub_xtract._writable = lambda: True
    epub_xtract.ensure_writable = lambda: True

    seen = []
    def progress(stage, done, total, name=""):
        seen.append(stage)
        if stage in ("start", "done", "partial", "failed", "empty"):
            print("   [%s] %s %s/%s" % (stage, name, done, total))

    # --- 0. the round trip the device actually performs -------------------
    # The earlier version of this test checked only where the .txt lands. It
    # never checked that the converter can FIND the .epub, which is the half
    # that was broken: the reader lists "alice.epub" / "books/alice.epub", and
    # source_path() reads a bare name as living in /books.
    import ast as _ast
    _src = open(os.path.join(ROOT, "device", "code.py"), encoding="utf-8").read()
    _env = {}
    for _nd in _ast.parse(_src).body:
        if isinstance(_nd, _ast.FunctionDef) and _nd.name in ("epub_txt_path",
                                                              "book_title"):
            exec(_ast.get_source_segment(_src, _nd), _env)
    # how convert_epub builds the path it hands over
    absolutise = lambda p: p if p.startswith("/") else "/" + p
    print("0. reader path -> where the converter looks:")
    bad0 = 0
    for listed, actual in (("alice.epub", "/alice.epub"),
                           ("books/alice.epub", "/books/alice.epub"),
                           ("My Book.epub", "/My Book.epub")):
        got = epub_xtract.source_path(absolutise(listed))
        ok = got == actual
        bad0 += not ok
        print("     %-20r -> %-24r %s" % (listed, got, "ok" if ok else
                                          "WRONG, file is at %r" % actual))
    fails_round = bad0
    print()

    print("converting %s (%d bytes)" % (os.path.basename(epub), os.path.getsize(epub)))
    out = epub_xtract.convert_book(epub, progress=progress, keep_display=True)
    fails = fails_round

    if not out or not os.path.exists(out):
        print("FAILED: no text produced")
        return 1
    size = os.path.getsize(out)
    text = open(out, encoding="utf-8", errors="replace").read()
    words = text.split()
    print("\n1. produced %s: %d bytes, %d words, %d paragraphs"
          % (os.path.basename(out), size, len(words), text.count("\n\n")))
    print("   chapters written/attempted: %s" % (epub_xtract.LAST_COUNTS,))
    print("   stages seen: %s" % ",".join(sorted(set(seen))))

    # Proportional, not absolute: a 4 KB quick-start pamphlet is a legitimate
    # EPUB and will never yield 200 words.
    ok = len(words) > 20 and size > os.path.getsize(epub) * 0.05
    print("2. output is proportional to the source: %s (%.0f%% of the .epub)"
          % ("yes" if ok else "NO", size / os.path.getsize(epub) * 100))
    fails += not ok

    leftover = [c for c in "<>" if c in text]
    stray = text.count("<p") + text.count("</") + text.count("&nbsp;") + text.count("&amp;")
    print("3. markup stripped: %s (%d stray tag/entity fragments)"
          % ("yes" if stray == 0 else "NO", stray))
    fails += bool(stray)

    print("4. first 3 non-blank lines:")
    for l in [l for l in text.split("\n") if l.strip()][:3]:
        print("     %s" % l[:76])

    # 5. the reader must be able to paginate what came out
    sys.path.insert(0, HERE)
    import test_pagination as T
    rps, _ = T.build(True)
    # No page cap: the whole book must paginate to the end, not just the part
    # an arbitrary limit happened to cover.
    pages, end = T.paginate(rps, out, limit=100000)
    print("5. reader paginates all of it: %d pages, consumed %d of %d bytes (%.1f%%)"
          % (len(pages), end, size, end / size * 100))
    ok = len(pages) > 1 and end >= size * 0.98
    fails += not ok

    # A blank final page is the reader meeting trailing newlines at EOF, which
    # predates EPUB and is cosmetic. A blank page in the MIDDLE would mean the
    # converter emitted a run of empty paragraphs, which is a real defect.
    blank = [i for i, (off, lines, w) in enumerate(pages)
             if not any(l.strip() for l in lines)]
    interior = [i for i in blank if i < len(pages) - 1]
    print("6. no blank pages mid-book: %s%s"
          % ("yes" if not interior else "NO at %s" % interior[:3],
             "  (last page is blank - trailing newlines at EOF)"
             if blank and not interior else ""))
    fails += bool(interior)

    print("\nsandbox: %s" % sandbox)
    print("%s" % ("ALL CHECKS PASSED" if not fails else "%d CHECK(S) FAILED" % fails))
    if not fails:
        shutil.rmtree(sandbox, ignore_errors=True)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
