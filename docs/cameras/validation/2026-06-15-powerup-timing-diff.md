# Power-up sequence diff: mainline holds the sensor in RESET

**Date:** 2026-06-15. Follows
[`2026-06-15-legacy-vs-mainline-cci-diff.md`](./2026-06-15-legacy-vs-mainline-cci-diff.md),
which proved the CCI register programming is byte-identical and relocated the NACK
to the **sensor power-up / reset / MCLK sequence**. This note finds the exact defect.

## Method

Added per-step timestamp breadcrumbs to both kernels' `cam_sensor_core_power_up`
loop (legacy: `vamos-legacy-pwrup: step-begin/pre-delay/end`; mainline already had
`vamos-cam-sensor: power idx / step done / delay`). Ran the probe on each, captured
the slot-by-slot power-up timeline. seq_type enum (shared): 0=MCLK, 1=VANA, 2=VDIG,
3=VIO, 8=RESET.

## Legacy 4.9 — power-up that WORKS (3/3 probe OK)

Six steps, slot 0 (`data/2026-06-15-legacy-pwrup-timing.txt`):

| idx | seq | step | config | delay | t (s) |
| --- | --- | --- | --- | --- | --- |
| 0 | 3 | VIO | 0 | 0 | 233.2986 |
| 1 | 1 | VANA | 0 | 0 | 233.2989 |
| 2 | 2 | VDIG | 0 | 0 | 233.2994 |
| 3 | 8 | **RESET low** | 0 | 1ms | 233.3001 |
| 4 | 0 | MCLK 24 MHz | 24000000 | 1ms | 233.3022 |
| 5 | 8 | **RESET high** | **1** | **34ms** | 233.3048 → 233.3452 |
| — | — | first CCI read → **ACK 0x5304** | | | 233.3465 |

Order: VIO → VANA → VDIG → RESET-low → MCLK → **RESET-high + 34 ms settle** → read.
Reset is deasserted *after* MCLK is running, then the sensor gets 34 ms to boot
before the first I2C transaction. Whole sequence ≈ 48 ms.

## Mainline camera_kt — power-up that FAILS (0/3, NACK)

`power_up core ... size=6`, but the loop executes only **5 distinct indices**:

| idx | seq | step | config | t (s) |
| --- | --- | --- | --- | --- |
| 0 | 3 | VIO | 0 | 77.978 |
| 1 | 1 | VANA | 0 | 78.073 |
| 2 | 2 | VDIG | 0 | 78.274 |
| 3 | 8 | **RESET low** | 0 | 78.529 |
| 4 | 0 | MCLK 24 MHz | 24000000 | 78.702 |
| **5** | — | **(never executed)** | — | — |
| — | — | `power_up core rc=0` | | 80.454 |
| — | — | CCI read → **NACK** (`status0=0x10000000`) | | 88.61 |

**The reset GPIO (559) is driven LOW at idx3 and never returns HIGH during power-up.**
Every `gpio-set gpio=559` in the power-up window is `value=0 / after_level=0`. The
only `seq=8 config=1` (reset-high) in the whole log is in **power-DOWN**
(`power_down idx=1 seq=8 config=1`, t=92.4). So:

> The OS04C10 is read **while still held in reset**. Its I2C block is dead →
> every address NACKs. This fully explains the NACK with otherwise-correct rails,
> MCLK, CCI pads, and CCI register programming.

Secondary differences (consequences, not the root cause): mainline inter-step deltas
are ~100–250 ms (vs legacy <1 ms) and the genpd-free MCLK enable still takes ~1 s;
none of that matters while reset is held low.

## Root cause — PINNED (2026-06-15, kernel #195 w/ patch 0035)

The parsed array is **complete and correct** — `power_setting DUMP size=6`:

```
[0] seq_type=3 (VIO)   config=0
[1] seq_type=1 (VANA)  config=0
[2] seq_type=2 (VDIG)  config=0
[3] seq_type=8 (RESET) config=0 delay=1     reset LOW
[4] seq_type=0 (MCLK)  config=24000000 delay=1
[5] seq_type=8 (RESET) config=1 delay=34    reset HIGH + 34ms  <- PRESENT, == legacy
```

So parsing is NOT the problem. The loop runs idx 0–4 then **aborts at idx 4 (MCLK)**:

```
mclk begin idx=4
power domain enable before-mclk end rc=0
mclk enable clocks end rc=-16            <-- clk_prepare_enable(cam_cc_mclk0_clk) = -EBUSY
clk enable failed  ->  goto power_up_failed
```

**`cam_soc_util_clk_enable()` for the MCLK branch returns -16 (EBUSY)**, which does
`goto power_up_failed` — so **idx 5 (reset-deassert) never runs** and the sensor is
read while held in reset → NACK. The `-16` is from `clk_prepare_enable()` on
`cam_cc_mclk0_clk`; clk_summary shows enable_count=0 (not a leaked refcount), so the
MCLK branch's **parent/gate (CAMCC under TITAN_TOP_GDSC) is not powered** when the
clock is enabled.

### This is a regression from the genpd removal (commit 539ac10)

Pre-genpd-removal (kernels #191/#192, `2026-06-14-sensor-nack-rootcause.md`) reported
**MCLK genuinely 24 MHz at the pad, `cam_cc_mclk0_clk enable=1`** — MCLK enable
*worked*. Post-removal (#194/#195) MCLK enable returns **-16**. So removing
`power-domains` from the sensor node **broke MCLK**: on mainline, the genpd
(TITAN_TOP_GDSC via `pm_runtime`) is what powers the CAMCC gate the MCLK branch needs.
The `cam_clk-supply` regulator alone does not satisfy the mainline clk framework's
gating like it did on legacy 4.9.

### Corrected understanding

- The **genpd IS required** on mainline for MCLK to enable — contrary to the earlier
  conclusion. What was wrong before was only the genpd's *timing* (enabled at the
  first VANA regulator step instead of at/with MCLK).
- The NACK seen pre-removal was a *different* failure (sensor reached the CCI read
  with MCLK up). Post-removal, the probe fails earlier (MCLK -EBUSY → reset held).

## Fix applied + result (2026-06-15, kernel #196)

Re-added `power-domains = <&clock_camcc TITAN_TOP_GDSC>` to all three sensor nodes
(kept patch 0028's MCLK-step deferral). Built, flashed, probed. The power-up is now
**fully correct and byte-identical to legacy**:

```
power idx=0 VIO  -> idx1 VANA -> idx2 VDIG -> idx3 RESET-low(+1ms)
 -> idx4 MCLK  (mclk enable clocks end rc=0)   <-- -16 EBUSY is GONE
 -> idx5 RESET-high (gpio 559 val=1, after=1) + 34ms settle
 -> power_up step done idx=5 rc=0  ->  power_up end rc=0
```

Confirmed fixes vs the genpd-removed build:
- MCLK enable now `rc=0` (was `-16` EBUSY) — genpd powers the CAMCC gate.
- idx5 reset-deassert now **runs**; reset GPIO 559 goes HIGH; 34 ms settle honored.
- Full 6-step power-up completes, ordering + timing match legacy 1:1.

**But the CCI chip-ID read STILL NACKs** (`status0=0x10000000`, identical signature).
Verified on a clean single power-up (slot 1): power_up end rc=0 → read → NACK.

### Where this leaves us

We have now made the mainline path identical to legacy across **every** layer we can
instrument: power rails, genpd, MCLK (24 MHz, rc=0), reset-deassert + 34 ms settle,
pinctrl/pad mux, and the CCI register programming (SET_PARAM/SCL/MISC/queue, proven
byte-identical earlier). Legacy ACKs; mainline NACKs. The remaining difference is
*not* in any of those layers.

Surviving candidates (narrow):
1. **CCI master init / soft-reset state.** Legacy's IRQ log showed `RST_DONE_ACK`
   (`status0=0x01000000`) immediately before each read — the CCI master is reset
   per-transaction. Confirm mainline issues the same CCI master reset/init before the
   read, and that the master isn't left in a halted/dirty state from a prior txn.
2. **Timing between reset-deassert and the read.** Legacy: ~1.3 ms from 34 ms-settle
   end to the read. Measure mainline's settle-end → QUEUE_START delta; if the read
   fires too soon (or the genpd toggling re-gates MCLK between settle and read), the
   sensor may not be ready.
3. **MCLK continuity across the read.** Verify MCLK stays on continuously from idx4
   through the read (the genpd disable/re-enable or a clk refcount could gate it
   between power-up and match_id).
4. **Electrical (scope-only).** Everything kernel-visible now matches.

The genpd re-add is correct and is being committed; it both restores MCLK and
completes the power-up. The genpd-removal commit (539ac10) was wrong about genpd being
unnecessary — corrected here.
