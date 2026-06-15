# Legacy 4.9 (ACK) vs mainline camera_kt (NACK) — instrumented CCI diff

**Date:** 2026-06-15. The decisive experiment from
[`2026-06-15-genpd-sensor-ordering-fix.md`](./2026-06-15-genpd-sensor-ordering-fix.md):
flash an instrumented legacy 4.9 kernel, capture a *successful* OS04C10 chip-ID read,
and diff its CCI register programming word-for-word against the mainline NACK.

## Method

- Built an instrumented legacy AGNOS kernel (`agnos-builder`, `4.9.103 #2`) with
  `pr_emerg` breadcrumbs added to `cam_cci_core.c` (`set_clk_param`, `cam_cci_read`
  set_param + queue-ready word counts + read-done) and `cam_cci_dev.c` (the
  `cam_cci_irq` handler: raw `status0/status1` + M0/M1 read-buf level).
- Flashed it to mici, ran `spectra_camera_test_legacy --sensor os04c10 --probe-only`
  → **3/3 PROBE OK** (`sensor_id:0x5304` for all three slots).
- Full legacy dump: `tmp/device-logs/legacy_cci_instrumented_dump.txt`.
- Mainline reference: kernel `6.18.0-vamos #194` (commit 539ac10), same probe,
  0/3, breadcrumbs from `2026-06-14-sensor-nack-rootcause.md` + live capture.

## The read, slot 0 / master 0 / sid 0x36 — side by side

| field | legacy 4.9 (ACK) | mainline camera_kt (NACK) |
| --- | --- | --- |
| `set_param` | `0x00030361` | `0x00030361` |
| `sid` | 0x36 | 0x36 |
| `retries` | 3 | 3 |
| `id_map` | 0 | 0 |
| `freq_mode` | 1 (FAST) | 1 (FAST) |
| `addr` / `addr_type` / `num_byte` | 0x300a / 2 / 2 | 0x300a / 2 / 2 |
| `scl_ctl` | `0x00260038` | `0x00260038` |
| `misc_ctl` | `0x00000063` | `0x00000063` |
| queue `cur_word_cnt` (= words loaded) | **5** | **5** (`exec=0x5`) |
| `qstart` | 0x2 (M0/Q1) | 0x2 (M0/Q1) |
| **IRQ after QUEUE_START** | `status0=0x00000001` = **RD_DONE**, `m0_rd_buf=0x1` | `status0=0x10000000` = **NACK**, `read_level=0x0` |
| result | `read_words=1 exp_words=1` → **0x5304** | `read status error 0xffffffea (-22)`, data=0 |

Legacy `set_clk_param` (full timing, also matches the mainline DT tables):
`scl_ctl=0x00260038 sda0=0x00280028 sda1=0x00160023 sda2=0x0000003e misc=0x00000063
thigh=38 tlow=56 stretch=0 trdhld=6 tsp=3`. Master 1 / slot 2 is the same with
`qstart=0x8` and the M1 RD_DONE bit (`status0=0x00001000`, `m1_rd_buf=0x1`).

## Conclusion — the CCI controller programming is byte-identical

Every register the CCI controller is programmed with for the chip-ID read —
SET_PARAM (sid/retries/id_map), SCL_CTL, MISC_CTL, the 5-word queue, EXEC count,
QUEUE_START target — is **identical** between the ACK and NACK kernels. The queue
executes the same 5 words on both. So **all four prior "still plausible" CCI-side
hypotheses are RULED OUT**:

- ❌ MISC_CTL / THZ / glitch-filter / half-cycle difference — identical (`0x63`).
- ❌ SET_PARAM id_map / retries / missing second SET_PARAM — identical (`0x00030361`).
- ❌ report_q vs rd_done completion path — legacy's own IRQ shows the controller
  *raises RD_DONE* (`status0=0x1`) on success; mainline's controller *raises NACK*
  (`status0=0x10000000`) for the identical queue. The completion code isn't the
  problem; the **bus-level ACK/NACK from the slave is**.
- ❌ freq mode actually programmed — both FAST, both `scl_ctl=0x00260038`.

The divergence is **not** in any software-visible CCI register. The same controller,
programmed identically, gets an ACK on legacy and a NACK on mainline. That means the
difference is the **physical/sequencing state of the sensor (or bus) at the instant
QUEUE_START fires** — i.e. upstream of the CCI read, in the **sensor power-up / reset
/ MCLK-settle sequence**, not in the CCI transaction itself.

## What this newly points at (next leads)

The sensor's I2C block must be powered, clocked (MCLK), and past its power-on-reset
*and internal boot* before it will ACK its address. Prior work proved the *steady-
state* rails/MCLK/reset are correct at the pad — but legacy and mainline reach that
state by **different sequences and timing**, and the OS04C10 latches I2C readiness on
the *transient*. Concrete differences to chase, in order:

1. **Reset-deassert → first-I2C settle.** Legacy applies the openpilot power_setting
   sequence (VIO→VANA→VDIG→MCLK→RESET with the openpilot-specified per-step delays,
   incl. the 34 ms post-reset wait). Confirm mainline honors the **same delays in the
   same order**, and that reset actually deasserts *after* MCLK is already toggling.
   Add per-step timestamps on both and diff the inter-step deltas (esp. around
   reset-high → first CCI read).
2. **MCLK-before-reset.** Legacy's sequence guarantees MCLK is running before
   reset-deassert. On mainline, verify MCLK is up *and stable* (not just enabled in
   CAMCC) for the full settle window before reset-high — a late or briefly-gated MCLK
   would leave the sensor's I2C state machine un-clocked out of reset.
3. **Per-read CCI reset.** Legacy IRQ shows `status0=0x01000000` (RST_DONE_ACK)
   immediately *before* every read — legacy resets the CCI master before each
   transaction. Check whether mainline does the same pre-read reset (a dirty master
   from a prior failed txn could change bus state).
4. **MCLK frequency/source detail.** Legacy MCLK = 24 MHz from a specific CAMCC
   parent; reconfirm mainline's MCLK is 24.000 MHz (not 19.2/off-by-divider) at the
   pad during the exact read window, with the same source clock.

## Status of the genpd fix

Unchanged and kept (commit 539ac10): removing `power-domains` from the sensor nodes
was correct (matches legacy) and is now proven *not* to affect the NACK. The root
cause is the **power-up/reset/MCLK timing sequence**, which is where attention moves
next. The instrumented legacy kernel + `spectra_camera_test_legacy` on the device are
the working A/B reference for any further timing diff.
