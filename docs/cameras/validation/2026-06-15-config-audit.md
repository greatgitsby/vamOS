# Config / "missing CONFIG" audit + minimal-driver attempt

**Date:** 2026-06-15. Premise step-back: after every runtime register matched, look for
a "super dumb" cause — a missing/wrong Kconfig that changes driver behavior without
changing registers, or a second driver fighting for the hardware.

## Driver binding — clean, no conflict

Only `cam-cci-driver` (camera_kt) is bound to `ac4a000.cci`. The stock upstream
`i2c-qcom-cci` driver is built `=m` but NOT loaded and NOT registered — no two-driver
conflict over the CCI hardware. camera_kt creates /dev/v4l-subdev0..8 as expected.

## Target-define audit (camera_kt is built AS qcm6490, device is sdm845)

`config/qcm6490-camera.mk` compiles the driver with `-DCONFIG_QCM6490=1` plus
`-DCONFIG_SPECTRA_{KT,ISP,ICP,JPEG,LRME,SENSOR}=1`. Findings:
- `CONFIG_QCM6490` is referenced in exactly ONE place: `camera_main.c:333`, gating the
  DT match `{.compatible = "qcom,camera_kt"}`. It's REQUIRED for the root driver to
  bind — and it is set, so the camera binds and probes. Not a bug.
- `CONFIG_ENABLE_US_API := y` is a make var but NOT in the `-D` ccflags list, so it
  never reaches the preprocessor — but it has **0 references** in the driver source, so
  harmless.
- No `CONFIG_SPECTRA_SECURE` / `CONFIG_SECURE_CAMERA` defined → the CCI is NOT routed
  through a TrustZone/secure path. `#ifdef CONFIG_SECURE_CAMERA` blocks (in csiphy) are
  compiled out. Good — rules out a secure-camera gate.
- The CCI/sensor read path has no `#ifdef CONFIG_QCM6490` or target-specific code — the
  default 100kHz clk params (`hw_thigh=201`) are overridden by the DT
  `qcom,i2c-fast-mode` node (confirmed: runtime scl_ctl=0x00260038 = thigh 38, fast
  mode). So the qcm6490-vs-sdm845 build target does not change the CCI transaction.

## Kernel config — camera deps present

Running kernel (`/proc/config.gz`): CONFIG_SPECTRA_CAMERA=y, SDM_CAMCC_845=y,
VIDEO_DEV=y, MEDIA_*=y, INTERCONNECT=y + QCOM_BCM_VOTER=y (ICC voting works — display/
ufs/i2c vote live), IOMMU/ARM_SMMU present, REGULATOR_QCOM_RPMH=y. No camera-path
subsystem is a missing/unloaded module. The chip-id read NACKs at the bus address phase
(no buffers/SMMU/ICC bandwidth involved), so none of these gate it anyway.

## Minimal in-driver CCI read (bisection attempt) — INCONCLUSIVE

Injected a bare-metal stock-i2c-qcom-cci-style read into `cam_sensor_match_id` (patch
0037, since removed). Result: `status0=0x0, buf_lvl=0` — the hand-rolled queue did not
execute cleanly. Two confounds made it unreliable and it was reverted:
1. **Live IRQ race:** camera_kt's CCI IRQ handler is registered and fires; it clears
   `CCI_IRQ_STATUS_0` before the injected poll loop reads it → status reads 0.
2. SET_PARAM was missing the `retries<<16` field; bare writes raced camera_kt's own
   queue/semaphore state.
Side effect: the bare writes left the CCI master in a "resetting" state, so later
`MSM_CCI_INIT` ioctls returned `rc=-11` (EAGAIN, "CCI hardware is resetting"). **The
0037 build is contaminated — reflash a clean kernel to restore baseline.**

The clean version of this test is the real **stock i2c-qcom-cci driver** (its own IRQ +
init), which needs the sensor powered declaratively (rails always-on + reset gpio-hog +
an MCLK clk consumer) since it does no sensor power sequencing. That DT work is the
recommended next experiment — it definitively separates "camera_kt driver bug" from
"hw/DT/power".

## Net

The config/binding audit is clean — no missing CONFIG explains the NACK, no driver
conflict, no secure-camera gate, no target-define affecting the CCI transaction. The
"super dumb" lead did not pan out for the CCI path.
