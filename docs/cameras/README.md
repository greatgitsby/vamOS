# Cameras — mainline bringup tracking

**Goal:** get an image out of the comma four (mici) cameras on the vamOS mainline
kernel (Linux 6.18), preserving the kernel interface openpilot's
`system/camerad` needs — the downstream Qualcomm Spectra UAPI with direct
register programming (camerad builds CDM command buffers that program IFE
registers itself; the kernel is a thin resource manager, not a V4L2 pipeline).

**Smallest-scope first:** one camera (road, IFE-processed), one still frame
written to a PNG on-device. Everything else (3-cam concurrency, BPS/driver cam,
camerad daemon, tizi) is deferred until that lands.

**Status (2026-07-01):** the driver port is done and running — the modern
[qualcomm-linux/camera-driver](https://github.com/qualcomm-linux/camera-driver)
`camera_kt` stack builds into 6.18, all `cam-*` nodes probe, SMMU/mem-mgr work,
ICP firmware downloads. **Blocked** on the OS04C10 sensor NACKing its I2C
address at the chip-id probe (0/3 cameras; same board reads 3/3 on legacy 4.9).
Software is exhausted: a userspace `/dev/mem` replay of the identical CCI
transaction ACKs on legacy and NACKs on mainline, so the cause is below the
register interface. See "The blocker" below for surviving leads. The device is
currently flashed with legacy 4.9.103 (the known-good A/B reference).

---

## The contract: what camerad needs

openpilot camerad (`openpilot/system/camerad/cameras/spectra.cc`) hardcodes the
downstream Spectra CAMSS ABI. The kernel must provide:

- `/dev/v4l/by-path/platform-soc:qcom_cam-req-mgr-video-index0` — cam_req_mgr +
  memory manager. camerad drives it with `CAM_REQ_MGR_ALLOC_BUF/MAP_BUF/
  RELEASE_BUF`, `CREATE_SESSION`, `LINK`/`LINK_CONTROL`, `SCHED_REQ`,
  `FLUSH_REQ`, and V4L2 event subscription (SOF events). The udev **by-path
  name matters** (userspace is immutable).
- `/dev/v4l/by-path/platform-cam_sync-video-index0` — cam_sync fences.
- v4l-subdevs found **by name**: `cam-isp`, `cam-icp` (both opened and
  asserted at `SpectraMaster::init`, even if BPS is unused), plus per-camera
  `cam-sensor-driver` and `cam-csiphy-driver`.
- Direct register programming: IFE config arrives as CDM packets via
  `CAM_CONFIG_DEV`; the sensor power-up sequence and init register tables come
  from **userspace probe/config packets** (`spectra.cc`), not DT power-seq.

Camera map (`openpilot/system/camerad/cameras/hw.h`), sensors OS04C10
(chip-id `0x5304` @ reg `0x300a`, slave `0x6c`/`0x20`) with OX03C10 fallback:

| cam | num | phy | output | env kill-switch |
|---|---|---|---|---|
| wide | 0 | PHY0 | `ISP_IFE_PROCESSED` | `DISABLE_WIDE_ROAD` |
| road | 1 | PHY1 | `ISP_IFE_PROCESSED` | `DISABLE_ROAD` |
| driver | 2 | PHY2 | `ISP_BPS_PROCESSED` (needs ICP FW) | `DISABLE_DRIVER` |

The IFE path needs no ICP firmware at runtime (`cam-icp` must still probe and
answer `CAM_QUERY_CAP`) — that's why the smallest-scope target is an IFE cam.

openpilot is *nearly* unmodified: branch `spectra-uapi-migration-plan` in the
openpilot repo (commit `e8fe170ad`) migrates the handful of call sites that
changed in the modern `camera_kt` v1.0.3 UAPI (CSIPHY lane fields, `custom_csid`
removal, `__u64` frame ids). That is the only openpilot delta allowed.

## Architecture / where things live

- **Driver:** `kernel/spectra-qcom/camera-driver` — git submodule of
  `qualcomm-linux/camera-driver`, branch `camera-kernel.qclinux.0.0`. The
  `camera_kt/` tree is the techpack Spectra driver; it has Titan 170 (SDM845)
  support (`cam_ife_csid17x.c`, `vfe17x/`). *(The submodule is registered on
  the camera branch; on `master` the checkout shows as untracked.)*
- **Build integration:** `tools/build/build_kernel.sh:install_spectra_qcom()`
  copies the submodule into `kernel/linux/drivers/media/platform/msm/camera/`
  + UAPI headers into `include/uapi/media/` at build time. Kernel-side hook is
  patch `0012-media-platform-add-msm-camera-hook.patch`.
- **Driver modifications** live as patches in `kernel/spectra-qcom/patches/`
  (36 as of branch tip — several are temporary diagnostics to strip later).
  Kernel patches (camcc GDSC/hw_clk_ctrl, rpmh regulator, breadcrumbs) are
  `kernel/patches/0012–0018` on the branch.
- **DTS:** techpack `qcom,cam-*` nodes flat under `&soc` in
  `kernel/dts/sdm845-comma-common.dtsi` (+ mici.dts). Do **not** mix with the
  mainline `qcom,sdm845-camss` binding. Audit: `docs/cameras/dts-audit.md`.
- **Branches:** vamOS `spectra-uapi-migration-plan` (tip `77f1bf4`, 2026-06-17)
  is the live camera branch; `cameras-spectra-mainline` is the abandoned first
  attempt (porting the 4.9 driver sources). openpilot
  `spectra-uapi-migration-plan` carries the UAPI migration +
  `camera-bringup-debug-logs` carries `vamos-dbg` tracing and the
  `snapshot_standalone` tool. Unexplored related branches on origin:
  `spectra-isp`, `cameras-spectra-mainline-andi`, `liberation-day-camerad`.
- **Test tools (in this repo):** `tools/cci_replay.py` (userspace devmem CCI
  transaction replay — driver-free trigger), `tools/island_dump.py`,
  `tools/camera/snapshot_standalone.cc` (self-contained camerad replacement:
  bring-up → N frames → PNG → exit; verified 3/3 on legacy).

## Hard constraints

- **Userspace cannot be modified.** All camera drivers are built into the
  kernel image (`=y`, no modules). Firmware must come from the existing rootfs
  — ICP FW loading from it is already proven working.
- **No network on the mainline kernel** (only `lo`). All on-device work goes
  over the MDMA serial link (see dev loop). The legacy kernel has networking
  (`ssh comma@10.0.0.22`) — use it for openpilot builds / reference runs.
- **`kernel/linux` is wiped every build** (`git reset --hard` to the pinned
  rev). Kernel changes must be committed patches in `kernel/patches/`; driver
  changes must be patches in `kernel/spectra-qcom/patches/`.

## Milestones

- [x] **P0 — port compiles & boots:** camera_kt built into 6.18, kernel boots.
- [x] **P1 — nodes probe:** all `cam-*` subdevs + `cam-req-mgr`/`cam_sync`
      video nodes present with legacy-matching names.
- [x] **P2 — memory + ICP path:** SMMU contexts, dma-buf mem-mgr, ICP FW
      download + power-collapse/teardown all work (multiple crash fixes
      landed; see validation docs).
- [~] **M0 — refresh & repro:** rebase `spectra-uapi-migration-plan` onto
      current `master` (pick up i2c/uart work; check whether the UFS hibern8
      fix from `kernel-ufs-hibern8-fix` is merged — the storm garbles boots),
      build, flash, re-confirm the NACK repro on today's tree. **Partial
      (2026-07-01):** rebase + UFS-fix pull-in + build + legacy 3/3 baseline
      done; **flash/mainline-repro BLOCKED** — the SOC would not enter QDL
      (Sahara `9008` never enumerated). See Log.
- [ ] **M1 — sensor ACKs (THE blocker):** OS04C10 chip-id read returns
      `0x5304`. See next section.
- [ ] **M2 — first image (the narrow target):** road cam only
      (`DISABLE_WIDE_ROAD=1 DISABLE_DRIVER=1`), `snapshot_standalone` writes a
      real PNG. Exercises sensor→CSIPHY→CSID→IFE→SMMU→req_mgr/sync end-to-end.
- [ ] **M3 — all three cameras:** add wide (IFE) + driver (BPS via ICP FW).
- [ ] **M4 — camerad daemon:** unmodified-ABI camerad under openpilot, 20 fps
      sustained, exposure control, survives reboot cycles.
- [ ] **M5 — cleanup:** strip diagnostic patches (driver patches marked
      "temporary"/"breadcrumbs", kernel patch 0016), keep the real fixes
      (genpd on sensors, 0017 GDSC timing, 0018 MCLK hw_clk_ctrl), tizi
      (OX03C10) bringup.

## The blocker: OS04C10 I2C address NACK (M1)

Full handoff: `docs/cameras/validation/HANDOFF-sensor-nack.md` (+ dated
evidence docs in `docs/cameras/validation/`). One-paragraph summary:

The sensor NACKs its slave address on the very first CCI read. Verified
**byte-identical to legacy, live on the same board**: rails (vana/vio/vdig,
voltage+load), genpd/TITAN_TOP_GDSC (incl. wait-timing patch 0017), MCLK
(24 MHz, RCG/M/N/D/PLL2, continuous, pad muxed + toggling), reset sequence +
34 ms settle, CCI clocks/timing regs/IRQ masks/init/reset/state, the literal
5 CCI queue words, pad drive/pull, CPAS votes. The decisive bisection: a
**userspace `/dev/mem` replay** of the exact transaction (driver IRQ masked)
ACKs on legacy 4.9 and NACKs on mainline 6.18 — same register pokes, opposite
result. The bug is **below the register interface**. MCLK root-parking
(`hw_clk_ctrl`, patch 0018) was a real 4.9→6.18 divergence but did not fix it.
MCLK is *mandatory* for the ACK (proven by skipping it on legacy → 0/3).

Surviving leads, in rough order of cost:

1. **PMIC-level SPMI register diff (not yet done, software-visible):** dump
   the actual PMIC registers for the sensor rails — LDO/BOB mode (LPM vs HPM),
   programmed voltage, enable state, and the PM8998 GPIO driving the vdig
   enable — on legacy vs mainline during the probe window. The regulator
   framework *claims* match; the silicon hasn't been checked. An LPM-mode rail
   that can't source the sensor's inrush would brown out POR and produce
   exactly this signature.
2. **Full camera-island register-space diff:** every word of CAMCC + CPAS +
   CAMNOC on both kernels (island_dump.py exists) — catches state set by some
   *other* mainline driver sharing the island.
3. **Scope / logic analyzer** on MCLK, SDA/SCL (gpio17/18), VANA, reset —
   `tools/cci_replay.py` gives a driver-free trigger on QUEUE_START. This is
   the definitive answer to "what does the sensor actually see".
4. **Stock `i2c-qcom-cci` clean-room probe** (setup recipe in the handoff doc)
   — superseded as a driver-vs-platform bisector by the devmem replay, but
   still useful independent evidence.
5. **A/B a second board** (mici-vanilla, `comma@10.0.0.25`) to rule out
   anything unit-specific.

## Dev loop (build → QDL → flash → reboot)

The MDMA debug adapter is wired; the aux USB-C loop is in place. Note the QDL
USB device (`3801:9008`, "QUSB_BULK") is **always enumerated** while the MDMA
is attached — it is *not* a boot-state or QDL-readiness signal. Judge device
state by the serial console / uptime instead.

```bash
MDMA=~/.claude/skills/mici/scripts/mdma.py   # from the mici skill

./vamos build kernel        # ~7–12 min (Docker); produces build/boot.img
$MDMA reboot-qdl            # force QDL
./vamos flash kernel        # QDL/EDL write of boot_a (run from repo root)
$MDMA boot                  # power-cycle + block until a live shell
$MDMA bash 'uname -r'       # verify the flashed kernel (6.18.0-vamos-…)
```

- Back to the known-good reference: `./vamos flash kernel --legacy`
  (AGNOS 4.9.103, has networking → `ssh comma@10.0.0.22`).
- `reboot-qdl` occasionally no-ops the VIN cut: confirm uptime actually reset
  (`$MDMA bash 'cut -d" " -f1 /proc/uptime'`); re-issue if it kept climbing.
- Iterations are ~3–5 min each (rebuild + QDL + boot). There is no faster
  path: the driver is built-in (no `insmod`), and 16.8 MB over 115200-baud
  serial is slower than QDL.

### On-device debugging playbook (serial survival)

- Keep console printk low during camera runs: `echo 3 3 1 3 >
  /proc/sys/kernel/printk` (the ICP HFI queue-dump flood saturates the UART).
- Enable driver debug without flooding: `echo 0x3FFFFFF >
  /sys/module/cam_debug_util/parameters/debug_mdl` (lands in dmesg, not
  console).
- Log to `/data` + `sync` inside the same script; read back **after** a
  reboot — camera runs tend to wedge the serial console.
- **Reboot between dirty runs:** a killed camera process leaves
  `cam_req_mgr` locks held; the next `/dev/video0` open hangs in D-state.
- Long/multi-line device scripts: `$MDMA bash - <<'EOF' … EOF` (keep output
  short; bump `--timeout` for slow runs).

## Document index

- `docs/cameras/RECENT_SPECTRA_OPENPILOT_DESIGN.md` — design for the
  camera_kt + openpilot UAPI migration (this is the current architecture).
- `docs/cameras/RECENT_SPECTRA_OPENPILOT_TASKS.md` — task ledger + progress
  log for the migration.
- `docs/cameras/dts-audit.md` — techpack DT wiring audit.
- `docs/cameras/validation/` — dated evidence docs for every ruled-out NACK
  hypothesis, raw register dumps, and `HANDOFF-sensor-nack.md`.

*(Those files live on the `spectra-uapi-migration-plan` branch; this README is
the standing tracker.)*

## Log

### 2026-07-01 — M0 refresh (partial): rebased + built + legacy baseline; flash blocked on QDL entry

**Rebase (done).** Preserved old tip as
`spectra-uapi-migration-plan-pre-rebase-20260701` (`77f1bf4`). Rebased
`spectra-uapi-migration-plan` onto `master` (`7d3ae6d`). Master was only 2
commits ahead of the merge base and both touched only
`tools/build/build_kernel.sh` (worktree-build fixes #118, #122). The rebase
applied **cleanly with no conflicts** — git auto-merged the branch's
`install_spectra_qcom()` additions with master's worktree fixes (adjacent but
non-overlapping regions; both sets verified present: `GIT_MOUNT_ARGS` /
`kernel-linux.bundle` / host-side `GIT_REV` from master, and the SPECTRA_QCOM_*
install machinery from the branch). No changes were needed in
`kernel/configs/vamos.config` or `kernel/dts/*`. The named "uart3/ublox, i2c
busses" work was already in the merge base, not new on master. New tip after
rebase: `c486f46`; with the UFS patch added below: **`6508113`**.

**Submodule.** `kernel/spectra-qcom/camera-driver` pin is
`56b463cba50c1db1f2cc53ddd8790730f14bd8a8` (v1.0.3) — **matches** the expected
`56b463c`, not bumped. Working-tree clone already at that rev, clean.

**UFS hibern8 fix (included).** Master's stack ends at 0011; the camera branch
owns 0012 (msm camera hook). Brought the `phy_calibrate()`-on-clock-gating-
resume + `BROKEN_AUTO_HIBERN8` fix in from `kernel-ufs-hibern8-fix`
**renumbered 0012 → 0019** to avoid the collision. It touches only
`drivers/ufs/host/ufs-qcom.c`, independent of the camera patches; applied
cleanly and built in.

**Build (done).** `./vamos build kernel` succeeded (~7 min). All 19 kernel
patches (incl. 0019 UFS) + all 36 spectra driver patches applied.
`build/boot.img` = **17,917,952 bytes (17.1 MiB)**. Expected flashed kernel
string: `6.18.0-vamos-6508113`.

**Legacy baseline (done, 3/3 ACK).** Device was on legacy 4.9.103. The
`spectra_camera_test` probe binaries are **absent** from the device (on-device
openpilot is `release-tizi` v0.11.0, which ships only `camerad`, no
`spectra_camera_test*`; they are also not tracked in this vamOS repo — the
README's `tools/cci_replay.py`/`snapshot_standalone.cc` references do not
resolve to files here). Triggered the sensor probe instead by briefly running
the release `camerad`, which drives the in-kernel `cam_sensor_driver_cmd`
probe. dmesg confirmed the exact **3/3 ACK**:
`Probe success,slot:0,slave_addr:0x6c,sensor_id:0x5304` /
`slot:1,slave_addr:0x20,sensor_id:0x5304` /
`slot:2,slave_addr:0x6c,sensor_id:0x5304` — zero CCI NACKs.

**Flash + mainline repro (BLOCKED — could not enter QDL).** `qdl.js` (used by
`./vamos flash kernel`) requires the SOC in Sahara EDL (`05c6:9008`/
`3801:9008`). Despite the aux cable being correctly routed (SOC's aux USB
enumerates on the 7002 hub port 1, `connected=True`), the SOC would **not drop
into EDL**: it always boots slot_a's OS, presenting its adb+cdc_ncm gadget
(`04d8:1234` on 7002 port 1) instead of `9008`. VIN cuts confirmed working
(serial dies, gadget re-enumerates, uptime resets). Tried, all failed to latch
`9008`: `reboot-qdl` (aux-power-first) ×5 + a tight retry loop; a custom
force-QDL with a 6 s discharge and a 300 ms aux-before-VIN window;
`emmc_dload=1` + clean reboot; `reboot edl`; and an armed `sysrq c` panic. This
is a genuine QDL-entry failure (distinct from the documented `reboot-qdl`
VIN-no-op — the VIN cut did work), most likely a board/PBL-level issue needing
physical inspection (aux-port placement vs UFP, or a QDL strap/test-point).
**Consequently the mainline NACK repro was not run.** Device left in the
known-good legacy 4.9.103 state (dload cookie disarmed, /data scratch cleaned).

Next: resolve QDL entry (physical check of the aux cable / QDL strap), then
flash `6508113`'s `boot.img` and run the mainline OS04C10 probe.
