# HANDOFF: OS04C10 I2C address NACK on vamOS mainline camera_kt

**Goal:** get the comma four (mici) cameras working on the vamOS mainline 6.18 kernel
with the recent Qualcomm `camera_kt` Spectra driver. **Blocker:** the OS04C10 chip-ID
probe returns a clean **I2C address NACK** on all three cameras. The same board probes
**3/3 OK on the legacy AGNOS 4.9 kernel**, including cold-boot first-touch (no userspace
warm-up) — so the legacy success is established by the kernel alone.

Branch: `spectra-uapi-migration-plan` (vamOS). Latest kernel builds as
`6.18.0-vamos-<hash>`. Probe tool on device: `system/camerad/spectra_camera_test
--sensor os04c10 --probe-only` (mainline-UAPI build) and
`spectra_camera_test_legacy` (legacy-UAPI build, used on the legacy kernel).

## The exact failure

```
irq nack cci=0 master=0 queue=1 status0=0x10000000 cur=0x2 exec=0x5 read_level=0x0
M0_Q1 NACK ERROR: 0x10000000
read status error master=0 status=0xffffffea (-22) slave=0x6c
```
The sensor NACKs its slave **address** (the NACK lands on queue word 2, the address
phase). `exec=0x5` (queue ran all 5 words), `read_level=0x0` (0 bytes read back).

## Sensor identity — CONFIRMED OS04C10

Legacy reads real `sensor_id:0x5304` on slot0(0x6c)/slot1(0x20)/slot2(0x6c).
openpilot: probe reg 0x300a, expected 0x5304, addr_type/data_type = WORD, FAST mode
(400kHz, freq_mode=1). Not the wrong sensor.

## EVERYTHING verified identical to the working legacy kernel (live, same board)

Ruled out by direct runtime measurement and/or source diff:

- **Power rails:** cam_vana 3328mV@80mA (BOB), cam_vio 1800mV (lvs1), cam_vdig 1050mV
  (camera_rear_ldo) — all enabled, correct, during the probe.
- **genpd / TITAN_TOP_GDSC:** required for MCLK (removing it broke MCLK with -EBUSY);
  re-added (commit re genpd). GDSCR config now matches legacy 0x0022F001 (patch 0017
  set CLK_DIS_WAIT=0xF, EN_FEW=2). PWR_ON=1 during probe.
- **MCLK:** 24.000 MHz, enable_count=1, continuous through the read; RCG config
  byte-identical (cfg_rcgr=0x2113, M=1, N=0xFE, D=0xFD, PLL2_OUT_EVEN). Pad toggling.
- **Reset:** full 6-step power-up VIO→VANA→VDIG→RESET-low→MCLK→RESET-high+34ms settle,
  byte-identical ordering+timing to legacy; reset GPIO driven HIGH before the read.
- **CCI clocks:** cci_clk_src = 37.5 MHz from PLL0_OUT_EVEN during the active read
  (parks to 19.2/TCXO only when idle, same as legacy). cci_clk enabled.
- **CCI timing registers:** scl_ctl=0x00260038, sda0=0x00280028, sda1=0x00160023,
  sda2=0x3e, misc=0x63 — identical. THESE MATCH THE UPSTREAM STOCK i2c-qcom-cci
  cci_v2_data (sdm845) FAST-mode params EXACTLY (thigh=38 tlow=56 ... tsp=3) — three
  independent implementations agree.
- **CCI init/reset/state:** per-probe MSM_CCI_INIT + master reset (RST_DONE_ACK seen),
  cci_state=ENABLED at read. IRQ masks (0x7fff7ff7/0x110000), RD thresholds (0x30),
  hw_version (0x10070000).
- **The 5 CCI queue command words:** SET_PARAM 0x00030361, LOCK 0x06, WRITE_DISABLE_P
  0x000a302b, READ 0x2a, UNLOCK 0x07 — BYTE-IDENTICAL legacy vs mainline.
- **Op-stream:** both do cci_client config → MSM_CCI_INIT → single read, no sensor-side
  writes. Removed patch 0034 (a per-probe GPIO bit-bang that stole the CCI pads +
  toggled reset before the real read) — clean path still NACKs.
- **CPAS/votes:** AHB SVS, AXI default bw, LOWSVS clock vote — match (patch 0006).
- **Pad drive/pull:** cci_i2c mux, pull-up, 2mA — match at the TLMM register.
- **Config/binding:** only cam-cci-driver bound (no stock-driver conflict); no
  CONFIG_SECURE_CAMERA (not TZ-routed); CONFIG_QCM6490 only gates the DT match string;
  no missing camera-dep CONFIG. hw_version != CCI_VERSION_1_2_9 so same non-v1.2 path.
- **Whole-file source diff** (cam_cci_core/dev/soc, sensor_io, cci_i2c): read
  construction byte-identical (enum WORD=2, num_byte=data_type); happy-path completion
  patched to legacy model (0011). Real divergences (IRQ-clear-at-top vs bottom; stateful
  RD_THRESHOLD masking; error-path reset_pending/report_q; is_initilized reset-skip on
  reopen; freq semaphore 0015) are all symptom-handling / retry-state / serialization —
  none can fabricate a FIRST-read bus-level address NACK (cold first read NACKs).

See the dated docs in this directory for the full evidence of each.

## What is NOT yet done — the decisive next test

**Run the upstream stock `i2c-qcom-cci` driver against the sensor.** It's a clean-room
upstream implementation of this exact CCI hardware (`drivers/i2c/busses/i2c-qcom-cci.c`,
maps `qcom,sdm845-cci` → cci_v2_data, built `=m` in vamos but not loaded). If it can
read the sensor at 0x36, the bug is 100% in camera_kt; if it NACKs too, the problem is
DT/kernel/SoC. This cleanly bisects driver vs hardware.

Setup required (the work to do):
1. `CONFIG_I2C_QCOM_CCI=y` in vamos.config.
2. A DT variant that uses the STOCK `qcom,sdm845-cci` binding on cci@ac4a000 (instead of
   camera_kt's `qcom,cci` override which `/delete-node/`s the i2c child buses), with the
   stock `i2c-bus@0`/`@1` children restored and the OS04C10 added as an i2c device
   `@36` under i2c-bus@0.
3. Power the sensor (the stock driver does NO sensor power sequencing): mark the rails
   regulator-always-on, drive the reset GPIO high via a gpio-hog, and provide MCLK
   (CAM_CC_MCLK0_CLK 24MHz). MCLK is the tricky bit — either mark it CLK_IS_CRITICAL in
   camcc-sdm845.c, or add a minimal clk-consumer. (Open question worth a quick test
   first: does the OS04C10 ACK its address WITHOUT MCLK? Many I2C slaves do — patch
   legacy to skip the MCLK step and see if it still probes 3/3. If yes, the stock test
   needs only rails+reset, no MCLK plumbing.)
4. Boot, `i2ctransfer`/`i2cget` the sensor at 0x36 reg 0x300a on the stock CCI bus.

An earlier in-driver bare-metal injection (raw CCI read inside cam_sensor_match_id) was
inconclusive because it raced camera_kt's live IRQ handler (status0 read back 0) and
corrupted master state. The stock driver avoids this by owning its own IRQ + init.

## Other live leads if the stock driver also NACKs (→ DT/SoC, not driver)

- A logic-analyzer/scope trace of SDA/SCL/MCLK comparing legacy vs mainline edges
  (transition-count matching does NOT prove SCL frequency/levels match — though
  cci_clk_src=37.5MHz was verified during the read).
- A CAMCC/CPAS/CAMNOC global-register-space full dump diff (read every word of those
  blocks on each kernel, diff) — to catch a register set by some OTHER mainline driver
  sharing the camera power/clock island.

## Kept fixes (correct, keep regardless)

- genpd power-domains re-added to sensor nodes (required for MCLK).
- Patch 0017: titan_top GDSC wait-timing matched to legacy.
- Patch 0034 removed (bus-corrupting pre-CCI bit-bang).
- Diagnostic patches in tree: 0035 (power_setting dump), 0036 (queue-word dump) — remove
  with the rest of the temp breadcrumb stack once the probe passes.

The instrumented legacy 4.9 kernel + `spectra_camera_test_legacy` on the device, and the
mainline build, remain the A/B reference bench.
