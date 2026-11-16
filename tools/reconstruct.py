#!/usr/bin/env python3
"""
Reconstruct the hit-object tail that was truncated out of the <=1.20x rate
difficulties of "DJ Nanasaki - Unorthodox Red".

Background
----------
Every difficulty in the set was cut at ~188045 ms of its OWN timeline, which is
exactly the duration of audio_1.45.ogg (3:08.045).  The rate generator clearly
clamped every chart to the length of that one audio file instead of to its own.
Charts whose natural length is already below that (1.25x and faster) survived
intact with 3648 objects; everything at 1.20x and slower lost its tail:

    1.00x 2707/3648   1.05x 2911/3648   1.10x 3116/3648
    1.15x 3321/3648   1.20x 3482/3648

Method
------
The five intact charts (1.25 .. 1.45) share the exact same object order, so the
missing objects can be recovered from them.  For each object we intersect the
constraints each intact rate puts on the original 1.00x timestamp T:

  * 1.30x is a perfect  t = floor(T / 1.3)  =>  T in [1.3t, 1.3t + 1.3)
  * the other rates round with +-1 ms of jitter (they came from a noisier
    pipeline) =>  T in (R(t-1), R(t+1))

The intersection pins T to one or two candidate integers; ties are broken with
the least-squares consensus of all five rates.  Verified against the 2707
objects that still exist at 1.00x: the true value is inside the candidate set
100% of the time and the picked value is exact for ~97% of them, worst case
1 ms off (OD8 mania's strictest window is +-16 ms, so this is inaudible and
unjudgeable).

Only the missing tail is appended - every line that already exists in a file is
left byte-for-byte untouched.
"""

import glob
import math
import os
import re
import sys
from fractions import Fraction as Fr

DIR = "2654868 DJ Nanasaki - Unorthodox Red"
CUT_MS = 188045  # duration of audio_1.45.ogg, the bogus clamp the generator used


# --------------------------------------------------------------------------- IO
class Osu:
    def __init__(self, path):
        self.path = path
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            raw = fh.read()
        self.nl = "\r\n" if "\r\n" in raw else "\n"
        head, _, body = raw.partition("[HitObjects]")
        self.head = head + "[HitObjects]"
        self.lines = [l for l in body.split(self.nl)]
        # keep structure: strip the leading empty piece produced by the split
        while self.lines and self.lines[0] == "":
            self.lines.pop(0)
        self.objs = [parse(l) for l in self.lines if l.strip()]

    @property
    def rate(self):
        m = re.search(r"\[Unorthodox \[(\d+(?:\.\d+)?)x\]\]", self.path)
        return Fr(m.group(1)) if m else Fr(1)


def parse(line):
    p = line.strip().split(",")
    typ = int(p[3])
    end = int(p[5].split(":")[0]) if typ & 128 else None
    return dict(x=int(p[0]), y=int(p[1]), time=int(p[2]), type=typ,
                hs=int(p[4]), end=end, sample=p[5].split(":", 1)[1] if typ & 128 else p[5])


def render(o):
    if o["type"] & 128:
        return f'{o["x"]},{o["y"]},{o["time"]},{o["type"]},{o["hs"]},{o["end"]}:{o["sample"]}'
    return f'{o["x"]},{o["y"]},{o["time"]},{o["type"]},{o["hs"]},{o["sample"]}'


# ------------------------------------------------------------------ reconstruct
def candidates(times, rates):
    """Integer 1.00x timestamps compatible with every intact rate chart."""
    lo, hi = Fr(-10 ** 9), Fr(10 ** 9)
    eps = Fr(1, 1000)
    for r, t in zip(rates, times):
        t = Fr(t)
        if r == Fr(13, 10):          # exact floor model
            l, h = r * t, r * (t + 1) - eps
        else:                        # +-1 ms jitter
            l, h = r * (t - 1) + eps, r * (t + 1) - eps
        lo, hi = max(lo, l), min(hi, h)
    return [T for T in range(math.ceil(lo), math.floor(hi) + 1)], lo, hi


def estimate(times, rates):
    """Pick the best integer 1.00x timestamp for one object."""
    cand, lo, hi = candidates(times, rates)
    # least-squares consensus of T ~= r * t  (weight 1/r: faster rates are
    # coarser in the 1.00x domain, so they get less say)
    num = sum(float(r) * t / float(r) for r, t in zip(rates, times))
    den = sum(1.0 / float(r) for r in rates)
    cons = sum(float(r) * t * (1.0 / float(r)) for r, t in zip(rates, times)) / den
    if not cand:  # should not happen; fall back to the consensus
        return int(round(cons)), cons, 0
    return min(cand, key=lambda T: (abs(T - cons), T)), cons, len(cand)


def main():
    os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    paths = sorted(glob.glob(os.path.join(DIR, "*.osu")))
    maps = {}
    for p in paths:
        o = Osu(p)
        maps[o.rate] = o

    intact = sorted(r for r, m in maps.items() if len(m.objs) == 3648)
    total = max(len(m.objs) for m in maps.values())
    print(f"intact rates: {[str(r) for r in intact]}  (objects: {total})")

    base = maps[Fr(1)]

    # ---- reconstruct the master 1.00x timeline -----------------------------
    master = []
    multi = 0
    for j in range(total):
        ref = maps[intact[0]].objs[j]
        starts = [maps[r].objs[j]["time"] for r in intact]
        T, cons, nc = estimate(starts, intact)
        multi += nc > 1
        E = None
        if ref["type"] & 128:
            ends = [maps[r].objs[j]["end"] for r in intact]
            E, _, _ = estimate(ends, intact)
        master.append(dict(ref, time=T, end=E))

    # ---- validate against the surviving 1.00x prefix ------------------------
    errs = {}
    for j, real in enumerate(base.objs):
        d = abs(master[j]["time"] - real["time"])
        errs[d] = errs.get(d, 0) + 1
        if real["end"] is not None:
            d = abs(master[j]["end"] - real["end"])
            errs[d] = errs.get(d, 0) + 1
    n = sum(errs.values())
    print(f"validation on the {len(base.objs)} surviving 1.00x objects "
          f"({n} timestamps): " + ", ".join(f"{k}ms={v} ({v/n:.1%})" for k, v in sorted(errs.items())))
    print(f"ambiguous (2 candidates): {multi}/{total}")
    if max(errs) > 1:
        sys.exit("reconstruction error > 1 ms - aborting")

    # keep the real values where we still have them
    for j, real in enumerate(base.objs):
        master[j]["time"] = real["time"]
        master[j]["end"] = real["end"]

    # ---- append the missing tail to every truncated difficulty --------------
    for r in sorted(maps):
        m = maps[r]
        have = len(m.objs)
        if have == total:
            print(f"  {os.path.basename(m.path)}: complete, untouched")
            continue
        tail = []
        for j in range(have, total):
            src = master[j]
            o = dict(src)
            o["time"] = int(round(src["time"] / r))
            if src["end"] is not None:
                o["end"] = int(round(src["end"] / r))
            tail.append(o)

        # sanity: times must stay sorted and must not collide with what is there
        last = m.objs[-1]["time"]
        assert tail[0]["time"] > last, (r, tail[0]["time"], last)
        for a, b in zip(tail, tail[1:]):
            assert b["time"] >= a["time"], (r, a, b)
        for o in tail:
            if o["end"] is not None:
                assert o["end"] > o["time"], (r, o)

        body = m.nl.join(m.lines).rstrip("\r\n")
        body += m.nl + m.nl.join(render(o) for o in tail) + m.nl
        with open(m.path, "w", encoding="utf-8", newline="") as fh:
            fh.write(m.head + m.nl + body)
        print(f"  {os.path.basename(m.path)}: {have} -> {total} objects "
              f"(+{len(tail)}), ends {last} -> {tail[-1]['time']} ms")


if __name__ == "__main__":
    main()
