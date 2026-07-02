#!/usr/bin/env python3
"""Scan Qualcomm SPMI PMIC peripherals via regmap debugfs and print a
diffable state table: for every 256-byte peripheral on every PMIC USID,
the TYPE/SUBTYPE header plus the 0x40-0x5F control window (enable, mode,
voltage set-points live there for regulators/switches/GPIOs).

Works on both legacy 4.9 and mainline 6.18 (any kernel whose PMIC regmaps
appear under /sys/kernel/debug/regmap/). Lines with no readable registers
are skipped, so the output diffs cleanly across kernels.

Run as root. Output to stdout; redirect to a file.
"""
import glob
import os
import re
import sys

LINE = 9  # regmap debugfs: "xxxx: yy\n"


def read_regs(f, start, count):
    f.seek(start * LINE)
    data = f.read(count * LINE).decode(errors='replace')
    vals = []
    for i, line in enumerate(data.splitlines()):
        m = re.match(r'^([0-9a-f]{4}): ([0-9a-fXx]{2})$', line)
        vals.append(m.group(2) if m else '??')
    while len(vals) < count:
        vals.append('??')
    return vals


def main():
    dirs = sorted(d for d in glob.glob('/sys/kernel/debug/regmap/*')
                  if re.search(r'[/-]0[0-3]$', d))
    if not dirs:
        sys.exit('no PMIC regmaps under /sys/kernel/debug/regmap/')
    for d in dirs:
        name = os.path.basename(d)
        try:
            f = open(os.path.join(d, 'registers'), 'rb', buffering=0)
        except OSError as e:
            print(f'# {name}: {e}')
            continue
        for periph in range(256):
            base = periph << 8
            hdr = read_regs(f, base + 0x04, 2)      # TYPE, SUBTYPE
            if all(v in ('XX', '??', 'xx') for v in hdr):
                continue
            status = read_regs(f, base + 0x08, 4)   # STATUS window
            ctl = read_regs(f, base + 0x40, 32)     # EN/MODE/VSET window
            print(f'{name} {base:04x} type={hdr[0]} sub={hdr[1]} '
                  f'st={" ".join(status)} ctl={" ".join(ctl)}')
        f.close()


if __name__ == '__main__':
    main()
