# Recent Qualcomm Spectra Driver + openpilot Task Plan

Design doc: [RECENT_SPECTRA_OPENPILOT_DESIGN.md](./RECENT_SPECTRA_OPENPILOT_DESIGN.md).

This task plan is written for parallel Codex workers. Each task has an owner
lane, repo, dependencies, files, acceptance checks, and conflict notes. Start
from clean branches named `spectra-uapi-migration-plan` in both repos.

## 0a. Progress Log (read this first)

Last updated: 2026-06-14.

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

- `third_party/qcom_spectra_uapi/camera_kt_v1_0_3/` — vendored v1.0.3 UAPI
  headers + MANIFEST (task O1.1's output). DECISION (2026-06-14, user): the real
  plan sources headers from `/usr` on the device (installed by the vamOS kernel
  build, lane K1.2), NOT from a vendored openpilot tree. So O1.1/O1.2 as
  originally written are superseded. The vendored copy is kept locally only as
  an off-device compile crutch and source-of-record.
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
- Latest flashed/tested kernel had Spectra built in and booted on mici, but no
  camera device nodes appeared because DT has no `qcom,camera_kt` root yet.
  Dmesg reported `No matching device found for camera_kt driver = -19`.
- K3.1 fresh DT audit is recorded in `docs/cameras/dts-audit.md`.
- K3.2 is now in progress. First-pass DT root plumbing adds `qcom,camera_kt`,
  `qcom,cam-req-mgr`, and `qcom,cam-sync`; `./vamos build kernel` succeeds and
  the generated mici DTB contains those nodes. The remaining K3.2 work is the
  downstream hardware topology without importing the old branch's device tree
  changes.

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
- Add the compiled `camera_kt` node families: cam-req-mgr, cam-sync, SMMU,
  CPAS, CDM interface/CDM, CCI, CSIPHY, CSID/VFE/IFE, ICP/A5/IPE/BPS, JPEG,
  LRME, and four sensor slots.
- Do not add FD, OPE, TFE, SFE, or custom camera nodes unless their build
  configs are enabled.
- Keep upstream mainline `camss` and `cci` disabled to avoid register overlap.
- Translate legacy GDSC regulator supplies to mainline CAMCC `power-domains`
  where the recent driver supports genpd.
- Wire comma-specific regulators, MCLK/reset/VANA pinctrl states, CCI masters,
  and sensor slots from the legacy AGNOS/openpilot tree after translating
  property names for recent `camera_kt` (`gpios-shared`, `csiphy-sd-index`,
  `cci-master`).
- Preserve platform and subdev names when practical.

Acceptance:

```bash
cd /home/trey/claudes/vamOS
./vamos build kernel
dtc -I dtb -O dts \
  kernel/linux/out/arch/arm64/boot/dts/qcom/sdm845-comma-mici.dtb \
  >/tmp/vamos-mici-dtb-dump.dts
git status --short
```

Conflict notes: KDTS owns DTS files.

## 6. Lane OUAPI - openpilot UAPI Header Source

### Task O1.1 - Vendor recent Spectra UAPI headers

Repo: openpilot

Dependencies: P0.1

Files:

- `third_party/qcom_spectra_uapi/camera_kt_v1_0_3/media/cam_*.h`
- `third_party/qcom_spectra_uapi/camera_kt_v1_0_3/MANIFEST`

Work:

- Copy `camera_kt/include/uapi/camera/media/cam_*.h` from the verified
  Qualcomm source submodule at
  `/home/trey/claudes/vamOS/kernel/spectra-qcom/camera-driver/`.
- Store them under a path that exposes includes as `<media/cam_*.h>`.
- Add a manifest with upstream commit and import date.

Acceptance:

```bash
test -f /home/trey/claudes/openpilot/third_party/qcom_spectra_uapi/camera_kt_v1_0_3/media/cam_defs.h
rg -n "CAM_QUERY_CAP_V2|CAM_COMMON_OPCODE_MAX" \
  /home/trey/claudes/openpilot/third_party/qcom_spectra_uapi/camera_kt_v1_0_3/media/cam_defs.h
git -C /home/trey/claudes/openpilot status --short
```

Conflict notes: OUAPI owns this third-party UAPI directory.

### Task O1.2 - Put vendored UAPI first in camerad include path

Repo: openpilot

Dependencies: O1.1

Files:

- `system/camerad/SConscript`
- possibly build helper files if openpilot uses shared include lists

Work:

- Add the vendored UAPI include root before system include paths for camerad.
- Do not globally change unrelated openpilot build targets unless required.

Acceptance:

```bash
cd /home/trey/claudes/openpilot
rg -n "qcom_spectra_uapi|camera_kt_v1_0_3" system/camerad/SConscript
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
- O1.1 complete.
- The vamOS submodule gitlink and openpilot UAPI manifest name the same
  Qualcomm commit.

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
