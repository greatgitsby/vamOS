# Whole-file source diff: legacy 4.9 vs mainline camera_kt CCI path

**Date:** 2026-06-15. After every runtime register/clock/power value was proven
identical, three agents diffed the ENTIRE source of the CCI/sensor-IO path
line-by-line (cam_cci_core.c, cam_cci_dev.c, cam_cci_soc.c, cam_sensor_io.c,
cam_sensor_cci_i2c.c), accounting for patches 0006/0011/0013/0014/0015.

## Read construction (IO layer) — byte-identical

`camera_io_dev_read` → `cam_cci_i2c_read` fills `cci_ctrl`/`cci_i2c_read_cfg`
identically: cmd=MSM_CCI_I2C_READ, addr, addr_type=WORD(2), data_type=WORD(2),
`num_byte = data_type = 2`, same endianness reassembly. The
`enum camera_sensor_i2c_type` values (WORD=2) and `struct cam_cci_read_cfg` layout
are identical — **no UAPI/enum drift**. Rules out a mis-constructed read.

## Read happy-path (core.c) — patched to match legacy

The 5 queue words, EXEC_WORD_CNT, QUEUE_START, and FIFO drain are identical. Patch
0011 restores legacy's completion model (wait on `reset_complete`; IRQ signals it on
RD_DONE). The read the controller emits is faithful to legacy.

## Real behavioral divergences found (all symptom-handling, not ACK-causing)

The agents independently concluded the divergences cannot fabricate a bus-level
address NACK — they change how a NACK is *handled* and the *next* transaction's
state, not whether the slave ACKs:

1. **IRQ clear position (§1.1):** mainline clears `CCI_IRQ_CLEAR_0/1` at the TOP of
   `cam_cci_irq` (before acting on status); legacy clears at the BOTTOM (after
   signaling completions + error/halt writes). Different controller-visible W/R
   ordering. Present on every IRQ including the first.
2. **Stateful RD_THRESHOLD masking (§1.2):** mainline-only — on RD_THRESHOLD without
   RD_DONE it disables that bit in CCI_IRQ_MASK_1 and re-enables on the later RD_DONE.
   If RD_DONE never arrives (a NACK), the threshold stays masked across transactions.
   But our NACK is an M0_ERROR IRQ (status0=0x10000000), not a RD_THRESHOLD, so this
   block does not fire on the failing read.
3. **Error/NACK path (§1.6 + §1.3):** post-0014 mainline still sets `reset_pending=true`
   in the error branch and `complete_all(report_q)` on NACK (legacy does neither — it
   HALTs and lets the HALT_ACK handshake own reset_pending); and 0014 makes
   `complete(reset_complete)` unconditional on RST_DONE. Affects retry/teardown state.
4. **`is_initilized` reset-skip on reopen (§2.2/2.3):** mainline skips the master reset
   on reopen if `is_initilized` is still true; legacy always resets on reopen. Affects
   2nd+ attempts, not the cold first read.
5. **Freq semaphore (patch 0015, §7):** mainline serializes ops behind a count-1
   `master_sem`; can stall/deadlock but cannot fabricate a NACK.

## Why none of these is the cause

**The cold first read of slot 0 NACKs** (verified: clean boot, first camera touch).
Divergences #2/#3/#4 only matter on the 2nd+ transaction or on error *recovery* — they
cannot explain the very first read NACKing. And the IO-layer read is byte-identical, so
the controller emits the same transaction. The "legacy silently NACKs too" hypothesis is
refuted: legacy returns real data (`read_words=1`, sensor_id=0x5304, Probe success).

## Conclusion

The entire CCI/sensor-IO **source path** is now diffed. The read the hardware executes
is faithful to legacy; the only divergences are in error-handling/serialization/IRQ-clear
ordering, none of which can cause a first-read address NACK. Combined with the exhaustive
runtime register/clock/power match, the NACK is not explained by anything in this driver
subtree.

This strongly implies the cause is **outside the CCI/sensor driver path** — in how the
broader recent kernel sets up the camera subsystem (CPAS/CAMCC/SMMU/interconnect global
state, or a register touched by a *different* mainline driver that shares the camera
power/clock island), or a genuine hardware-sequencing nuance that requires a
logic-analyzer trace of SDA/SCL/MCLK to compare the analog edges (which transition-count
matching does not prove).

Diagnostic patches in tree: 0035 (power_setting dump), 0036 (queue-word dump). Kept
fixes: bit-bang removal (was 0034), GDSC wait-timing match (0017), genpd re-add.
