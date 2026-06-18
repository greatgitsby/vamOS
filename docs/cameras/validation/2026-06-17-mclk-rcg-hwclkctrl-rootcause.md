# MCLK RCG hardware-clock-control bit cleared on mainline (real divergence, NOT the NACK cause)

**Date:** 2026-06-17. Branch: `spectra-uapi-migration-plan`.

## TL;DR — IMPORTANT CORRECTION

A real 4.9-vs-6.18 clock-framework divergence was found and fixed (patch 0018), but
**on-device testing DISPROVED it as the OS04C10 NACK root cause.** The fix is still
correct and worth keeping (it makes mainline MCLK behave like 4.9), but the sensor
**still NACKs 0/3 with the fix applied** — so MCLK-root-parking was not the cause.

What's true:
- On mainline SDM845, the camera MCLK RCGs have **`CFG_HW_CLK_CTRL` (CFG_RCGR BIT(20))
  cleared**, so the RCG *root* parks OFF at idle (legacy keeps it on). Verified on-device:
  legacy `ROOT_OFF=0`, mainline `ROOT_OFF=1` at idle.
- **Patch 0018** (`.hw_clk_ctrl = true` on `cam_cc_mclk0..3_clk_src`) fixes this: with it,
  `HW_CLK_CTRL=1` and `ROOT_OFF=0` **continuously through the probe hold** (225k/225k
  samples) — the root now runs exactly like legacy.
- **BUT the OS04C10 probe still returns 0/3 NACK with patch 0018 applied.** And an earlier
  correlation test had already shown that *at the actual read instant* the root was on
  (root_off=0) yet still NACKed. So the root-parking, while a genuine divergence, is **not
  causal** for the address NACK.

**Keep patch 0018** (correct MCLK behavior, matches 4.9), but the NACK hunt continues —
the cause is something else still below the register interface.

## How it was found

1. **Bisection (devmem replay):** a userspace `/dev/mem` replay of the identical CCI
   register stream ACKs on legacy 4.9 and NACKs on mainline 6.18 — proving the fault is
   *below* the camera driver (camera_kt exonerated).
2. **On-device clue:** mainline MCLK0 `CMD_RCGR` (camcc 0x4004) reads `ROOT_OFF=1`
   (root parked off) at idle; legacy reads `ROOT_OFF=0` (root running). MCLK is proven
   mandatory for the ACK (legacy MCLK-skip → 0/3).
3. **Kernel git-history dive (the decisive step):**
   - **`bdc3bbdd40ba`** (2018-03-08) *"clk: qcom: Clear hardware clock control bit of
     RCG"* adds `CFG_HW_CLK_CTRL_MASK = BIT(20)` to the write *mask* in
     `clk_rcg2_configure()` without setting it in the *value* — so every
     `clk_rcg2_set_rate()` **clears BIT(20)**. Commit msg: *"For upcoming targets like
     sdm845, POR value of the hardware clock control bit is set for most root clocks
     which needs to be cleared for software to be able to control."*
   - **`a0e0ec7424c9`** (2023-05-17) *"clk: qcom: rcg2: Make hw_clk_ctrl toggleable"*
     adds the `rcg->hw_clk_ctrl` opt-in flag and `if (rcg->hw_clk_ctrl) cfg |=
     CFG_HW_CLK_CTRL_MASK;`. Commit msg: *"This allows the clocks to be turned on
     automatically when a downstream branch tries to change rate or config."*
   - `camcc-sdm845.c` **never sets `.hw_clk_ctrl`**, and `cam_cc_mclk0_clk_src` uses
     plain `clk_rcg2_ops` (no `.enable`, so software never sets `CMD_ROOT_EN`). Net:
     BIT(20) stays cleared → root parks off when the framework thinks the clock is idle.

## Why earlier MCLK checks missed it

Every prior comparison looked at MCLK **cfg/M/N/D/PLL2/branch-enable** — all
byte-identical (cfg=0x2113 etc.). Nobody checked the **CFG_HW_CLK_CTRL bit** or the
`CMD_RCGR ROOT_OFF` *status*. The branch reads "enabled" and the divider reads correct,
but the root isn't actually feeding the pad continuously — exactly the failure mode of a
clock that's "configured but not running."

## The fix (this branch)

`drivers/clk/qcom/camcc-sdm845.c`: `.hw_clk_ctrl = true` on all four
`cam_cc_mclk{0,1,2,3}_clk_src`. This re-asserts CFG_RCGR BIT(20) on set_rate, restoring
the downstream-feedback that keeps the MCLK root running when its branch is enabled —
the 4.9-equivalent behavior.

## Status

Fix applied on `spectra-uapi-migration-plan`, kernel rebuilt and flashed; on-device
confirmation of the OS04C10 probe (expect `sensor_id 0x5304`, 3/3) is the final check.
NOTE: the bench device currently also exhibits an unrelated intermittent UFS hibern8
storm that slows/garbles boots — separate issue (see kernel-ufs-hibern8-fix branch).
