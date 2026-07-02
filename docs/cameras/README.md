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
ICP firmware downloads. **The historical blocker is SOLVED (2026-07-01):** the OS04C10 NACK was PM8998
L8 (1.2 V) never being enabled on mainline — see M1 below. All three sensors
now ACK (chip id `0x5304`) on mainline. Current work: M2/M3 — actual frames
(`snapshot_standalone` from openpilot branch `camera-mainline-m2`). Boot
reliability fixes landed too (squashfs `discard`, UFS clock-gating off);
a residual UFS storm from clock *scaling* remains on some boots.

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
- **Test tools:** `tools/camera/sensor_probe.py` (in this branch) — pure-python
  chip-id prober speaking the camera_kt v1.0.3 UAPI, byte-for-byte replica of
  camerad's `sensors_init()` probe packet; needs no openpilot build and runs
  over the MDMA serial link (`cat` it to `/data` and `sudo python3` it). The
  June devmem bench (`cci_replay.py`, `island_dump.py`, `cci_hold_capture.py`,
  MCLK/SCL probes) lives on branch
  `camera-probe-wip-before-master-reset-20260616-204055` (commit `cfff805`) —
  cherry-pick from there for M1. `snapshot_standalone.cc` lives in the
  openpilot repo on branch `camera-bringup-debug-logs`.

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
- [x] **M0 — refresh & repro (DONE 2026-07-01):** rebased onto `master`
      (no conflicts), UFS hibern8 fix included as patch 0019, kernel built +
      flashed (`6.18.0-vamos-44d1fee`), legacy baseline 3/3 ACK, and the NACK
      repro **confirmed byte-identical** on the rebased tree via the new
      `tools/camera/sensor_probe.py` (0/3, `status0=0x10000000`, `cur=0x2
      exec=0x5 read_level=0x0`, `slave=0x6c`). The QDL blocker was **bad
      physical cabling**, not software. See Log.
- [x] **M1 — sensor ACKs (SOLVED 2026-07-01):** 3/3 `PROBE OK`, chip id
      `0x5304` on the mainline kernel. Root cause of the entire NACK saga:
      **PM8998 L8 (1.2 V) was never enabled on mainline.** Legacy
      `comma_mici.dts` carries a bare `&pm8998_l8 { regulator-always-on; }`
      (board requirement, no DT-visible consumer — it feeds the camera sensor
      power chain). Found via a legacy-vs-mainline **PMIC SPMI register diff**
      (`tools/camera/pmic_scan.py` over regmap debugfs): L8 ON@1.2V under
      legacy camera streaming, unconfigured on mainline, while every
      camera-block register matched. Fix: `regulator-always-on` on
      `vreg_l8a_1p2` in `sdm845-comma-mici.dts` (mici-only, as in legacy).
      This retires the scope/LA plan and all remaining M1 leads.
- [x] **M2 — first image (DONE 2026-07-02):** road cam
      `snapshot_standalone` writes a real PNG (1344x760, exit 0) on mainline
      6.18 — full sensor→CSIPHY→CSID→IFE→SMMU→buf_done→req_mgr/sync path.
      Delivered to `tmp/frames/snap_road.png` (md5-verified). Dark bench
      frame (no exposure control yet — M4).
- [ ] **M3 — all three cameras (PARTIAL 2026-07-02):** road+wide dual-IFE
      simultaneous capture WORKS (`snap_wide.png` delivered, distinct md5).
      Driver cam (BPS) blocked: ICP FW "config io mapping" HFI response
      times out (-110) at BPS acquire — needs its own cycle.
- [x] **M4 — camerad daemon (2-cam, DONE 2026-07-02):** real `camerad`
      (DISABLE_DRIVER=1) ran 5+ min continuously on mainline: both IFE
      cameras at ~20 fps (IFE IRQ rates ~72/s and ~62/s steady), frame_id
      6315 at the 5:16 mark, zero CAM_ERR, no CRM recovery storms, and the
      AE loop working — mid-run VisionIPC frame (y_mean 41.9, delivered as
      `tmp/frames/midrun_road.png`) shows a clearly visible scene vs the
      near-black power-on default. Driver cam still excluded (M3 BPS wall).
      Reboot-cycle endurance not yet exercised.
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

### 2026-07-01 — M0 completed: flash unblocked (cabling), boot gaps found, NACK repro confirmed

**QDL root cause was physical.** The SOC never entered QDL because the device
wasn't hooked up right; after re-seating the cabling, `reboot-qdl` latches
`3801:9008` within ~1 s. All the software EDL-forcing attempts in the previous
entry (dload cookie, `reboot edl`, timing sweeps) were chasing a miswired
cable — don't repeat them. Two related operational facts: (a) a booted SOC
shows `04d8:1234` (AGNOS gadget) on the 7002 hub — `3801:9008` appears *only*
in QDL, so its presence IS a QDL signal; on the mainline kernel the aux port
shows nothing (no gadget configured). (b) The MDMA VIN toggle can wedge
(power-cycle no-ops, stale Sahara session ⇒ `qdl.js` "Unsupported mode:
error"); recovery is USBDEVFS_RESET on the `0424:4002/704c/7002` hubs, which
restores the toggle (fresh device numbers = fixed).

**Boot gaps on the AGNOS userspace (now release-tizi v0.11.0):**
- `/persist` (squashfs) fails to mount on every mainline boot: AGNOS's
  immutable fstab passes `discard`, which 6.18's fs_context **rejects**
  (`squashfs: Unknown parameter 'discard'`); legacy 4.9 silently ignored it.
  `local-fs.target` then fails and boot drops to the emergency shell. **Action
  item: tiny kernel patch to accept/ignore `discard` in squashfs.** (Manual
  workaround: `mount -t squashfs -o ro /dev/sda2 /persist`, then exit the
  emergency shell / `systemctl default`; boot continues to degraded
  multi-user.)
- The **UFS boot storm persists WITH patch 0019**: one boot in ~2 hits UIC
  `pwr ctrl cmd 0x18` completion timeouts + TSTBUS dumps right around the
  persist mount (~12 s), sometimes ending in `device doesn't support HS`
  fallback. 0019 targeted idle hibern8 recalibration; the boot-time storm is a
  different (or additional) window. Needs its own investigation — it also
  saturates the serial console and shreds MDMA handshakes mid-storm.

**Contract gaps found (matter for M2/M4):**
- udev by-path names don't match legacy: the driver-root DT node yields
  `platform-ac00000.camera-kt:cam-req-mgr-video-index0` /
  `platform-ac00000.camera-kt:cam-sync-video-index0`, but camerad hardcodes
  `platform-soc:qcom_cam-req-mgr-video-index0` and
  `platform-cam_sync-video-index0`. Runtime devtmpfs symlinks work as a
  stopgap; the real fix is DT/driver naming. **Action item for M4.**
- The release (legacy-UAPI) camerad on the v1.0.3 kernel gets remarkably far —
  video0/cam_sync open, ISP+ICP subdevs open, **ICP firmware downloads and
  boots** (`CICP.FW.1.0-00050`, BPS/IPE reset OK, TITAN_170_V2) — then
  segfaults in libc before the sensor probe (UAPI struct drift). For M4 the
  on-device openpilot must be the `spectra-uapi-migration-plan` build; until
  then `sensor_probe.py` is the probe vehicle.

**Repro (the M0 goal): confirmed 0/3, byte-identical NACK.** New tool
`tools/camera/sensor_probe.py` (pure python, v1.0.3 UAPI, no openpilot, ships
over serial) probes all 3 slots: `PROBE FAIL ret=-19 (ENODEV)` each, dmesg
shows the exact June signature — `irq nack cci=0 master=0 queue=1
status0=0x10000000 cur=0x2 exec=0x5 read_level=0x0`, `M0_Q1 NACK ERROR`,
`read status error status=0xffffffea slave=0x6c` — on kernel
`6.18.0-vamos-44d1fee` with all 34 driver diagnostics (power-setting dump,
CCI queue words, TLMM pad sampling) firing as in June. The 4.9-vs-6.18
below-register-interface conclusion stands on the refreshed tree; M1 leads
unchanged (PMIC SPMI diff first).

Device end state: mainline `6.18.0-vamos-44d1fee` flashed and booted (degraded
multi-user after manual persist mount this boot; next reboot will drop to
emergency again until the squashfs patch lands).

### 2026-07-01 — M1 SOLVED: the NACK was PM8998 L8, found by PMIC SPMI diff

**Method (lead #1 from the blocker list):** new tool
`tools/camera/pmic_scan.py` walks every SPMI peripheral on all four PMIC
USIDs via regmap debugfs (seek `registers` at `reg*9` bytes) and prints a
diffable TYPE/SUBTYPE/STATUS/ctl-window table. Captured on mainline during
the probe's powered hold and on legacy 4.9 during live camerad streaming
(3/3 baseline re-verified), then diffed on the host. Note: mainline can only
read ~208 APPS-owned peripherals (ownership enforced by the mainline
spmi-pmic-arb) vs 1228 on legacy — the intersection sufficed.

**The delta that mattered:** PM8998 **L8** (`0-01@0x4700`, 1.2 V) — enabled
with VSET=1.2 V on legacy, never configured on mainline. Legacy
`comma_mici.dts` carries a bare `&pm8998_l8 { regulator-always-on; };` — a
mici board power requirement with no DT-visible consumer (it feeds the
camera sensor power chain), which the mainline DTS port silently dropped.
One line in `sdm845-comma-mici.dts` (`&vreg_l8a_1p2 { regulator-always-on; }`)
→ **3/3 PROBE OK, chip id 0x5304, on mainline**. Every prior June
conclusion was consistent with this: all camera-block registers really were
byte-identical; the sensor really was electrically dead — just on a rail
nobody was watching.

**Still-unexplained deltas parked for later:** PM8998 GPIO9/GPIO11 are
outputs on legacy, inputs on mainline; GPIO12 (vdig-en) drive-strength
differs; several LDOs (L20/L23/L25) differ. None blocked the ACK.

**Boot reliability (same day):** patch 0020 (squashfs accepts `discard`) —
/persist now mounts, no more emergency-shell drops. Patch 0021 v1 (clock
gating off) didn't stop the UFS storm; v2 also removes clock scaling +
aggressive power collapse (gear-change `pwr ctrl cmd 0x18` storms) — in
build, unverified.

**M2 vehicle:** openpilot branch `camera-mainline-m2` (greatgitsby fork,
`8f2abb08f`): UAPI migration + vamos-dbg tracing + vendored v1.0.3 headers
(`third_party/qcom_spectra_uapi/`) + `NO_MODELD=1` build guard +
`snapshot_standalone`. On-device build (needs legacy kernel for network):
`GIT_LFS_SKIP_SMUDGE=1` fetch/checkout, then
`NO_MODELD=1 scons -j$(nproc) system/camerad/camerad system/camerad/snapshot_standalone`.

### 2026-07-01 (late) — PAUSED mid-M2: CDM-era wedge hunt, state for resumption

Where it stands: probe 3/3 and full SpectraMaster init (incl. ICP FW) are
clean on the CDM-enabled kernel; any run entering `camera_open` has coincided
with a **total SoC bus wedge** (NMI-unresponsive CPUs, console dead, unsynced
page cache lost). BUT several wedges happened with no camera run at all —
prime confound: AGNOS `gpio.service`/`sound.service` (unported subsystems)
retry-loop on every mainline boot and may be the real wedger. They are NOT
yet ruled in/out: runtime-masked on the last boot, idle-survival test was
paused before a verdict.

Resume plan:
1. Boot, `systemctl mask --runtime gpio.service sound.service`, let the
   device idle 5-10 min. If it wedges anyway → the services are innocent.
2. Run the phase gates (openpilot `snapshot_standalone` has
   `STOP_AFTER_{INIT,OPEN,START}` env gates) with services masked:
   gate INIT was clean; rerun gate OPEN (road-only) — kernel `1688110+`
   carries patch 0039 breadcrumbs (`vamos-cdm:` pr_err around
   cam_hw_cdm_init's resource-enable and the first CDM register write in
   reset_hw/pause_core). Stream stdout to /dev/ttyMSM0 or watch the console
   from the host (raw serial capture survives the wedge); the last
   breadcrumb pinpoints the wedging access.
3. If CDM's first register write wedges: compare clock/GDSC state vs legacy
   at that instant (CDM's 5 clocks all enable via soc_util; CPAS start
   ordering verified correct in cam_cdm_core_common.c:371 before init).
   Check whether our CPAS actually powers CAMNOC (the June port no-op'd icc
   votes).

Repo state: vamOS branch `camera` (965ea39) = full lineage + 0038 quiesce +
0039 breadcrumbs. openpilot branch `camera-mainline-m2` (fork) has the phase
gates + compat shims; the device /data/openpilot is on it, binaries built.
Device: mainline kernel `6.18.0-vamos-1688110` flashed (CDM + breadcrumbs).
sensor_probe.py + pmic_scan.py live in tools/camera/ and on /data.

### 2026-07-02 — wifi rebase (SSH!), wedge hunt narrowed to CPAS ICC votes

**Branch `camera` rebased onto `wifi`** (71 commits clean; patches renumbered
0016-0024, duplicate squashfs fix dropped). **Wifi works on mainline; SSH at
`comma@10.0.0.56`** — dev loop is now network-speed; serial only for
wedge recovery + QDL.

**Wedge hunt (SoC-wide NoC hang at IFE bring-up), eliminations with
evidence** (instrumentation = spectra patch 0040 v4 + kernel patches):
- gpio/sound services: masked, wedge persists (they're still suspects for
  *idle* deaths, unproven either way).
- CDM: exonerated — boot-time probe init completes every boot (incl. reset
  IRQ); acquire-time init completed fully in one run (GDSCR dump + config
  reached). Wedge point FLOATS between CSID init, CDM reset, and
  post-config — signature of an async/DMA victim, not the CPU's access.
- csid "csid2" naming: red herring — camera_kt keeps ONE static
  `csid_dev_name[8]` shared by all instances (upstream bug, cosmetic).
- GDSC wait timing (0025: bps/ipe/ife CLK_DIS_WAIT=0xF EN_FEW=2 like
  legacy): kept, didn't fix.
- mmnoc MMU TBU GDSCs unvoted (0026: ALWAYS_ON): kept, didn't fix.
- GDSCR dump at config time: titan+ife1 ON, PD get_syncs all rc=0.

**Prime suspect (structural, port-era deferred item): CPAS interconnect
votes.** The June port no-op'd ICC; the DTS `cam-cpas` node has NO
`interconnects` and the `camera-bus-nodes` tree is a bare level0. So
`bus_icc_based=false` and NOTHING votes the camera NoC→DDR path at RPMh —
while the live ICP (HFI queue DMA) and later IFE traffic need it. Explains
the floating wedge + boot-time-OK (bootloader/initial BCM state still hot).
camera_kt natively supports ICC: `bus_icc_based =
of_property_read_bool(cpas_node, "interconnects")`; AHB client from the
CPAS node's own `interconnects`; per-tree-node `qcom,axi-port-mnoc` /
`qcom,axi-port-camnoc` children with their own `interconnects` +
`interconnect-names` + `qcom,axi-port-name` (parser:
`cam_cpas_soc.c` ~130-360, 665-700; sdm845 icc phandles from
`dt-bindings/interconnect/qcom,sdm845.h`, 2-cell with QCOM_ICC_TAG).

### 2026-07-02 — CPAS ICC votes WIRED (commit 8e498ab): wall moved deep to CDM BL

**Correction to the prime-suspect framing above.** The `cam-cpas` node
*already* had an AHB `interconnects` (`cam_ahb`, added in an earlier rebase),
so `bus_icc_based` was already **true** — but the `camera-bus-nodes` tree was
a bare level0 with **no `qcom,axi-port-mnoc` child**, so `num_axi_ports == 0`
and `cam_cpas_util_vote_default_ahb_axi` never voted the camera→DDR (mnoc)
data path at `cpas_start`. That was the real gap.

**Driver reality (important for anyone extending this):** the compiled bus
backend is **`common/cam_soc_icc.c`** (Kbuild line 243, unconditional), NOT
`camera_kt/drivers/cam_utils/cam_soc_bus.c` (that's the legacy msm_bus
variant, gated on `CONFIG_QCOM_BUS_SCALING`, never built — and it
`#include`s a nonexistent `<linux/msm-bus.h>`). With `CONFIG_SPECTRA_KT=1`
the register path is `of_icc_get(&pdev->dev, name)` — **by-name lookup on the
cam-cpas node itself**. So every mnoc/camnoc port's `interconnect-names`
string must ALSO appear in the **cam-cpas node's own**
`interconnects`/`interconnect-names` (the child mnoc node's `interconnects`
is only read for informational src/dst ids). The `src_id`/`dst_id` parsed in
`cam_cpas_soc.c` are cosmetic under KT.

**The fix (commit `8e498ab`, DTS only — `sdm845-comma-common.dtsi`):**
- cam-cpas node `interconnect-names = "cam_ahb", "cam_hf_0", "cam_sf_0"`
  with matching `interconnects`: AHB (`&gladiator_noc MASTER_APPSS_PROC …
  &config_noc SLAVE_CAMERA_CFG`) + two camera→DDR paths (`&mmss_noc
  MASTER_CAMNOC_HF0/… SF … &mem_noc SLAVE_EBI1`), 2-cell QCOM_ICC_TAG_ALWAYS.
- `camera-bus-nodes/level0-nodes` now has two axi ports: `cam-hf-axi`
  (cell-index 0, `qcom,axi-port-mnoc` → `cam_hf_0`) and `cam-sf-axi`
  (cell-index 1 → `cam_sf_0`). No `client-name` on these (pure axi ports),
  so clients without a per-client tree take the `tree_node_valid==false`
  path in `cam_cpas_util_apply_client_axi_vote`, which still adds
  `CAM_CPAS_DEFAULT_AXI_BW` to every port at start. camnoc stays
  clock-controlled (no `control-camnoc-axi-clk`, `camnoc_axi_clk` in the
  clock list) so no camnoc icc ports.
- Reference used: CodeLinaro `camera-devicetree` branch
  `camera-kernel.qclinux.0.0`, `qcm6490-camera.dtsi` cam_cpas node — same
  parser, confirms the layout (its ports name `cam_hf_0`/`cam_sf_0`/
  `cam_sf_icp` on both the cpas node and the port children).

**interconnect_summary evidence (device, post-fix):** `ac40000.cam-cpas`
now appears as a requester on **18** interconnect path rows (was AHB-only
before); `qxm_camnoc_hf0` and `qxm_camnoc_sf` masters carry the
`camnoc-axi-min-ib-bw` floor (`2147483647`), and the `ebi` aggregate reads
`2149523183 / 2147483647` — the camera→DDR route reaches EBI. The paths
register and vote (`of_icc_set_bw`). Kernel is DTC-clean; `bus_icc_based`
confirmed true. Sensor probe still **3/3 PROBE OK** (M1 intact).

**Result: the ICC votes materially advanced the wall — this was NOT a null
elimination.** With votes in place the OPEN gate (road-only,
`STOP_AFTER_OPEN=1 DISABLE_WIDE_ROAD=1 DISABLE_DRIVER=1`) now reaches, in
order (host dmesg-stream `vamos-*` breadcrumbs, patch 0040 v4):
`start_hw init_hw done rc=0` → `vamos-cdm: init enter` →
`reset_hw: first reg write (pause_core)` → **`pause_core survived`** →
GDSCR dump (titan=0xf822f000 ife1 ON) → **`about to READ CDM reg` →
`CDM hw-version reads 0x10000000 (CDM alive)`** →
`start_hw: config_hw (CDM submit) begin` → **[21 s silence] → SoC-wide RCU
stall / NMI to hung CPUs**. Pre-ICP the wedge floated between CSID init,
CDM reset, and post-config; post-ICP the **CDM reset register write and the
CDM hw-version READ both survive every time**, and the wedge is now pinned
to **CDM BL submit/execute** (`config_hw`) — the first operation that DMA-
fetches the CDM command buffer from DDR over CAMNOC and replays register
writes to the IFE. Victim CPUs sit in innocent code (vmstat_update,
cpuidle) — an async/DMA bus victim, consistent with a CAMNOC-side hang, not
a CPU access fault.

**Next candidate (unstarted, one-cycle discipline): CAMNOC QoS / safe-LUT
for the CDM BL DMA.** `cam_cpastop_init_settings` programs per-port
priority/danger/**safe_lut**/qosgen from `camnoc_info->specific[]`. The chip
is TITAN_170_V2 → `CAM_CPAS_TITAN_170_V200` → `cam170_cpas200_camnoc_info`
(table exists, selection path present in `cam_cpastop_hw.c:909`), but it was
**not yet confirmed to actually apply at start** on our board (no explicit
version/QoS print at default debug; the `debug_mdl` mask I used did not
surface CAM_CPAS/CAM_PERF DBG lines, and the `/data` dmesg-sync loop lost the
window — the wedge freezes writeback, so on-device log capture of the
cpas_start instant is unreliable; use `pr_err` breadcrumbs compiled in, not
runtime debug_mdl, and the **host** dmesg-stream). The SCM path
(`cam_cpastop_scm_write`, `cam_compat.c:288`) is real (`qcom_scm_io_writel`,
guarded by `CONFIG_SPECTRA_SECURE`) and only fires for the conditional
`tcsr_camera_hf_sf_ares_glitch` errata — not the safe-LUT, so candidate (b)
looks less likely than (a). Recommended next cycle: add `pr_err`
breadcrumbs around `cam_cpastop_init_settings` (chip-version match + a dump
of the programmed safe_lut/qosgen for the HF/SF ports) as a new spectra
patch, confirm QoS lands and compare the LUT values against a legacy 4.9
CAMNOC register dump; if QoS is correct, move to LLCC/SCID (candidate c) or
scope the CDM AXI master.

**Dev-loop notes for the resumer:** host has no persistent pyusb; make a
throwaway venv for `mdma.py` (`python3 -m venv /tmp/mdmavenv &&
/tmp/mdmavenv/bin/pip install pyusb`, then run mdma.py with that python).
Flash loop worked first try (`reboot-qdl` → `./vamos flash kernel` → `boot`).
The by-path udev names still mismatch (M4 item): before any snapshot run,
`ln -sf /dev/video0 …platform-soc:qcom_cam-req-mgr-video-index0` and
`/dev/video1 …platform-cam_sync-video-index0`. openpilot on device
`/data/openpilot` is branch `camera-mainline-m2`, snapshot_standalone built.

### 2026-07-02 (cont.) — WEDGE SOLVED (monitor-array CAMNOC read), five walls fell, stopped at SOF freeze

**The SoC-wide NoC wedge is ROOT-CAUSED and FIXED (patch 0043, commit
`c284cc0`).** It was never the CDM BL fetch, QoS, SMMU, or the icc votes
themselves: `cam_cpas_update_monitor_array()` (cam_cpas_hw.c ~2050)
unconditionally reads three "camnoc fill level" debug registers at CAMNOC
+0xA20/0x1420/0x1A20 — offsets that exist on the newer CAMNOCs camera_kt
ships on, but hit a dead region on sdm845's Titan 170 V110 → AXI read never
completes → NoC + all CPUs hang. It's only called from
`cam_cpas_hw_update_axi_vote` (never `cpas_start`), which is why probe/init
always survived and the first IFE BW-config blob always died — and why the
wedge "floated" pre-ICC (without votes, even earlier accesses died). Chain
of evidence: 0041 (commit `7262a3d`) showed CDM reset+read surviving and the
trail dying inside the BW blob; 0042 showed every bw layer down through
`icc_set_bw` completing rc=0, trail ending exactly at the monitor call.
Guard mirrors the existing 580-only fill-level guard.

**Eliminations settled the coordinator's list:** (a) CAMNOC QoS — APPLIES
and reads back correct (`vamos-cpas: poweron QoS` breadcrumbs, safe_lut
readback 0x1 on enabled ports). Note the chip is **Titan 170 V110** per DTS
`qcom,cpas-hw-ver = <0x170110>` (the "TITAN_170_V2" from ICP FW logs refers
to Napali v2 silicon, not the CPAS table). (b) SCM safe-LUT —
`cam_cpastop_scm_write` is real (`qcom_scm_io_writel`, CONFIG_SPECTRA_SECURE)
and only serves the conditional TCSR-glitch errata; not in play. (c) SMMU —
all three cam_smmu cbs (ife/icp/cpas-cdm) bind; `cam_mem_get_io_buf` against
the CDM's own iommu handle returns rc=0 with a valid iova (0x7400000), so
the BL buffer is mapped in the CDM domain.

**Post-wedge walls, each fixed the same day:**
1. **GenIRQ INVALID_CMD (patch 0044, `694c967`):** CDM executed the three
   userspace config BLs (VFE regs written via AHB) then errored 0x10004
   (BL_DONE|ERROR_INV_CMD) on the kernel-written GenIRQ BL. The genirq
   buffer is CACHED dma-heap memory written via kernel vmap with no
   writeback; sdm845's camera SMMU is not IO-coherent → CDM fetched stale
   zeros. Fix: `cam_mem_mgr_cache_ops(CLEAN)` after writing the command.
   (Heads-up for other kernel-written camera buffers: same hazard.)
2. **Buffer import (`e581431` + openpilot msgq `1c4512a`):** no /dev/ion on
   mainline, so msgq's SConscript picked the generic shm-file VisionBuf →
   `cam_mem_mgr_map: Failed to import dma_buf fd` storm. Fix: enable
   `CONFIG_DMABUF_HEAPS(_SYSTEM)` in vamos.config (safe: the patched
   mem-mgr's heap-find is compiled out; it allocates via the 0003 cmm
   allocator) + visionbuf.cc allocates from `/dev/dma_heap/system` when
   present (dmabuf fds; DMA_BUF_IOCTL_SYNC for cache ops).
3. **Sensor nop packet rejected (openpilot `0ad682eb5`):** camera_kt's
   `cam_sensor_i2c_pkt_parse` requires len_of_buff strictly > sizeof
   (cam_packet); camerad's poke allocated exactly 64B. Fix: pad by 8.
4. **FLUSHED-state rejection (same openpilot commit):** camerad's startup
   `clearAndRequeue` issues a CRM flush-all; camera_kt (unlike 4.9) moves
   the ISP ctx to CAM_CTX_FLUSHED, which rejects UPDATE packets until a new
   INIT+START_DEV. Fix (M2-scope): skip the flush when nothing was ever
   queued (`ever_queued`). **M4 TODO:** the runtime error-recovery path
   still flushes and will hit this — needs the proper INIT+START_DEV resume
   sequence or a kernel-side relaxation.
5. **Request pool exhaustion (patch 0045, `744e7c9`):** camerad keeps
   VIPC_BUFFER_COUNT=18 requests in flight; camera_kt's
   CAM_ISP_CTX_REQ_MAX was 8 ("No more request obj free" → ENOMEM on the
   10th CONFIG_DEV). Raised to 20 (generic CAM_CTX_REQ_MAX already 20).

**Current state — stopped at SOF freeze (the next wall, one clean cycle):**
with all of the above, the road-only run completes its entire setup: probe,
acquire, CSIPHY config+start, IFE init+config (CDM BLs execute, genirq
completes, `config_hw done rc=0`, IFE BUS RD started), sensor init (312
regs) + STREAM_ON written (CCI ACKs), all 18 requests queued and scheduled,
camerad polls sync objects... and **no SOF ever arrives**:
`__cam_req_mgr_process_sof_freeze: watchdog paused, maybe stream on/off is
delayed`. Exit is clean (`road: NO FRAME (bring-up failed)`), no crash, no
wedge, device stays healthy — the dev loop is now fully network-speed.
Suspects for the next cycle, in order: CSIPHY register programming vs
legacy (mainline csiphy tables for sdm845 v1.0 PHY; settle_cnt=33,
data_rate 48MHz-units), CSID RX status registers (lane/CRC/unbounded-frame
counters — read them live while streaming), CSID input mux/VC-DT config
from the acquire packet, MCLK still running during stream. The 4.9 kernel
is the reference for a csiphy/csid register diff (same method that cracked
M1 and the wedge).

**Repo state:** branch `camera` at `744e7c9` — commits this session:
`8e498ab` (ICC DT), `650cb85` (doc), `7262a3d` (0041/0042 instrumentation),
`c284cc0` (0043 wedge fix), `694c967` (0044 genirq cache), `e581431`
(dmabuf heaps config), `744e7c9` (0045 req pool). Device `/data/openpilot`
(camera-mainline-m2) local commits: msgq `1c4512a`, openpilot `0ad682eb5`
(not pushed). Kernel on device: `6.18.0-vamos-e581431` (has 0043/0044/0045
+ heaps). Instrumentation 0040-0042 is TEMP — strip at M5 along with the
vamos-bw/vamos-cdm/vamos-cpas prints.

### 2026-07-02 (night) — M2 DONE: first frames on mainline; SOF freeze + buf_done root-caused

**FRAMES.** `snapshot_standalone` exits 0 and writes real PNGs on mainline
6.18: `snap_road.png` (1344x760, 3,065,378 B) and — in the same night —
**road+wide simultaneously** (M3 partial; distinct md5s). Both delivered to
`tmp/frames/` md5-verified against the device. Dark bench scene (no
exposure control in the snapshot tool — M4). Kernel: `6.18.0-vamos-79a93c9`
lineage (patches 0043-0049 + dmabuf-heaps config).

**SOF freeze root cause (patch 0047, commit `5954827`).** Methodical walk
up the pipe with a /dev/mem probe (tools live in /tmp of this session;
pattern documented below):
- CSIPHY config packet audit (0046, `0200afb`): CLEAN — v1.0.3 packed
  struct matches camerad's packing; runtime dump shows
  `lane_assign=0x3210 lane_cnt=4 3ph=0 settle_cnt=33 drate=48000000`,
  v1.0 table (hw_ver 0x10), lane_enable 0xd5; CSID1
  `rx_cfg0=0x132103 phy_sel=1 dt=0x2c vc=0`. The "48000000/6600000000"
  numbers are openpilot's stock ABI values (kernel divides settle by 2e8).
- CSID1 RX (devmem, mid-stream): **~30k MIPI packets/s, crc=0** — sensor
  streams, PHY locks, RX receives.
- CSID IPP: `pxl_irq_status=0x1ff8` latching — pixel path processes frames
  (its irqs are intentionally masked; SOF comes from the VFE).
- VFE1: `status0=0x1f` (SOF|EOF|EPOCH0|EPOCH1|RUP) **latched** but
  `mask0=0x3fe00` (bus bits only) and /proc/interrupts frozen — the CPU
  never gets a CAMIF irq. Cause: **`cam_vfe170.h` alone among all VFE
  variants never defines camif `subscribe_irq_mask0/1`** (vfe175/165/lites
  all use 0x17), so camif start subscribes an all-zero mask. The vfe170
  camif was never exercised on this camera_kt branch. Fix mirrors the
  other variants.

**buf_done root cause (patch 0049, commit `79a93c9`).** With SOF fixed,
requests applied per-frame but the FULL output never generated buf_done
(congestion → apply reject → CRM recovery → the runtime FLUSHED wall).
0048 breadcrumbs (`be247c9`) showed the FULL port on 170 is **2 WMs
(Y idx3 + UV idx4) with NO composite group**, and
`cam_vfe_bus_start_wm` **assigns** its done bit into
`bus_irq_reg_mask[REG1]` instead of OR-ing — the subscribe mask read
`[0x0 0x10 0x0]`, Y-done filtered out forever. Ports with comp groups
(all newer targets) never hit this. Fix: `|=`. Frames on the next run.

**M3 state:** road+wide (dual IFE) works. Driver cam (BPS): ICP FW
accepts BPS create, then `cam_icp_process_stream_settings: FW response
timed out -110` on "config io mapping" at acquire → `configICP()`
assert. Next M3 cycle starts there (HFI config-io handling vs CICP.FW
1.0-00050).

**Operational notes for the resumer:**
- The device drops off the network (and possibly wedges) ~60-90 s after
  boot when idle — the old "idle death" confound is still live (gpio/sound
  masking does not obviously prevent it). Practical loop: `mdma boot`,
  then do ALL ssh work immediately; large file pulls die mid-scp — use
  chunked `dd | base64` over fresh connections (~2-3 512K chunks per
  window) and md5-verify.
- /dev/mem probes MUST touch only clocked blocks: reading the inactive
  CSID0/CSID-lite while CSID1 streams wedges the NoC (same signature as
  the 0043 bug). CONFIG_DEVMEM=y, STRICT_DEVMEM allows MMIO.
- TEMP patches to strip at M5: 0040-0042, 0046, 0048, plus the update_wm
  dump inside 0049 (keep its one-line |= fix) and the vamos-phy/csid/bus2
  prints.

### 2026-07-02 (later) — M4 done (2-cam camerad); M3 BPS parked on the ICP FW watchdog

**M4 (2-cam): the real `camerad` works on mainline.** After rebuilding
camerad on-device (with the dma-heap VisionBuf), `DISABLE_DRIVER=1 camerad`
ran 5+ minutes: both IFEs streaming at ~20 fps (IRQ 497/499 rising ~72/s
and ~62/s, steady across the whole run), zero CAM_ERR in dmesg, no
clearAndRequeue/CRM recovery at all in steady state, and auto-exposure
live — a mid-run frame grabbed via the Python VisionIPC client
(`frame_id=6315`, y_mean 41.9 vs the near-black defaults) shows a clearly
recognizable bench scene. Artifacts: `tmp/frames/midrun_road.{nv12,png}`
(md5-verified pull). Note camerad survives the ~90 s network drop-offs
(the drop is network-only; the device and camerad keep running — this
reframes the "idle death": it appears to be **wifi/connectivity loss, not
a SoC wedge**, at least while camerad is active).

**M3 (driver cam / BPS): parked with a precise wall.** Chain of findings:
- The MEM_MAP -110 was a symptom: the ICP FW dies with SYS_ERROR
  SFR "ICP SS WD Timeout" a constant **~0.7 s after its first boot**, in
  every configuration tried: (a) hard-close boot PC (no handshake),
  (b) proper PC_PREP + proc_suspend (after adding `icp_pc_en` to the
  cam-icp DT node — kept, commit `02e4e9f`, it is correct), and
  (c) FW left running untouched (temporary patch 0050, since dropped) —
  i.e. the FW does not service its own watchdog on mainline even when
  healthy and running.
- The downstream a5 node enables 4 extra clocks we lack
  (icp_apb/atb/cti/ts). All exist in mainline camcc-sdm845 but ALL fail
  to enable ("status stuck at off", -EBUSY) — they are QDSS-fed and the
  QDSS infrastructure (tsgen etc.) is down on mainline. Adding them
  breaks the ICP subdev open outright; reverted.
- Working hypothesis for the next cycle: the a5 FW's timekeeping/WD-pet
  depends on the QDSS timestamp/APB plumbing that AGNOS 4.9 brings up
  (check qdss tsgen + gcc qdss clocks state under legacy; consider a
  minimal tsgen enable or coresight etm/stm config on mainline).
- State restored to stable-for-IFE: 0050 dropped, a5 clock list back to
  original, `icp_pc_en` kept (FW parks cleanly in PC after boot; with no
  BPS acquire the WD landmine never triggers — 2-cam operation verified
  unaffected).

**M5 remains:** strip TEMP patches (0040-0042, 0046, 0048, 0049's dump
hunk — keep the |= fix), renumber, clean rebuild + verification run
(probe 3/3, 2-cam snapshot, short camerad run), re-author the two
device-side openpilot commits host-side and push to the fork
(`camera-mainline-m2`): local commits openpilot `0ad682eb5` + msgq
`1c4512a` are saved as patch files in `tmp/openpilot-patches/` (device
has no push credentials).

### 2026-07-02 — M5 cleanup executed + verified; build-system gotcha found

**Patch series cleaned.** TEMP instrumentation stripped (old spectra
0040-0042 breadcrumbs, 0046 csiphy/csid dumps, 0048 bus dumps, and the
update_wm dump inside the buf-done fix). The five real fixes were
regenerated against the clean series and renumbered (`52293a0`):

Kernel patches (`kernel/patches/`, 26): 0001 dts hook, 0002 ath10k quirk,
0003/0006 panels, 0004/0005 dispcc, 0007-0009 touch, 0010 spidev,
0011 wifi MAC, 0012 squashfs discard, 0013-0015 modem/rmtfs, 0016 msm
camera hook, 0017/0018 camcc GDSC sw-control, 0019/0020 regulator
(0020 = breadcrumbs, strip later), 0021/0025 GDSC wait timing, 0022 MCLK
hw_clk_ctrl, 0023/0024 UFS, 0026 mmnoc TBU always-on.

Spectra series (`kernel/spectra-qcom/patches/`, 42): 0001-0016 port
integration + CCI/legacy-compat fixes; 0017-0036 NACK-era diagnostics
(mostly disabled by 0024/0038 — strip candidates); 0037 OPP skip;
0038 quiesce; 0039 CDM breadcrumbs (TEMP, kept for now); and the five
bring-up fixes:
  0040 cpas: skip camnoc fill-level monitor reads on Titan 170 v1xx (NoC wedge)
  0041 cdm: clean genirq buffer cache before BL commit (INVALID_CMD)
  0042 isp: CAM_ISP_CTX_REQ_MAX 8→20 (camerad queue depth)
  0043 vfe170: camif subscribe_irq_mask (SOF freeze)
  0044 vfe bus: OR per-WM done bits (buf_done)

**BUILD-SYSTEM GOTCHA (bit us here):** `install_spectra_qcom` copies the
submodule with `cp -a` (old mtimes) into the persistent `kernel/linux/out`
incremental build. A source file whose patch is REMOVED reverts to an old
mtime and make keeps the **stale object** — the first M5 kernel still
contained dropped-patch code (caught via a leftover pr_err string at
runtime). After removing any patch, purge
`kernel/linux/out/drivers/media/platform/msm/camera` before building.

**Verification (kernel `52293a0`, clean rebuild): GREEN.**
probe 3/3 OK; 2-cam snapshot exit 0 (road+wide PNGs); camerad 2+ min with
both IFEs at ~63 irq/s each (~20 fps), no camera errors (only the
pre-existing benign boot noise: "Unsupported Bus RD Version 0x0" ×2 and
"cam_vfe_hw_init: inval param" ×4 from the optional bus_rd/vfe-lite
probes, plus teardown "releasing hw").

**openpilot commits:** host checkout re-authored and PUSHED —
`greatgitsby/openpilot` `camera-mainline-m2` now has `61de3d0f7`
(nop-packet pad + startup-flush skip). The msgq visionbuf dma-heap commit
could NOT be pushed (no `greatgitsby/msgq` fork exists and creating one
needs user action) — it remains applied on the device and saved as
`tmp/openpilot-patches/msgq-visionbuf-dmaheap.patch`. **Action item:
create the msgq fork and push, or vendor the visionbuf change.**

**Known remaining issues:**
1. M3 driver cam: ICP FW "ICP SS WD Timeout" ~0.7 s after boot in every
   config (QDSS/tsgen theory queued — legacy comparison pending).
2. Wifi drop-offs ~60-90 s under load/idle (device keeps running; network
   only). Makes big file pulls need chunked dd|base64.
3. gpio.service / sound.service unported — runtime-masked each boot.
4. Exposure tuning: AE works but bench frames are dim; no tuning pass.
5. udev by-path names still need the runtime symlinks (M4 leftover).
6. Runtime CRM flush recovery path still hits camera_kt FLUSHED-state
   rejection (startup flush skipped; error-path needs INIT+START_DEV).
7. tizi (OX03C10) untouched.
8. Strip candidates on next pass: kernel 0020, spectra 0017-0036
   diagnostics, 0038/0039.

### 2026-07-02 — M3 hunt round 2: QDSS clock theory eliminated; parked with full evidence

**Legacy comparison (flashed 4.9.103, back on mainline after):**
- Legacy ICP FW never hits the watchdog (zero SYS_ERROR/WD events across
  FW download + 5 s idle; the boot-time power collapse parks it cleanly
  and resume works — the release camerad segfault is unrelated UAPI drift).
- Legacy holds **qdss_qmp_clk enable=8** (the AOSS QMP "qdss" clock);
  all five camcc ICP branch clocks read 0 outside active windows on both
  kernels, so the camcc branches are NOT the differentiator.
- Tested the resulting theory (`5de508b`): enable the mainline
  `&aoss_qmp` qdss clock alongside the a5 clocks (soc_util enables it at
  a5 init). **Did NOT fix it** — identical "SFR: ICP SS WD Timeout" ~0.7 s
  after first boot, surfacing at acquire-time resume. The commit stays as
  harmless legacy-parity; 2-cam verified unaffected (snapshot exit 0 ×2).

**Accumulated M3 evidence for the next attempt:**
- WD fires at first-boot +0.7 s in ALL configs: hard-close PC, PC_PREP
  handshake PC (icp_pc_en), FW left running untouched, and now with the
  qdss qmp clock on. A running, healthy, SYS_INIT'ed FW does not pet its
  WD on mainline.
- camcc icp_apb/atb/cti/ts branches are enable-stuck (-EBUSY, upstream
  off) on mainline — and legacy ALSO leaves them at 0 when idle, so the
  FW does not depend on them being SW-enabled.
- Remaining angles (untried): (a) diff legacy 4.9 cam_icp/a5 driver code
  for CSR writes at init (a5_qos, timer/wdt CSR bits) — needs the AGNOS
  kernel source tree (not on this host); (b) the a5 "Sierra" CSR block
  (0xac10000) register diff legacy-vs-mainline during the FW-alive window
  via devmem (same method that cracked M1); (c) FW variants on rootfs
  (only CAMERA_ICP.elf shipped — check /lib/firmware + /vendor for
  alternates); (d) whether the FW's WD pet depends on receiving periodic
  HFI traffic (legacy camerad segfaults ~1 s in — did its FW live longer
  than 0.7 s only because the driver sent PC_PREP within the window?
  Instrument mainline to send a benign HFI (e.g. PROPERTY query) at
  +0.5 s and see if the WD deadline extends).
