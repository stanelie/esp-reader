#!/usr/bin/env python3
"""Fit CAL_SLOPE / CAL_OFFSET from a battery_cal.csv produced on the device.

    python3 tools/battery_fit.py /media/.../CIRCUITPY/battery_cal.csv

The reader converts raw counts to volts with

    volts = raw * CAL_SLOPE + CAL_OFFSET

A bare multiplier cannot fit this: the ESP32-S3 converter does not pass through
the origin, so any single constant is right at exactly one voltage and drifts
everywhere else. Two points give you a line; three or more let the leave-one-out
check below tell you whether it is actually linear across the range you care
about, or whether you have merely drawn a line through two ends.
"""
import sys, csv


def fit(xs, ys):
    n = len(xs)
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        raise SystemExit("error: all readings identical - nothing to fit")
    a = (n * sxy - sx * sy) / denom
    b = (sy - a * sx) / n
    return a, b


def main(path):
    pts = []
    railed_rows = []
    used = "mean"
    with open(path, newline="") as f:
        for row in csv.DictReader(r for r in f if not r.lstrip().startswith("#")):
            v = (row.get("volts") or "").strip()
            if not v:
                continue
            # Prefer the median. Individual samples on this hardware
            # intermittently rail to full scale - 42-43% of a batch, in one
            # observed run - which leaves the mean meaningless while the median
            # is untouched. Fitting the mean of a contaminated row would bake
            # that straight into the constants.
            med = (row.get("median") or "").strip()
            if med:
                used = "median"
                raw = float(med)
            else:
                raw = float(row["mean"])
            r = (row.get("railed") or "").strip()
            if r and int(r) > 0:
                railed_rows.append((row.get("idx", "?"), int(r)))
            pts.append((raw, float(v), row.get("idx", "?")))

    if railed_rows:
        print("WARNING: %d row(s) contained railed samples: %s"
              % (len(railed_rows),
                 ", ".join("idx %s (%d)" % r for r in railed_rows)))
        print("  Using the median keeps them usable, but a row with railed\n"
              "  samples is evidence the divider misbehaved during it.\n")

    if len(pts) < 2:
        raise SystemExit(
            "error: need at least 2 points with the `volts` column filled in "
            "(found %d).\nOpen %s and write the PPK2 voltage next to each row."
            % (len(pts), path))

    pts.sort(key=lambda p: p[1])
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    a, b = fit(xs, ys)

    print("%d points, fitted on the `%s` column:" % (len(pts), used))
    print("  %-6s %-10s %-10s %s" % ("volts", "raw", "predicted", "residual"))
    worst = 0.0
    for x, y, idx in pts:
        pred = a * x + b
        r = (y - pred) * 1000.0
        worst = max(worst, abs(r))
        print("  %-6.2f %-10.1f %-10.4f %+.1f mV" % (y, x, pred, r))

    print("\nCAL_SLOPE  = %.9g" % a)
    print("CAL_OFFSET = %.9g" % b)
    print("\nworst residual %.1f mV" % worst)
    print("0 V extrapolates to raw %.0f (the offset this is correcting for)"
          % (-b / a))

    if len(pts) >= 3:
        print("\nleave-one-out - each point predicted by a fit of the others:")
        worst_loo = 0.0
        for i in range(len(pts)):
            rest_x = xs[:i] + xs[i + 1:]
            rest_y = ys[:i] + ys[i + 1:]
            aa, bb = fit(rest_x, rest_y)
            pred = aa * xs[i] + bb
            err_mv = (ys[i] - pred) * 1000.0
            err_counts = (ys[i] - pred) / aa
            worst_loo = max(worst_loo, abs(err_mv))
            print("  %.2f V held out -> predicted %.4f V  (%+.1f mV, %+.1f counts)"
                  % (ys[i], pred, err_mv, err_counts))
        print("worst held-out error %.1f mV" % worst_loo)
        if worst_loo > 30:
            print("  ^ that is large. Suspect a mislabelled voltage, a reading\n"
                  "    taken with USB attached, or genuine non-linearity.")
    else:
        print("\nOnly 2 points, so the fit is exact by construction and tells you\n"
              "nothing about linearity. Take a third in the middle of the range.")

    # One reading per voltage, medians averaged if a voltage was measured more
    # than once - which is what point mode's REPEATS produces.
    by_v = {}
    for x, y, idx in pts:
        by_v.setdefault(y, []).append(x)
    table = tuple((sum(v) / len(v), k) for k, v in sorted(by_v.items()))

    print("\nPaste into the panel profile in device/code.py:")
    print('        "cal_points": (%s),'
          % ", ".join("(%.1f, %.2f)" % t for t in table))

    if worst > 5:
        print("""
  Note: the linear fit above has %.0f mV of residual, so the reader will NOT
  reproduce it - raw_to_volts() interpolates between the points instead, which
  is exact at each measured voltage. The fit is printed as a linearity check,
  not as something to paste.""" % worst)
    print("\n(The line fitted above, if you want it for comparison:"
          " slope %.9g, offset %.9g)" % (a, b))

    lo, hi = min(ys), max(ys)
    if lo > 3.45 or hi < 4.1:
        print("\nnote: points span %.2f-%.2f V. The reader maps 3.20-4.20 V to\n"
              "0-100%%, so cover at least ~3.4 to ~4.2 V or the ends are\n"
              "extrapolation." % (lo, hi))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
