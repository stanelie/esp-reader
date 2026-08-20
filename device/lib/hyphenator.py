"""On-device hyphenation (Frank Liang's algorithm, as used by TeX).

Ported verbatim from the Badger 2040 reader, where it was validated against Ned
Batchelder's reference hyphenate.py over 234,000 words. Only the pattern path
differs. tools/tests/test_hyphenator.py re-runs that comparison over the system
word list and requires identical output, so the port stays honest.

English only. The patterns are language-specific and mixing sets produces bad
breaks in both, so another language means another blob and a way to choose
between them - see tools/build_hyphen_patterns.py, which already handles the
latin-1 folding French would need.

The ~4900 patterns live in `hyphen_en.bin` as a single sorted, newline-delimited
blob, loaded once into one bytes object (~31 KB) and binary-searched in place -
no per-pattern Python objects, no big dict. That was to fit an RP2040; here it
is simply cheap.

Patterns and exceptions are public domain (Knuth & Liang; ushyphmax by
Gerard D.C. Kuiken). Algorithm after Ned Batchelder's public-domain
hyphenate.py; this implementation reproduces its output exactly.
"""

_PATTERNS_PATH = "/hyphen_en.bin"
_LETTERS_MAX = 9  # longest pattern key (letters incl. boundary dots)
MIN_TAIL = 3      # letters that must follow a break (see hyphenate_split)

# Characters a line may break after, the break point being the character
# itself. The Badger reader only needed the ASCII hyphen because its EPUB
# converter folded dashes to it; this reader shows the author's text as written,
# so the em and en dashes have to be recognised where they are. They are common
# enough in prose that not breaking on them leaves visibly short lines -
# "superhighway\u2014jacking" is 166 px and fits nowhere.
BREAK_AFTER = "-\u2014\u2013"   # hyphen, em dash, en dash


def _breakable(word):
    for c in BREAK_AFTER:
        if c in word:
            return True
    return False

# Words Knuth listed as exceptions (hyphen points precomputed).
EXCEPTIONS = {
    'associate': [0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0],
    'associates': [0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0],
    'declination': [0, 0, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0],
    'obligatory': [0, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0],
    'philanthropic': [0, 0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0],
    'present': [0, 0, 0, 0, 0, 0, 0, 0, 0],
    'presents': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    'project': [0, 0, 0, 0, 0, 0, 0, 0, 0],
    'projects': [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    'reciprocity': [0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0],
    'recognizance': [0, 0, 0, 1, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0],
    'reformation': [0, 0, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0],
    'retribution': [0, 0, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0],
    'table': [0, 0, 0, 1, 0, 0, 0],
}

_BLOB = None


def _load():
    global _BLOB
    if _BLOB is None:
        with open(_PATTERNS_PATH, "rb") as f:
            _BLOB = f.read()
    return _BLOB


def _cmp_key(blob, ls, le, kbuf, ks, ke):
    """Compare the letters-only key of pattern line blob[ls:le] against
    kbuf[ks:ke], lexicographically, without allocating (no slice of either
    buffer is ever materialized). Returns -1, 0 or 1."""
    ki = ks
    p = ls
    while p < le:
        c = blob[p]
        if 48 <= c <= 57:   # skip digits - they aren't part of the key
            p += 1
            continue
        if ki >= ke:
            return 1        # line key longer, key is a prefix of it
        kc = kbuf[ki]
        if c != kc:
            return -1 if c < kc else 1
        ki += 1
        p += 1
    return 0 if ki == ke else -1


def _points_at(blob, ls, le):
    # digit vector of pattern blob[ls:le], e.g. b".ach4" -> [0, 0, 0, 0, 4]
    pts = [0]
    p = ls
    while p < le:
        c = blob[p]
        if 48 <= c <= 57:
            pts[-1] = c - 48
        else:
            pts.append(0)
        p += 1
    return pts


def _lookup(kbuf, ks, ke):
    """Binary-search the sorted, newline-delimited blob for a pattern whose
    letters-only key equals kbuf[ks:ke]. Return its digit vector or None.

    Takes a buffer + range instead of a pre-sliced key so the caller never
    allocates a new bytes object per candidate substring - hyphenate() tries
    O(word_length * _LETTERS_MAX) substrings per word, and materializing each
    one was generating enough short-lived garbage to fragment the RP2040 heap
    over a reading session (observed as a MemoryError much later, in an
    unrelated large allocation like the display's framebuffer rotation)."""
    blob = _BLOB
    lo = 0
    hi = len(blob)
    while lo < hi:
        mid = (lo + hi) // 2
        ls = blob.rfind(b"\n", 0, mid) + 1
        le = blob.find(b"\n", mid)
        if le < 0:
            le = len(blob)
        c = _cmp_key(blob, ls, le, kbuf, ks, ke)
        if c == 0:
            return _points_at(blob, ls, le)
        if c < 0:
            lo = le + 1
        else:
            hi = ls
    return None


def hyphenate(word):
    """Return `word` split into pieces at legal hyphenation points."""
    if len(word) <= 4:
        return [word]
    lw = word.lower()
    # only plain ASCII letters are handled; anything else is left whole
    for ch in lw:
        if not ("a" <= ch <= "z"):
            return [word]

    if lw in EXCEPTIONS:
        points = EXCEPTIONS[lw]
    else:
        _load()
        work = "." + lw + "."
        wb = work.encode("ascii")
        n = len(wb)
        points = [0] * (n + 1)
        for i in range(n):
            top = min(i + _LETTERS_MAX, n)
            for j in range(i + 1, top + 1):
                pts = _lookup(wb, i, j)
                if pts:
                    for k in range(len(pts)):
                        if points[i + k] < pts[k]:
                            points[i + k] = pts[k]
        # never break in the first two or last two letters
        points[1] = points[2] = points[-2] = points[-3] = 0

    pieces = [""]
    for c, p in zip(word, points[2:]):
        pieces[-1] += c
        if p % 2:
            pieces.append("")
    return pieces


def hyphenate_split(word, space_left, measure=len):
    """Split `word` for line-wrapping. Return (head, rest) where `head` is the
    exact text to place on the current line - it already includes its trailing
    hyphen, whether that hyphen was added by Liang's algorithm or was already in
    the word - and `rest` continues on the next line. `head` must satisfy
    measure(head) <= space_left and is the longest such legal break, or
    (None, None) if the word can't/shouldn't be broken here. `measure` lets the
    caller budget in pixels (proportional font) instead of characters."""
    if _breakable(word):
        # The word already contains a dash, so break right after one. The dash
        # is in the text, so `head` is just the prefix up to and including it -
        # nothing is added.
        #
        # Two characters each side here rather than MIN_TAIL. That rule exists
        # to stop Liang inventing an ugly split like "walk-ed"; a dash the
        # author wrote is a legitimate break point whatever follows it, and
        # "well-to-do" should be allowed to break where its own hyphens are.
        best_head = None
        best_rest = None
        for i in range(2, len(word) - 2):
            if word[i] in BREAK_AFTER:
                head = word[:i + 1]
                if measure(head) > space_left:
                    break
                best_head, best_rest = head, word[i + 1:]
        if best_head is None:
            return None, None
        return best_head, best_rest

    # All-letter word: Liang soft hyphenation, which adds a trailing "-".
    if len(word) < 5:
        return None, None
    pieces = hyphenate(word)
    if len(pieces) < 2:
        return None, None
    best_head = None
    best_rest = None
    acc = ""
    for idx in range(len(pieces) - 1):
        acc += pieces[idx]
        head = acc + "-"
        if measure(head) > space_left:
            break
        # hyphenate() enforces two letters on each side, which is what Ned
        # Batchelder's reference does and what this port is validated against.
        # English convention wants three after the break, though - "ASCI-Is" and
        # "walk-ed" are legal by the algorithm and wrong on the page. Enforcing
        # it here rather than in hyphenate() keeps the algorithm identical to
        # the reference and makes this a typesetting choice, which is what it
        # is.
        if len(word) - len(acc) < MIN_TAIL:
            continue
        best_head, best_rest = head, word[len(acc):]
    if best_head is None:
        return None, None
    return best_head, best_rest
