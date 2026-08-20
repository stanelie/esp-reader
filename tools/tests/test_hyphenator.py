#!/usr/bin/env python3
"""Offline checks for device/lib/hyphenator.py.

The port must not have changed anything. The implementation it came from was
validated against Ned Batchelder's reference hyphenate.py over 234k words, and
that validation only transfers if this version behaves identically - so run
both over the system word list and require an exact match.

    python3 tools/tests/test_hyphenator.py [path/to/badger/hyphenator.py]

Without the Badger file it still checks the properties that must hold on their
own: pieces rejoin to the original word, breaks respect the two-letter margins,
and hyphenate_split never returns a head wider than the space it was given.
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
WORDS = "/usr/share/dict/words"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    ours = load(os.path.join(ROOT, "device", "lib", "hyphenator.py"), "ours")
    ours._PATTERNS_PATH = os.path.join(ROOT, "device", "hyphen_en.bin")

    words = []
    if os.path.exists(WORDS):
        words = [w.strip() for w in open(WORDS, encoding="utf-8", errors="ignore")]
        words = [w for w in words if w.isalpha() and w.isascii()]
    if not words:
        words = ("hyphenation extraordinary beautiful university development "
                 "communication responsibility international").split()

    failures = 0

    # 1. identical to the implementation this was ported from
    badger = sys.argv[1] if len(sys.argv) > 1 else None
    if badger and os.path.exists(badger):
        theirs = load(badger, "theirs")
        theirs._PATTERNS_PATH = os.path.join(ROOT, "device", "hyphen_en.bin")
        bad = [w for w in words if ours.hyphenate(w) != theirs.hyphenate(w)]
        print("1. vs the Badger implementation, %d words: %s"
              % (len(words), "IDENTICAL" if not bad else "MISMATCH %s" % bad[:5]))
        failures += bool(bad)
    else:
        print("1. vs Badger: skipped (pass its hyphenator.py as argv[1])")

    # 2. lossless: the pieces are the word, and nothing else
    bad = [w for w in words if "".join(ours.hyphenate(w)) != w]
    print("2. pieces rejoin to the original, %d words: %s"
          % (len(words), "yes" if not bad else "NO %s" % bad[:5]))
    failures += bool(bad)

    # 3. hyphenate() promises two letters on each side of a break - that is
    #    lefthyphenmin=2 / righthyphenmin=2, exactly the reference's rule.
    bad = []
    for w in words:
        pieces = ours.hyphenate(w)
        if len(pieces) > 1 and (len(pieces[0]) < 2 or len(pieces[-1]) < 2):
            bad.append((w, "-".join(pieces)))
    print("3. hyphenate() keeps 2 letters each side: %s"
          % ("yes" if not bad else "NO %s" % bad[:5]))
    failures += bool(bad)

    # 3b. hyphenate_split tightens the tail to MIN_TAIL, because a two-letter
    #     tail is legal by the algorithm and wrong on a page
    bad = []
    for w in words[:20000]:
        head, rest = ours.hyphenate_split(w, 10**6, len)
        if head and head[-1] == "-" and len(rest) < ours.MIN_TAIL:
            bad.append((w, head, rest))
    print("3b. hyphenate_split leaves >= %d letters after a break: %s"
          % (ours.MIN_TAIL, "yes" if not bad else "NO %s" % bad[:5]))
    failures += bool(bad)

    # 4. hyphenate_split must honour its width budget, and its two returns must
    #    reconstruct the word with exactly one hyphen added
    measure = lambda s: len(s) * 7
    bad = []
    # dashed words too - the plain word list has none, and they take the other
    # branch of hyphenate_split entirely
    for w in list(words[:20000]) + ["superhighway\u2014jacking", "well-to-do",
                                    "mind-your-own-business", "in\u2013out",
                                    "up-to-date", "condition\u2014cyber"]:
        for budget in (35, 70, 140):
            head, rest = ours.hyphenate_split(w, budget, measure)
            if head is None:
                continue
            # Either the break used a dash the word already had, in which case
            # nothing was added, or Liang added exactly one hyphen. A head
            # ending in "-" is ambiguous between the two - "well-to-" added
            # nothing - so accept whichever reconstruction holds rather than
            # guessing from the last character.
            if measure(head) > budget:
                bad.append((w, budget, head, "over budget"))
            elif not (head + rest == w
                      or (head[-1] == "-" and head[:-1] + rest == w)):
                bad.append((w, budget, head, "does not reconstruct"))
    print("4. hyphenate_split respects the budget and reconstructs: %s"
          % ("yes" if not bad else "NO %s" % bad[:3]))
    failures += bool(bad)

    print("\n%s" % ("ALL CHECKS PASSED" if not failures
                    else "%d CHECK(S) FAILED" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
