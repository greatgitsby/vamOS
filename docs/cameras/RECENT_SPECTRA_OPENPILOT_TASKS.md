# Recent Qualcomm Spectra Driver + openpilot Task Plan

Design doc: [RECENT_SPECTRA_OPENPILOT_DESIGN.md](./RECENT_SPECTRA_OPENPILOT_DESIGN.md).

This task plan is written for parallel Codex workers. Each task has an owner
lane, repo, dependencies, files, acceptance checks, and conflict notes. Start
from clean branches named `spectra-uapi-migration-plan` in both repos.

## 0a. Progress Log (read this first)

Last updated: 2026-06-15.

### openpilot — done and pushed

Committed on `origin/spectra-uapi-migration-plan` (commit `e8fe170ad`,
"camerad: migrate Spectra call sites to recent camera_kt UAPI"). This is the
real source migration of the changed Spectra call sites to the v1.0.3
`camera_kt` UAPI layout:

- CSIPHY (`spectra.cc configCSIPHY`): `cam_csiphy_info` dropped
  `lane_mask`/`csiphy_3phase`/`combo_mode`; now sets `reserved=0`,
  `mipi_flags=0` (DPHY, no combo). Covers task O4.2's call-site change.
- ISP (`spectra.cc configISP`): removed `.custom_csid`, absent from recent v0
  `cam_isp_in_port_info`. Covers task O4.3's call-site change.
- `camera_qcom2.cc`: `frame_id`/`request_id` are now `__u64`; `%lu` -> `%llu`.
- Sensors (`os04c10.cc`, `ox03c10.cc`): removed stale
  `<media/msm_camsensor_sdk.h>` include (deleted upstream by "use linux headers
  from /usr", PR #37993; absent from `camera_kt` UAPI). The `CSI_RAW10`/
  `CSI_RAW12` MIPI data-type codes it transitively provided are now defined in
  `sensor.h` (standard MIPI values, openpilot-owned).

### openpilot — TEMP local scaffolding, intentionally NOT committed

These exist only in the local working tree to allow off-device build
verification. Do NOT commit them. Re-create as needed; remove before any real
PR.

- `third_party/qcom_spectra_uapi/camera_kt_v1_0_3/` — uncommitted v1.0.3 UAPI
  headers only, with no MANIFEST or README. DECISION (2026-06-14, user): the
  real plan sources headers from `/usr` on the device (installed by the vamOS
  kernel build, lane K1.2), NOT from a vendored openpilot tree. So O1.1/O1.2 as
  originally written are superseded. The local copy is only an off-device
  compile crutch; the source of truth is the explicit vamOS submodule checkout.
- `system/camerad/SConscript` — temp edits: force `clang` on x86_64 (match the
  device compiler), a `Configure`-based UAPI auto-detect that prepends the
  vendored path when the system headers are not the recent `camera_kt` UAPI
  (probed via `CAM_QUERY_CAP_V2`), and gating `env.Program('camerad')` to
  `larch64` so off-device builds compile objects only (no link).
- `SConstruct` — temp: also run `system/camerad/SConscript` on `x86_64`
  (normally `larch64`-only) so camerad objects compile off-device.

How to re-verify off-device after re-creating the above:

```bash
cd /home/trey/claudes/openpilot && source .venv/bin/activate
scons -j$(nproc) system/camerad/cameras/spectra.o \
  system/camerad/cameras/camera_qcom2.o system/camerad/cameras/camera_common.o \
  system/camerad/cameras/cdm.o system/camerad/sensors/ox03c10.o \
  system/camerad/sensors/os04c10.o
# Expect: "scons: done building targets." with 0 errors.
```

As of this writing all six camerad objects compile clean against v1.0.3.
This verifies compilation only; the full `camerad` link + run must happen
on-device (`larch64`). mici (10.0.0.22) was offline during this work.

### openpilot — remaining tasks (NOT yet done)

- O2.1 — DONE (uncommitted, local). Added
  `system/camerad/cameras/spectra_uapi_version.h` with all six static_asserts
  (OPCODE_MAX, SENSOR_PROBE_CMD, and sizeof cam_cmd_i2c_info / i2c_rdwr_header /
  cam_cmd_unconditional_wait / cam_csiphy_info), a `SPECTRA_UAPI_VERSION` string,
  and `spectra_uapi_version()`. Included from `spectra.cc`; added a startup ABI
  banner (`LOGW`) in `SpectraMaster::init`. Verified: asserts pass against
  v1.0.3, and a negative test confirms they fire on a mismatched layout. This is
  the PRIMARY mismatch guard under the `/usr`-header model. Commit once the
  header-sourcing approach is finalized.
- O3.1/O3.2 — DONE (uncommitted, local). Added
  `system/camerad/cameras/spectra_device_nodes.{h,cc}` with
  `open_v4l_video_by_name()` (matches `/sys/class/video4linux/videoN/name`,
  falls back to the legacy by-path string, logs all discovered video nodes on
  failure). Wired into `SpectraMaster::init` for `cam-req-mgr` (video0) and
  `cam_sync` (video1), keeping the old by-path strings as fallbacks. Subdev
  opens already used name matching via the existing
  `open_v4l_by_name_and_index` (that helper matches `v4l-subdevN`; the new one
  matches `videoN`). Compiles clean against v1.0.3.
- O4.1/O4.4 — packet sizing: DONE the substantive part (uncommitted, local).
  Found and fixed a real bug: `openSensor()` allocated the sensor probe power
  buffer with a hardcoded `196` bytes, computed for the OLD AGNOS struct sizes.
  Under v1.0.3 the same power-command sequence needs 248 bytes, so the old code
  under-allocated by 52 bytes (heap overflow during probe). Replaced with
  `kProbePowerBufSize`, a `constexpr` computed from `sizeof` (verified == 248
  for v1.0.3), plus a `power_block_size()` helper. This is the O4.1 "compute
  packet sizes from sizeof, don't hardcode" intent applied to the one remaining
  magic number; the i2c/probe descriptors already used `sizeof`.
- O4.4 — req-mgr new-field init: NO CODE NEEDED. v1.0.3 does add
  `cam_req_mgr_sched_request.additional_timeout`/`.reserved` and
  `cam_req_mgr_link_control.init_timeout[]`, BUT all three call sites already
  use `= {0}` aggregate init, which zero-initializes every member including the
  new ones. Adding explicit `.additional_timeout = 0` etc. would be redundant.
  Startup ABI log: DONE as part of O2.1 (the LOGW banner in `SpectraMaster::init`
  prints the UAPI version, CAM_COMMON_OPCODE_MAX, and key struct sizes).
- O4.2/O4.3 — helper FILES (`spectra_csiphy_config.*`, `spectra_isp_config.*`)
  were NOT created; the inline call-site changes are done and committed (CSIPHY +
  ISP, commit e8fe170ad). Extracting them into helper files per the original
  lane split is optional polish, not required for the build to pass.

### openpilot — standalone bring-up tool (committed as temp)

`system/camerad/snapshot_standalone.cc` + a `snapshot_standalone` Program in
`system/camerad/SConscript` are committed (commit `14d9d1bbf`) as the camera
bring-up dev loop for this migration. Imported from the vamOS reference copy
(`tools/camera/snapshot_standalone.cc`, stash commit `336159a`; see that file's
README for the full on-device build/run recipe). It reuses the real
`SpectraMaster`/`SpectraCamera` path, so it exercises ALL the migrated code
(CSIPHY, ISP, probe-buffer sizing, node discovery, UAPI guard), brings up every
camera, writes `snap_{wide,road,driver}.{nv12,png}`, and exits 0 if all enabled
cameras produced a frame else 1. Verified: its object compiles clean against the
migrated tree off-device. On-device build note: openpilot's `SConstruct` reads
`selfdrive/modeld/SConscript` whose tinygrad probe aborts scons on the comma
four — skip that one line for the build (see the README). The full link + run is
blocked on the kernel DTS gate (no camera nodes yet; see
`validation/2026-06-14-mici-node-probe.md`).

### vamOS — not started by this worker

P0/K/V lanes (submodule pin exists; kernel import/build/DTS/validation pending).
The `/usr` header decision means lane K1.2 (install UAPI into the kernel build,
shipping to `/usr/include/media`) is the authoritative header source for
openpilot — keep it aligned with the same v1.0.3 commit.

## 0. Ground Rules

- Do not import old branch docs, old proof bundles, or old AGNOS-port WIP.
- Do not apply the pre-existing vamOS stash unless explicitly asked.
- Keep vamOS commits separate from openpilot commits.
- Keep one worker per lane unless a lane is split into explicitly independent
  files.
- Add helper files first; keep direct edits to shared files small.
- Every task must end with `git status --short` in the relevant repo.
- If a task changes an interface used by another lane, update this file in the
  "Interface Notes" section.

Branch setup:

```bash
git -C /home/trey/claudes/vamOS switch spectra-uapi-migration-plan
git -C /home/trey/claudes/openpilot switch spectra-uapi-migration-plan
```

Source submodule setup:

```bash
git -C /home/trey/claudes/vamOS submodule update --init --depth 1 \
  kernel/spectra-qcom/camera-driver
git -C /home/trey/claudes/vamOS/kernel/spectra-qcom/camera-driver \
  log -1 --format='%H %cd %s' --date=short
```

## 1. Dependency Graph

```text
P0 source submodule/audit
  -> K1 source install/build glue
  -> K2 compile port
  -> K3 DTS
  -> V1 boot/node validation
  -> V2 sensor probe
  -> V3 frame streaming

P0 source submodule/audit
  -> O1 vendored UAPI
  -> O2 ABI guard
  -> O3 node discovery
  -> O4 packet/layout migration
  -> V2 sensor probe
  -> V3 frame streaming
```

Parallel from day one:

- P0 can run first and then unblock K1 and O1.
- K2 and K3 can run in parallel after K1 creates the source install structure.
- O3 and O4 can run in parallel after O1/O2 define the UAPI include path.
- Validation workers can build scripts independently while K/O lanes progress.

## Current Status - 2026-06-14

- K1.2 and K1.3 are implemented in vamOS using the pinned Qualcomm
  `camera-driver` submodule and Qualcomm's own `camera-driver/Kbuild`.
- `./vamos build kernel` completes with `CONFIG_SPECTRA_CAMERA=y`; the build log
  shows `drivers/media/platform/msm/camera/built-in.a` linked into the kernel.
- A minimal active `qcom,camera_kt` root with only `qcom,cam-req-mgr` and
  `qcom,cam-sync` was tested in commit `5b02808` and boot-looped before Linux
  printed anything. MDMA `profile-boot` reached ABL `Exit BS`, then the device
  restarted roughly 58 seconds later. Commit `f9ca57e` backs out only that DTS
  root; mici boots again and reports
  `6.18.0-vamos-f9ca57e` from `uname -a`.
- A root-only `qcom,camera_kt` node under `&soc` was tested in commit
  `c6783a6` and boots on mici. `uname -a` reports
  `6.18.0-vamos-c6783a6`, and dmesg reports
  `Spectra camera_kt driver initialized rc : 0`. No camera device nodes appear
  yet because no child platform devices are present.
- A sync-only child increment was tested in commit `12b6ff1` and also boots on
  mici. `uname -a` reports `6.18.0-vamos-12b6ff1`; sysfs shows
  `ac00000.camera-kt:cam-sync` bound to `cam_sync`. No video/media nodes appear
  yet because req-mgr is not present.
- A req-mgr child increment was tested in commit `4b159fe` and boots on mici.
  Req-mgr binds sync, `/dev/video0` is `cam-req-mgr`, `/dev/video1` is
  `cam_sync`, and `/dev/media0`/`/dev/media1` are present.
- An addressable-root increment was tested in commit `48cdb8e` and boots on
  mici. Adding `#address-cells`, `#size-cells`, and `ranges` to the camera root
  preserves req-mgr/sync binding and the same video/media nodes.
- A CPAS child increment was tested across three commits:
  - `ed1c859` booted with CPAS present in the live DT, then failed CPAS bind at
    OPP table setup (`OPP add_table failed ... rc -19`).
  - `535a0be` added the CPAS OPP table and booted, then failed CPAS bind at the
    default AHB ICC vote because the AHB table had only two usecases while the
    driver voted enum level 3.
  - `9703bd1` expanded the AHB vote table to eight enum-indexed usecases. This
    boots on mici as `6.18.0-vamos-9703bd1`; `ac40000.cam-cpas` binds to
    `cam-cpas`; req-mgr binds both sync and CPAS; `/dev/video0` is
    `cam-req-mgr`, `/dev/video1` is `cam_sync`, and `/dev/media0`,
    `/dev/media1`, and `/dev/v4l-subdev0` are present.
- K3.1 fresh DT audit is recorded in `docs/cameras/dts-audit.md`.
- K3.2 CPAS base is done through the boot-verified checkpoint in `9703bd1`.
- The current checked-out openpilot branch only consumes `cam-req-mgr`,
  `cam_sync`, `cam-isp`, `cam-icp`, three `cam-sensor-driver` indices, and
  three `cam-csiphy-driver` indices. Its driver camera uses BPS through ICP;
  there is no openpilot consumer for JPEG, LRME, FD, OPE, TFE, SFE, custom
  camera blocks, IPE, or a fourth camera slot in this branch.
- `8770602` added the first CDM-interface DTS checkpoint but was not flashed.
  A follow-up trim keeps the CPAS/CDM client lists to the openpilot path only:
  CSIPHY0-2, CCI0, CSID0-2, IFE0-2, virtual CDM, CPAS CDM, BPS0, and ICP0.
  The next DTS checkpoints should be isolated as SMMU, real CPAS CDM, ISP/ICP,
  and then CCI/CSIPHY/sensors.
- `d594129` booted on mici as `6.18.0-vamos-d594129`. The trimmed
  CDM-interface checkpoint binds `ac00000.camera-kt:cam-cdm-intf` to
  `msm_cam_cdm_intf`; req-mgr still binds sync, CPAS, and CDM interface; and
  `/dev/video0`, `/dev/video1`, `/dev/media0`, `/dev/media1`, and
  `/dev/v4l-subdev0` are present.
- `c908681` added only the openpilot-required Spectra SMMU context banks and
  booted on mici as `6.18.0-vamos-c908681`. The SMMU parent, `ife`, `icp`,
  `cpas-cdm0`, `cam-secure`, and ICP firmware child all bind to
  `msm_cam_smmu`; the existing req-mgr/sync/CPAS/CDM nodes remain present.
  Known warning: each non-secure SMMU CB logs `iommu_set_fault_handler()` because
  mainline 6.18 refuses setting a fault handler on DMA-cookie domains. The
  downstream driver ignores that path and binding continues.
- `a26a734` attempted the first real CPAS CDM hardware node
  (`qcom,cam170-cpas-cdm0`) with only the openpilot-needed `ife`/`ife3` CDM
  clients plus an SMMU alias for `cpas-cdm`. It built and flashed, but did not
  boot: a 10-second MDMA `profile-boot` reached ABL `Exit BS` / `UEFI End` at
  about 4.55s and printed no Linux earlycon output. `818c807` reverts only that
  CPAS CDM increment and boots on mici as `6.18.0-vamos-818c807`. Do not re-add
  the `a26a734` CPAS CDM shape wholesale; reintroduce it as smaller
  boot-verified slices.
- Split CPAS CDM retest results:
  - `f2fc7a2` adds only the SMMU alias
    `cam-smmu-label = "cpas-cdm0", "cpas-cdm"` and boots on mici as
    `6.18.0-vamos-f2fc7a2`.
  - `3655f26` adds the `qcom,cam170-cpas-cdm0` node with full legacy-style
    resources and `ife`/`ife3` clients, but leaves it `status = "disabled"`.
    It boots on mici as `6.18.0-vamos-3655f26`; diagnostics show the same
    req-mgr/sync/CPAS/virtual-CDM/SMMU nodes as the SMMU checkpoint, and no
    hardware CDM bind.
  - `70e1533` enables the same node but trims `cdm-client-names` to the legacy
    AGNOS value `"ife"`. It still does not boot: `uname -a` never returns, and a
    10-second MDMA `profile-boot` again reaches ABL `Exit BS` / `UEFI End` at
    about 4.56s with no Linux output. `dde427c` reverts only that enablement
    and boots on mici as `6.18.0-vamos-dde427c`.
  - Conclusion: the SMMU alias and disabled DT node are safe; the failure is the
    enabled CPAS CDM probe/power/reset path, not the extra `ife3` client name.
- The next active hardware checkpoint keeps CPAS CDM disabled and adds only the
  openpilot-needed ISP/ICP/BPS blocks. It also adds
  `kernel/spectra-qcom/patches/0002-cam-soc-skip-probe-power-cycle.patch`, which
  lets DTS nodes opt out of the recent `camera_kt` probe-time GDSC
  enable/disable pulse via `qcom,skip-probe-power-domain-cycle`. Runtime power
  enablement is unchanged; this is a boot isolation guard for the hardware-node
  bind path.
- While this checkpoint is boot-stall-prone, `tools/build/build_kernel.sh` uses
  the actual mainline Qualcomm GENI earlycon name
  `earlycon=qcom_geni,0x00a84000,115200n8` plus `keep_bootcon`,
  `ignore_loglevel`, `loglevel=8`, and `initcall_debug`. This is deliberate
  diagnostic noise for MDMA `profile-boot`; remove or quiet it after the DTS
  hardware-node gate is stable.
- First raw serial capture with that earlycon command line reaches Linux and
  `camera_init`, then reports `bps_gdsc status stuck at 'off'` while generic
  platform probing calls `dev_pm_domain_attach()` for `qcom,bps`. Because this
  happens before the Spectra driver's probe-time skip property can run, the next
  boot checkpoint keeps `qcom,bps` in the graph but omits its mainline
  `BPS_GDSC` `power-domains` attachment. BPS runtime power is now an explicit
  follow-up after the kernel boots with the minimal ISP/ICP graph.
- The `9275abf` checkpoint passes that BPS attach point: `qcom,bps` probes and
  `camera_init` returns. The next failure is deferred component binding:
  CSID/VFE resource init calls `devm_pm_opp_of_add_table()` through
  `cam_soc_util_configure_opp()` and fails with `-ENODEV` because those nodes do
  not yet have `operating-points-v2`. The staged fix adds OPP tables for CSID,
  VFE, A5, and BPS using the existing source-clock frequencies from
  `clock-rates`.
- The `7082098` checkpoint boots to shell and gets past CSID/VFE OPP setup. It
  then fails ICP aggregate binding because `cam_icp_mgr_alloc_devs()` requires
  `num-ipe`, and the ICP manager stores an IPE interface even though openpilot's
  immediate path is BPS-focused. The next staged DT adds `qcom,ipe0` and
  `qcom,ipe1` with clocks/OPPs and wires them into `qcom,cam-icp`; their
  mainline IPE GDSCs stay unattached for this checkpoint, matching the BPS GDSC
  staging rule.
- The `6e7e83f` checkpoint boots and probes IPE0/IPE1, then CPAS rejects IPE0
  registration because `client-names` lacked `ipe0`/`ipe1`. The next staged DT
  adds only those CPAS client names; the legacy AGNOS list uses the same names.
- Current mainline debug checkpoint boots on mici as
  `6.18.0-vamos-9985ba6 #191`. The camera power wiring has been aligned and
  validated against the legacy kernel for the three openpilot sensors: VANA
  (BOB/gpio8), rear DVDD (`camera_rear_ldo`/PM8998 gpio12), VIO (`lvs1`),
  reset GPIOs, MCLK0/1/2, CCI0/1 pinctrl, and slot 2's legacy gpio28 pinctrl
  are all active in the powered probe window. MCLK pad sampling shows 24 MHz
  toggling for all three slots.
- `system/camerad/spectra_camera_test --sensor os04c10 --probe-only` still
  reports `0/3 cameras OK`, but the failure is now sharply scoped: tight TLMM
  sampling immediately after `CCI_QUEUE_START` shows SDA/SCL transitions on CCI
  master 0 and master 1, followed by a real address NACK. The remaining blocker
  is no longer basic DTS power/pinctrl plumbing; compare recent-driver CCI queue
  programming and master setup against the known-good legacy 4.9 driver next.

## 2. Lane P0 - Source Submodule And Audit

### Task P0.1 - Pin Qualcomm source snapshot submodule

Repo: vamOS

Dependencies: none

Files:

- `.gitmodules`
- `kernel/spectra-qcom/camera-driver` submodule

Work:

- Create `kernel/spectra-qcom/`.
- Add `https://github.com/qualcomm-linux/camera-driver.git` as a submodule at
  `kernel/spectra-qcom/camera-driver`, tracking branch
  `camera-kernel.qclinux.0.0`.
- Pin the submodule gitlink to commit
  `56b463cba50c1db1f2cc53ddd8790730f14bd8a8` (`v1.0.3`).
- Do not add a separate source `README.md` or `MANIFEST`; the submodule URL,
  branch, and gitlink are the source record.
- Keep the `camera_kt/` selection rationale and SDM845 evidence in this plan
  and the companion design doc:
  - `qcom,csiphy-v1.0`
  - `cam_csiphy_1_0_hwreg.h`
  - `qcom,vfe170`
  - IFE resource IDs used by openpilot still present.

Acceptance:

```bash
test -d /home/trey/claudes/vamOS/kernel/spectra-qcom/camera-driver/camera_kt
git -C /home/trey/claudes/vamOS submodule status \
  kernel/spectra-qcom/camera-driver | rg "56b463c"
rg -n "qcom,csiphy-v1.0|cam_csiphy_1_0_hwreg|qcom,vfe170" \
  /home/trey/claudes/vamOS/kernel/spectra-qcom/camera-driver/camera_kt
git -C /home/trey/claudes/vamOS status --short
```

Conflict notes: none.

## 3. Lane KIMPORT - Kernel Source Import And Build Glue

### Task K1.1 - Verify submodule source layout

Repo: vamOS

Dependencies: P0.1

Files:

- `kernel/spectra-qcom/camera-driver` submodule

Work:

- Treat the Qualcomm submodule as the source of truth for the imported driver.
- Do not copy Qualcomm source into the vamOS git tree.
- Do not edit the submodule for vamOS porting work. Keep the gitlink pinned and
  carry porting deltas as local patches, build glue, or files under
  `kernel/spectra-qcom/compat/`.
- Use this source path contract in build glue:
  `kernel/spectra-qcom/camera-driver/`.
- Reuse Qualcomm's top-level `camera-driver/Kbuild` and
  `config/qcm6490-camera.mk` as the object-list/include-path contract. Do not
  create a parallel vamOS-maintained source list unless the vendor Kbuild stops
  being usable.

Acceptance:

```bash
test -f /home/trey/claudes/vamOS/kernel/spectra-qcom/camera-driver/camera_kt/include/uapi/camera/media/cam_defs.h
test -d /home/trey/claudes/vamOS/kernel/spectra-qcom/camera-driver/camera_kt/drivers/cam_req_mgr
test -d /home/trey/claudes/vamOS/kernel/spectra-qcom/camera-driver/camera_kt/drivers/cam_sensor_module
git -C /home/trey/claudes/vamOS status --short
```

Conflict notes: P0 owns the submodule gitlink. KIMPORT owns the build contract
that consumes `kernel/spectra-qcom/camera-driver/camera_kt`.

### Task K1.2 - Add deterministic build install/cleanup

Repo: vamOS

Dependencies: K1.1

Files:

- `tools/build/build_kernel.sh`

Work:

- Add `install_spectra_qcom()` after patch application and before kernel build.
- Copy the vendor build unit from `kernel/spectra-qcom/camera-driver/` into
  `kernel/linux/drivers/media/platform/msm/camera/`:
  - `Kbuild`
  - `config/`
  - `common/`
  - `camera_kt/`
- Generate the small `cam_generated_h` header that Qualcomm's standalone
  `Makefile` normally creates before invoking Kbuild.
- Copy UAPI headers from
  `kernel/spectra-qcom/camera-driver/camera_kt/include/uapi/camera/media`
  into `kernel/linux/include/uapi/media/cam_*.h`.
- Apply any tracked local Spectra patch stack after the source copy and before
  kernel build.
- Add cleanup to remove copied Spectra files during `clean_kernel_tree`.
- Make the install idempotent and deterministic.

Acceptance:

```bash
cd /home/trey/claudes/vamOS
bash -n tools/build/build_kernel.sh
rg -n "install_spectra_qcom|spectra-qcom|include/uapi/media" tools/build/build_kernel.sh
git status --short
```

Conflict notes: only KIMPORT edits `tools/build/build_kernel.sh`.

### Task K1.3 - Add Kconfig/Makefile link patch

Repo: vamOS

Dependencies: K1.2

Files:

- `kernel/patches/0012-media-platform-add-msm-camera-hook.patch`
- `kernel/spectra-qcom/patches/0001-integrate-camera-driver-kbuild.patch`
- `kernel/configs/vamos.config`

Work:

- Add the smallest possible patch to include the copied msm camera directory in
  mainline media platform build.
- Add a local patch on the copied vendor tree that:
  - defaults `CAMERA_ARCH` to `qcm6490`
  - points `CAMERA_KERNEL_ROOT` at the copied in-tree location
  - builds the vendor composite object through `CONFIG_SPECTRA_CAMERA`
  - keeps the qcm6490 vendor feature selection in
    `config/qcm6490-camera.mk`
- Add or enable `CONFIG_SPECTRA_CAMERA`.
- Enable required media dependencies.
- Avoid broad config churn.
- Do not import or rely on old branch device tree changes.

Acceptance:

```bash
cd /home/trey/claudes/vamOS
git -C kernel/linux apply --check ../patches/0012-media-platform-add-msm-camera-hook.patch
rg -n "SPECTRA|CAMERA|MEDIA|VIDEO_DEV" kernel/configs/vamos.config
git status --short
```

Conflict notes: only KIMPORT edits this link patch and `vamos.config`.

## 4. Lane KPORT - Kernel Compile Port

### Task K2.1 - First compile surface

Repo: vamOS

Dependencies: K1.3

Files:

- `build/logs/spectra-qcom-build-1.log` or a tracked summary under
  `docs/cameras/build-surfaces/`

Work:

- Run a kernel build with `CONFIG_SPECTRA_CAMERA=y`.
- If it fails in the recent Spectra tree, capture the first complete compile
  surface and categorize errors by subsystem:
  - kbuild/include path
  - V4L2/media
  - DMA/IOMMU
  - interconnect/bus
  - SCM/socinfo/Qualcomm private helpers
  - clocks/regulators/pinctrl
  - timers/debugfs/misc Linux API drift
- If it succeeds, record the build artifact and move to boot/node validation.

Acceptance:

```bash
cd /home/trey/claudes/vamOS
./vamos build kernel 2>&1 | tee /tmp/spectra-qcom-build-1.log || true
test -s /tmp/spectra-qcom-build-1.log
git status --short
```

Conflict notes: analysis-only unless a tracked summary is added.

### Task K2.2 - Add local compatibility wrapper structure

Repo: vamOS

Dependencies: K2.1

Files:

- `kernel/spectra-qcom/compat/README.md`
- `kernel/spectra-qcom/compat/*.h`
- local Spectra source patches under `kernel/spectra-qcom/patches/` if include
  edits are needed

Work:

- Create a local wrapper location for mainline compatibility helpers.
- Move compile-only shims there.
- Keep the Qualcomm submodule pristine. If a source include or API edit is
  required, add a tracked patch under `kernel/spectra-qcom/patches/` and have
  `install_spectra_qcom()` apply it to the copied kernel tree.
- Document every shim with:
  - why it exists
  - whether it is on the active mici camera path
  - what retires it

Acceptance:

```bash
test -f /home/trey/claudes/vamOS/kernel/spectra-qcom/compat/README.md
rg -n "active path|retire|stub|shim" /home/trey/claudes/vamOS/kernel/spectra-qcom/compat/README.md
git -C /home/trey/claudes/vamOS status --short
```

Conflict notes: KPORT owns `kernel/spectra-qcom/compat`.

### Task K2.3 - Clear compile errors by subsystem

Repo: vamOS

Dependencies: K2.1, K2.2

Files:

- `kernel/spectra-qcom/compat/**`
- `kernel/spectra-qcom/patches/**`

Work:

- Fix one subsystem per commit.
- Prefer mechanical API updates over broad rewrites.
- Keep source edits as patches applied to the copied kernel tree; do not commit
  dirty submodule contents.
- Do not introduce active-path fake success.
- Keep disabled/unused IP excluded through Kconfig/Makefile if it is not needed
  for openpilot on mici.

Acceptance:

```bash
cd /home/trey/claudes/vamOS
./vamos build kernel
git status --short
```

Conflict notes: split this task by subsystem if multiple workers are available.
Each worker must announce its subsystem before editing.

## 5. Lane KDTS - Device Tree And Hardware Wiring

### Task K3.1 - Audit existing mainline SDM845 camera resources

Repo: vamOS

Dependencies: K1.1

Files:

- `docs/cameras/dts-audit.md`

Work:

- Inspect current `kernel/dts/sdm845-comma-common.dtsi`,
  `sdm845-comma-mici.dts`, and the upstream SDM845 dtsi in `kernel/linux`.
- List existing camera-related disabled nodes, regulator names, GPIOs, clocks,
  power domains, and interconnects.
- Identify register overlaps with mainline `camss` nodes that must be disabled.
- Reference the pinned Qualcomm driver, public Google/downstream Qualcomm camera
  DTS examples, and the legacy AGNOS/openpilot SDM845 kernel in
  `/home/trey/claudes/agnos-builder`.

Acceptance:

```bash
rg -n "cam|cci|csiphy|vfe|ife|camera|mclk|regulator" \
  /home/trey/claudes/vamOS/kernel/dts \
  /home/trey/claudes/vamOS/kernel/linux/arch/arm64/boot/dts/qcom | head -200
git -C /home/trey/claudes/vamOS status --short
```

Status: DONE in `docs/cameras/dts-audit.md`.

Conflict notes: audit can run in parallel; do not edit DTS in this task.

### Task K3.2 - Add Spectra camera DTS nodes

Repo: vamOS

Dependencies: K3.1

Files:

- `kernel/dts/sdm845-comma-common.dtsi`
- `kernel/dts/sdm845-comma-mici.dts`
- maybe `kernel/dts/sdm845-comma-tizi.dts` only if shared includes require it

Work:

- Add a downstream camera root compatible with `qcom,camera_kt` so
  `camera_kt/drivers/camera_main.c` can bind and populate child platform
  devices.
- Keep the active `camera_kt` root directly under `&soc`; commit `c6783a6`
  proved that root-only shape boots on mici. Do not re-add the failed `5b02808`
  child set wholesale; add one child family per flash.
- Add only the `camera_kt` node families consumed by the current openpilot
  branch: cam-req-mgr, cam-sync, SMMU, CPAS, CDM interface/CPAS CDM, CCI,
  CSIPHY0-2, CSID0-2, VFE/IFE0-2, ICP/A5/BPS, and three sensor slots.
- Real CPAS CDM is not accepted yet. The SMMU alias (`f2fc7a2`) and disabled
  hardware CDM node (`3655f26`) both boot; enabling that node with only the
  legacy `"ife"` client (`70e1533`) causes the same no-Linux-output boot stall
  seen in `a26a734`. Commit `dde427c` restores the booting disabled-node state.
  Next CPAS CDM work should fix or instrument the enabled probe/power/reset
  path before adding `ife3` back.
- Add ISP/ICP/BPS as the next active checkpoint, but set
  `qcom,skip-probe-power-domain-cycle` on each power-domain-backed hardware node
  so component bind does not pulse the camera GDSCs during boot.
- Do not add JPEG, LRME, FD, OPE, TFE, SFE, custom, IPE, or fourth-camera nodes
  unless openpilot starts consuming them.
- Keep upstream mainline `camss` and `cci` disabled to avoid register overlap.
- Translate legacy GDSC regulator supplies to mainline CAMCC `power-domains`
  where the recent driver supports genpd.
- Wire only the comma-specific regulators, MCLK/reset/VANA pinctrl states, CCI
  masters, and sensor slots required by the three `ALL_CAMERA_CONFIGS` entries
  in the current openpilot branch, after translating property names for recent
  `camera_kt` (`gpios-shared`, `csiphy-sd-index`, `cci-master`).
- Preserve platform and subdev names when practical.

Acceptance:

```bash
cd /home/trey/claudes/vamOS
./vamos build kernel
dtc -I dtb -O dts \
  kernel/linux/out/arch/arm64/boot/dts/qcom/sdm845-comma-mici.dtb \
  >/tmp/vamos-mici-dtb-dump.dts
/home/trey/.agents/skills/mici/scripts/mdma.py reboot-qdl
./vamos flash kernel
/home/trey/.agents/skills/mici/scripts/mdma.py reboot
/home/trey/.agents/skills/mici/scripts/mdma.py bash --wait 180 --timeout 30 'uname -a'
timeout 30s /home/trey/.agents/skills/mici/scripts/mdma.py profile-boot
git status --short
```

Status: in progress through sensor probe. Kernel #191 builds, flashes, boots,
and reaches powered OS04C10 chip-ID reads for wide/road/driver. Power and pinctrl
are validated 1:1 against the legacy kernel for the active openpilot sensors, but
all three probes still address-NACK after CCI pad activity is observed. See
`docs/cameras/validation/2026-06-14-sensor-nack-rootcause.md`.

Conflict notes: KDTS owns DTS files.

## 6. Lane OUAPI - openpilot UAPI Header Source

### Task O1.1 - Use device-installed recent Spectra UAPI headers

Repo: openpilot

Dependencies: P0.1

Files:

- `system/camerad/cameras/spectra_uapi_version.h`
- no committed `third_party/qcom_spectra_uapi` files

Work:

- Use the recent `<media/cam_*.h>` headers installed on the device by the vamOS
  kernel build from the pinned Qualcomm `camera-driver` submodule.
- Keep the explicit source checkout in vamOS as the provenance record; do not
  add an openpilot UAPI MANIFEST or README.
- Keep any local `third_party/qcom_spectra_uapi/camera_kt_v1_0_3/media/`
  fallback uncommitted and header-only. It is only for off-device object-build
  checks when the host `/usr/include/media` headers are stale.
- Guard the camerad build with compile-time ABI checks in
  `spectra_uapi_version.h`.

Acceptance:

```bash
cd /home/trey/claudes/openpilot
rg -n "CAM_QUERY_CAP_V2|SPECTRA_UAPI_VERSION" system/camerad/cameras/spectra_uapi_version.h
test -z "$(git ls-files third_party/qcom_spectra_uapi)"
test ! -e third_party/qcom_spectra_uapi/camera_kt_v1_0_3/MANIFEST
test ! -e third_party/qcom_spectra_uapi/camera_kt_v1_0_3/README.md
git -C /home/trey/claudes/openpilot status --short
```

Conflict notes: OUAPI owns the userspace ABI guard. The vamOS submodule is the
source provenance; no openpilot manifest/readme is needed.

### Task O1.2 - Keep camerad include path compatible with device headers

Repo: openpilot

Dependencies: O1.1

Files:

- `system/camerad/SConscript`
- possibly build helper files if openpilot uses shared include lists

Work:

- On device, rely on the system recent Spectra UAPI headers installed by vamOS.
- If an off-device fallback include path is used for local compile checks, keep
  it uncommitted and gated so it does not become the production source path.
- Do not globally change unrelated openpilot build targets unless required.

Acceptance:

```bash
cd /home/trey/claudes/openpilot
git diff -- system/camerad/SConscript
scons -n system/camerad 2>/tmp/openpilot-camerad-dryrun.log || true
git status --short
```

Conflict notes: OUAPI owns camerad build include-path changes.

### Task O2.1 - Add ABI guard header

Repo: openpilot

Dependencies: O1.2

Files:

- `system/camerad/cameras/spectra_uapi_version.h`
- small includes in `spectra.cc` or `spectra.h`

Work:

- Add static assertions for recent Qualcomm UAPI layout:
  - `CAM_COMMON_OPCODE_MAX == CAM_COMMON_OPCODE_BASE + 0xa`
  - `CAM_SENSOR_PROBE_CMD == CAM_COMMON_OPCODE_MAX + 1`
  - `sizeof(cam_cmd_i2c_info) == 8`
  - `sizeof(i2c_rdwr_header) == 8`
  - `sizeof(cam_cmd_unconditional_wait) == 8`
  - `sizeof(cam_csiphy_info) == 24`
- Add one function or macro returning a short UAPI version string for logs.

Acceptance:

```bash
cd /home/trey/claudes/openpilot
rg -n "CAM_COMMON_OPCODE_MAX|cam_cmd_i2c_info|spectra_uapi" system/camerad/cameras
scons -j$(nproc) system/camerad
git status --short
```

Conflict notes: keep integration include small; OPACKETS also touches
`spectra.cc`, so coordinate the include location.

## 7. Lane ONODES - openpilot Device Node Discovery

### Task O3.1 - Add sysfs video-node discovery helper

Repo: openpilot

Dependencies: none

Files:

- `system/camerad/cameras/spectra_device_nodes.h`
- `system/camerad/cameras/spectra_device_nodes.cc`
- `system/camerad/SConscript`

Work:

- Implement helper to find `/dev/videoN` by reading
  `/sys/class/video4linux/videoN/name`.
- Support at least `cam-req-mgr` and `cam_sync`.
- Preserve legacy by-path strings as fallback.
- On failure, log every discovered video node and name.

Acceptance:

```bash
cd /home/trey/claudes/openpilot
rg -n "video4linux|cam-req-mgr|cam_sync|by-path" system/camerad/cameras
scons -j$(nproc) system/camerad
git status --short
```

Conflict notes: ONODES owns new node helper files. One integration patch may
touch `spectra.cc` open paths.

### Task O3.2 - Wire node discovery into SpectraMaster

Repo: openpilot

Dependencies: O3.1

Files:

- `system/camerad/cameras/spectra.cc`

Work:

- Replace hardcoded opens for req-mgr and cam_sync with helper calls.
- Keep fallback behavior for legacy paths.
- Add clear logs showing selected path.

Acceptance:

```bash
cd /home/trey/claudes/openpilot
scons -j$(nproc) system/camerad
rg -n "selected.*cam|cam-req-mgr|cam_sync" system/camerad/cameras/spectra.cc
git status --short
```

Conflict notes: coordinate with OPACKETS because both lanes integrate into
`spectra.cc`.

## 8. Lane OPACKETS - openpilot Spectra Layout Migration

### Task O4.1 - Add byte-vector packet builder utilities

Repo: openpilot

Dependencies: O1.2

Files:

- `system/camerad/cameras/spectra_packet_builder.h`
- `system/camerad/cameras/spectra_packet_builder.cc`
- `system/camerad/SConscript`

Work:

- Add utilities to append packed UAPI records to byte buffers using `sizeof`.
- Return offsets and lengths needed by existing `cam_packet` descriptors.
- Avoid old hardcoded packet sizes.

Acceptance:

```bash
cd /home/trey/claudes/openpilot
rg -n "PacketBuilder|append|sizeof" system/camerad/cameras/spectra_packet_builder.*
scons -j$(nproc) system/camerad
git status --short
```

Conflict notes: helper-file-only until O4.4.

### Task O4.2 - Update CSIPHY helpers

Repo: openpilot

Dependencies: O1.2, O2.1

Files:

- `system/camerad/cameras/spectra_csiphy_config.h`
- `system/camerad/cameras/spectra_csiphy_config.cc`
- `system/camerad/SConscript`

Work:

- Add helper functions that fill recent `cam_csiphy_acquire_dev_info` and
  `cam_csiphy_info`.
- Encode the intended DPHY/no-combo mapping.
- Preserve current lane assignment, lane count, settle-time, and data-rate
  semantics.

Acceptance:

```bash
cd /home/trey/claudes/openpilot
rg -n "mipi_flags|lane_assign|csiphy_3phase|cam_csiphy_info" system/camerad/cameras
scons -j$(nproc) system/camerad
git status --short
```

Conflict notes: helper-file-only until O4.4.

### Task O4.3 - Update ISP input helper

Repo: openpilot

Dependencies: O1.2, O2.1

Files:

- `system/camerad/cameras/spectra_isp_config.h`
- `system/camerad/cameras/spectra_isp_config.cc`
- `system/camerad/SConscript`

Work:

- Add helper to fill recent v0 `cam_isp_in_port_info`.
- Do not set old `.custom_csid`.
- Keep current resource IDs, dimensions, dt, format, and output-resource behavior.

Acceptance:

```bash
cd /home/trey/claudes/openpilot
rg -n "cam_isp_in_port_info|custom_csid|CAM_ISP_IFE" system/camerad/cameras
scons -j$(nproc) system/camerad
git status --short
```

Conflict notes: helper-file-only until O4.4.

### Task O4.4 - Integrate packet/config helpers into `spectra.cc`

Repo: openpilot

Dependencies: O4.1, O4.2, O4.3, O2.1

Files:

- `system/camerad/cameras/spectra.cc`
- `system/camerad/cameras/spectra.h` if needed

Work:

- Replace hardcoded sensor power/probe packet sizes with packet builder output.
- Replace direct old-layout CSIPHY struct initialization with helper output.
- Replace direct ISP input struct initialization with helper output.
- Explicitly initialize new req-mgr fields:
  - `additional_timeout = 0`
  - `reserved = 0`
  - all `init_timeout[] = 0`
- Add startup ABI log.

Acceptance:

```bash
cd /home/trey/claudes/openpilot
scons -j$(nproc) system/camerad
rg -n "196|custom_csid|additional_timeout|init_timeout|spectra_uapi" system/camerad/cameras/spectra.cc
git status --short
```

The `rg` command should show no remaining hardcoded sensor power size and no
unconditional `.custom_csid` assignment.

Conflict notes: this is the single shared openpilot integration point. Run after
ONODES integration or coordinate manually.

## 9. Lane VALIDATION - Build And Device Gates

### Task V1.1 - Add kernel build validation script

Repo: vamOS

Dependencies: K1.3

Files:

- `tools/camera/validate_spectra_kernel_build.sh`

Work:

- Script a kernel build.
- Print current vamOS commit, kernel submodule commit, Spectra source submodule
  commit, and output boot image path.
- Fail if `CONFIG_SPECTRA` or chosen config symbol is missing from final config.

Acceptance:

```bash
cd /home/trey/claudes/vamOS
bash -n tools/camera/validate_spectra_kernel_build.sh
git status --short
```

Conflict notes: VALIDATION owns `tools/camera`.

### Task V1.2 - Add device node dump script

Repo: vamOS

Dependencies: none

Files:

- `tools/camera/dump_camera_nodes.sh`

Work:

- SSH to `comma@10.0.0.22`.
- Dump:
  - `uname -a`
  - `/dev/v4l/by-path`
  - `/sys/class/video4linux/*/name`
  - `/sys/class/video4linux/v4l-subdev*/name`
  - relevant `dmesg` camera lines

Acceptance:

```bash
cd /home/trey/claudes/vamOS
bash -n tools/camera/dump_camera_nodes.sh
git status --short
```

Conflict notes: VALIDATION owns this script.

### Task V2.1 - Sensor probe validation

Repo: openpilot

Dependencies: O2.1, O3.2, O4.4, kernel Gate 3

Files:

- `system/camerad/snapshot_standalone.cc` if present or a small existing camera
  debug path
- validation notes under `docs/cameras/validation/` in vamOS if results are
  committed

Work:

- Run a standalone openpilot camera bring-up path that reaches
  `CAM_SENSOR_PROBE_CMD`.
- Record each sensor slot, slave address, expected chip ID, ioctl opcode, struct
  sizes, and return code.
- If it fails, determine whether failure is:
  - wrong UAPI/header
  - missing node
  - sensor power/reset/mclk
  - CCI NACK/timeout
  - driver parse error

Acceptance:

```bash
ssh comma@10.0.0.22 "tmux capture-pane -t comma:0.0 -p -S -200" || true
ssh comma@10.0.0.22 "dmesg | grep -Ei 'cam|cci|sensor|csiphy|vfe|ife' | tail -200"
git -C /home/trey/claudes/openpilot status --short
```

Conflict notes: requires device access; coordinate with any worker flashing the
kernel.

### Task V3.1 - Frame streaming validation

Repo: openpilot and vamOS

Dependencies: V2.1 passes for at least one sensor

Files:

- validation note under `docs/cameras/validation/`

Work:

- Start with one camera and one RDI/IFE path.
- Validate buffer allocation, SMMU mapping, req-mgr schedule, SOF events, and
  frame completion.
- Then run full `snapshot.py`.
- Finally run `camerad` and confirm three cameras stream.

Acceptance:

```bash
ssh comma@10.0.0.22 "cd /data/openpilot && source /usr/local/venv/bin/activate && python3 system/camerad/snapshot.py"
ssh comma@10.0.0.22 "dmesg | grep -Ei 'cam|smmu|fault|cci|sensor|vfe|ife|icp|bps' | tail -300"
git -C /home/trey/claudes/openpilot status --short
git -C /home/trey/claudes/vamOS status --short
```

Conflict notes: device access serialized.

## 10. Integration Gates

Gate A - Planning landed

- These two docs are committed on `spectra-uapi-migration-plan`.
- No old docs or old branch WIP are present.

Gate B - Source pinned

- P0.1 and K1.1 complete.
- K1.2 copies the same submodule UAPI headers into the kernel UAPI include tree.
- O1.1 guard checks compile against the recent `camera_kt` UAPI.
- No openpilot UAPI manifest or README is required; the explicit vamOS submodule
  checkout is the source provenance.

Gate C - Buildable kernel

- K1.2, K1.3, K2.3 complete.
- `./vamos build kernel` completes.

Gate D - Buildable openpilot

- O1.2, O2.1, O3.2, O4.4 complete.
- `scons -j$(nproc) system/camerad` completes.

Gate E - Boot nodes

- Kernel flashes and boots on mici.
- req-mgr, cam_sync, cam-sensor, cam-csiphy, cam-isp, and cam-icp nodes are
  discoverable.

Gate F - Sensor probe

- All three openpilot cameras pass chip-ID probe.

Gate G - Streaming

- `snapshot.py` captures real images.
- `camerad` streams all three cameras with clean dmesg.

## 11. Interface Notes

Add notes here when a lane changes a contract another lane uses.

- Initial contract: UAPI headers must expose `<media/cam_*.h>`.
- Initial contract: openpilot should keep v1 req-mgr link/map/alloc ioctls until
  runtime evidence requires v2.
- Initial contract: kernel should preserve node names where possible, but
  openpilot must not require exact legacy by-path strings.
