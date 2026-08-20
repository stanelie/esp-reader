#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 stanelie <github@stanelie.com>
# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn a TeX .pat.txt hyphenation pattern list into the reader's blob format.

    python3 tools/build_hyphen_patterns.py hyph-fr.pat.txt device/hyphen_fr.txt

The blob is what hyphenator.py binary-searches in place: pattern lines, newline
delimited, sorted by their letters-only key, latin-1 encoded. Latin-1 rather
than UTF-8 for one reason that matters - Liang's algorithm indexes hyphenation
points by character position, and the search compares raw bytes. With UTF-8 an
accented letter is two bytes, so byte offsets and character offsets diverge and
every point after the first accent lands in the wrong place. In latin-1 the two
are the same thing, and French needs no character latin-1 cannot hold, given
the two substitutions below.

Two characters are folded on the way in:

  U+2019 RIGHT SINGLE QUOTATION MARK -> U+0027 APOSTROPHE
      French elision patterns ship twice, once per apostrophe. Folding both to
      ASCII means one pattern each, and the reader folds the same way before
      looking a word up, so l'homme and l’homme hyphenate identically.

  U+0153 LATIN SMALL LIGATURE OE -> 0xBD
      Not in latin-1, but it is at 0xBD in latin-9, and 0xBD is unused by
      French otherwise. Only 5 patterns need it - but they cover coeur, soeur,
      oeuvre, boeuf and voeu, which are not rare words to give up on.
"""
import sys

FOLD = {0x2019: "'", 0x0153: "\xbd", 0x0152: "\xbd"}


def key(pattern):
    return "".join(c for c in pattern if not c.isdigit())


def main(src, dst):
    seen = {}
    dropped = []
    for raw in open(src, encoding="utf-8"):
        pat = raw.strip()
        if not pat or pat.startswith("%"):
            continue
        pat = pat.translate(FOLD)
        try:
            pat.encode("latin-1")
        except UnicodeEncodeError:
            dropped.append(pat)
            continue
        k = key(pat)
        if k in seen and seen[k] != pat:
            # Same key, different weights: keep the stronger. Only happens
            # through the apostrophe fold, where the two spellings agree.
            if pat != seen[k]:
                dropped.append("%s (conflicts with %s)" % (pat, seen[k]))
            continue
        seen[k] = pat

    out = [seen[k] for k in sorted(seen)]
    with open(dst, "wb") as f:
        f.write("\n".join(out).encode("latin-1") + b"\n")

    print("%s -> %s" % (src, dst))
    print("  %d patterns, %d bytes" % (len(out), sum(len(p) + 1 for p in out)))
    print("  longest letters-only key: %d chars  <- LETTERS_MAX for this language"
          % max(len(key(p)) for p in out))
    chars = sorted({c for p in out for c in p if not c.isdigit()})
    print("  alphabet: %s" % "".join(chars))
    if dropped:
        print("  dropped %d: %s" % (len(dropped), ", ".join(dropped[:5])))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2])
