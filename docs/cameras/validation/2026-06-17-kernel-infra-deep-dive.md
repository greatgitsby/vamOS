# Deep-dive: base-kernel SoC-infra differences vs 4.9 (2026-06-17)

Continuation of the OS04C10 NACK hunt after the devmem replay proved the fault is
**below the CCI registers, in base-kernel SoC state** (camera_kt driver exonerated;
all camera-island MMIO is byte-identical because camera_kt ports CPAS/CAMNOC/cam-smmu).
This session traced torvalds/linux git history across the shared-SoC subsystems and
A/B-tested every concrete lead on-device.

## Two REAL divergences found and fixed — but NEITHER is the NACK cause

Both are genuine 4.9-vs-6.18 differences, both now corrected (kept as fixes), and
both **disproven on-device** as the cause (probe still NACKs 0/3 with each applied):

### 1. MCLK RCG hardware-clock-control (patch 0018)
Mainline commit `bdc3bbdd40ba` clears `CFG_HW_CLK_CTRL` (CFG_RCGR BIT20) on the camera
MCLK RCGs; `camcc-sdm845.c` never opts back in. Result: MCLK root parks off at idle on
mainline (`ROOT_OFF=1`) vs running on 4.9. **Fix:** `.hw_clk_ctrl = true` on
`cam_cc_mclk0..3` (`kernel/patches/0018-...`). Verified working: with it, root runs
continuously (`ROOT_OFF=0`, 225k/225k samples through the probe). **Probe still NACKs.**

### 2. CX voltage corner (DT: CX power-domain + required-opps)
The camera nodes' `clock-cntl-level` strings dead-end at `dev_pm_opp_set_rate()` with no
`required-opps`, so mainline casts **no CX corner vote** -> CX sits at MIN_SVS (perf 48,
the `enable_corner` floor). Legacy CCI is `clock-cntl-level="lowsvs"` and downstream maps
it to a CX LOW_SVS vote (perf 64). **Fix:** added `<&rpmhpd SDM845_CX>` as a 2nd
power-domain on `cam_cci` + `required-opps=<&rpmhpd_opp_low_svs>` on its OPP node
(`kernel/dts/sdm845-comma-common.dtsi`). Verified working: CX now reads **64 (LOW_SVS)
during the probe**, matching legacy exactly. **Probe still NACKs 0/3.**

## Ruled OUT this session (with the on-device evidence)
- **CAMNOC QoS / SBM-fault** (`0xac42xxx`/`0xac44040`): mainline values match 4.9
  (CDM_PRIO=0x22222222, IFE02_PRIO=0x66666543, DANGER=0xffffff00, SBM_FAULTINEN0=0x3f) —
  camera_kt ports `cam_cpastop`. Not it.
- **Titan-top GDSC power**: GDSCR `0xad0b134=0xf822f000`, PWR_ON=1 SW_COLLAPSE=0,
  434k/434k samples through the read — island genuinely powered. Not it.
- **apps_smmu unmatched-stream fault**: `sGFSR=0` at `0x15000000`, no "Blocked Stream
  ID". Not it. (And the CCI I2C path doesn't DMA anyway.)
- **Secure-state / `restore_sec_cfg`**: DEAD — legacy 4.9 does NOT call it for camera
  either (only UFS/MDSS/crypto/PCI use it). Not a divergence.
- **GCC camera clocks** (ahb/axi/xo): byte-identical, `CLK_IS_CRITICAL`, can't be gated.
- **CAMCC PLL source**: bi_tcxo (XO) in both; GCC_MMSS_MISC identical. Not it.
- **GCC camera reset**: no `GCC_CAMERA_BCR`; `GCC_MMSS_BCR` untouched by both.
- **cmd-db / rpmh active-set / AOSS**: no camera consumer; active votes synchronous.
  (`prevent_cx_collapse` AOSS knob times out on this kernel — separate broken-link issue.)

## Assessment
Every register, clock, power-domain, corner, QoS, and secure-state difference reachable
in software has now been A/B-tested and either matched or fixed-without-effect. The two
real divergences (MCLK root, CX corner) are now corrected so mainline matches 4.9 — yet
the NACK persists. This is strong convergent evidence that the residual cause is
**physical/analog at the sensor pins** (MCLK waveform integrity, SDA/SCL slew/levels,
reset slew), which software introspection cannot resolve. The replay bench provides a
clean driver-free `QUEUE_START` trigger for a scope/logic-analyzer — the remaining lever.

Kept fixes (correct regardless): patch 0018 (MCLK hw_clk_ctrl), CX-corner DT vote.
