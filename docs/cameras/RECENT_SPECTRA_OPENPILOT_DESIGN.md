# Recent Qualcomm Spectra Driver + openpilot Design Plan

This is the build plan for replacing the old AGNOS-derived Spectra port with a
recent Qualcomm Spectra camera driver while keeping openpilot's camera
architecture native. It is intentionally scoped to this clean mainline branch;
do not carry old branch docs, proof bundles, or WIP patches into this branch.

Companion task plan: [RECENT_SPECTRA_OPENPILOT_TASKS.md](./RECENT_SPECTRA_OPENPILOT_TASKS.md).

## 1. Goal

Make openpilot stream all comma four / mici cameras on vamOS mainline using a
recent Qualcomm downstream Spectra driver.

Success means:

- vamOS boots a mainline kernel with the recent Qualcomm Spectra stack built in.
- The driver exposes the Qualcomm `cam-*` ABI openpilot already uses:
  `cam_req_mgr`, `cam_sync`, `cam_isp`, `cam_icp`, `cam_sensor`, `cam_csiphy`,
  and the required camera subdev nodes.
- openpilot camerad keeps its existing Spectra flow. It is rebuilt against the
  matching UAPI and gets small compatibility fixes, not a camera subsystem
  rewrite.
- `snapshot.py` and `camerad` can bring up road, wide, and driver cameras and
  produce real frames.

## 2. Non-goals

- Do not port openpilot to mainline `qcom-camss`. Mainline `qcom-camss` exposes
  a V4L2 media graph and raw/RDI capture path, not the Qualcomm downstream
  request-manager and ISP/BPS ABI openpilot uses.
- Do not build an Android camera HAL or userspace camera provider.
- Do not preserve old AGNOS UAPI compatibility in the new driver path. The new
  driver and openpilot must agree on one exact UAPI version.
- Do not import old branch documentation or old branch camera proof artifacts
  into this branch. Re-measure facts or cite external source commits when needed.
- Do not spend time on secure camera, flash LED, OIS, EEPROM, presil, or unused
  camera IP unless a build or runtime dependency proves they are on the mici
  path.

## 3. Source Selection

Use Qualcomm's public camera driver repository:

- Repository: `https://github.com/qualcomm-linux/camera-driver`
- Branch/tag family: `camera-kernel.qclinux.0.0` / `v1.0.3`
- Verified release state on 2026-06-14: `v1.0.3`, released 2026-06-04.
- Verified commit: `56b463cba50c1db1f2cc53ddd8790730f14bd8a8`

Use the `camera_kt/` subtree, not sibling `camera/`.

Why `camera_kt/`:

- It contains old CSIPHY support, including `cam_csiphy_1_0_hwreg.h`.
- It recognizes `qcom,csiphy-v1.0`, which SDM845/mici needs.
- It contains VFE 170 support through compatibles such as `qcom,vfe170` and
  `qcom,vfe170_150`.
- The sibling `camera/` subtree targets newer camera generations and is a worse
  match for SDM845.

Submodule setup for workers:

```bash
git -C /home/trey/claudes/vamOS submodule update --init --depth 1 \
  kernel/spectra-qcom/camera-driver
git -C /home/trey/claudes/vamOS/kernel/spectra-qcom/camera-driver \
  log -1 --format='%H %cd %s' --date=short
```

## 4. Repositories And Ownership

vamOS repo:

- Root: `/home/trey/claudes/vamOS`
- Branch: `spectra-uapi-migration-plan`
- Owns kernel import, Kconfig/Makefile glue, DTS, firmware/build packaging, and
  kernel-side validation.

openpilot repo:

- Root: `/home/trey/claudes/openpilot`
- Branch: `spectra-uapi-migration-plan`
- Owns camerad UAPI headers, Spectra packet/layout fixes, device-node discovery,
  and userspace validation.

Do not edit both repos in one commit. Keep kernel and userspace commits separate
so bisecting ABI changes is possible.

## 5. High-Level Architecture

The target architecture has two matched halves.

Kernel half:

- Pin Qualcomm's camera driver repository as a vamOS git submodule.
- Use the submodule's `camera_kt` subtree as the source input for the kernel
  build.
- Build it into the kernel image, not as a rootfs-loaded module.
- Install the driver's UAPI headers into the kernel build include path as
  `<media/cam_*.h>`.
- Preserve device names and subdev names openpilot expects, or document the new
  names and update openpilot discovery.

Userspace half:

- Vendor the exact same Qualcomm `camera_kt` UAPI headers into openpilot's
  camerad build.
- Add a small openpilot compatibility layer around changed Spectra layouts.
- Keep the existing `CAM_REQ_MGR` session/link/config/schedule flow.
- Keep the existing IFE/BPS/CDM pipeline unless runtime testing proves a specific
  blob needs adjustment.

The key rule: kernel and userspace must be built from the same Spectra UAPI
snapshot. Do not mix AGNOS `cam_*.h` headers with the recent Qualcomm driver.

## 6. Proposed vamOS Layout

Add a new import path rather than mutating unrelated kernel directories directly:

```text
kernel/spectra-qcom/
  camera-driver/        # git submodule, pinned to 56b463c...
    camera_kt/
      drivers/...
      include/uapi/camera/media/cam_*.h
  patches/
    *.patch             # local porting deltas applied after build-time copy
  compat/
    README.md
    *.h, *.c for mainline-only compatibility shims
```

Build-time install copies only what the kernel needs:

```text
kernel/spectra-qcom/camera-driver/camera_kt/drivers/
  -> kernel/linux/drivers/media/platform/msm/camera/

kernel/spectra-qcom/camera-driver/camera_kt/include/uapi/camera/media/cam_*.h
  -> kernel/linux/include/uapi/media/cam_*.h
```

The copy to `include/uapi/media` is deliberate. Qualcomm's headers include each
other as `<media/cam_defs.h>`, and openpilot already includes `<media/cam_*.h>`.

Build glue should be tiny:

- `tools/build/build_kernel.sh`: add `install_spectra_qcom()`, local patch
  application, and cleanup logic.
- `kernel/patches/NNNN-spectra-qcom-link.patch`: link the copied camera directory
  into `drivers/media/platform/msm` Kconfig/Makefile.
- `kernel/configs/vamos.config`: enable the camera/media dependencies and the
  new Spectra config symbol.

## 7. Kernel Porting Strategy

Start from Qualcomm's recent code and port it forward/backward only where vamOS
mainline requires it. Keep the upstream submodule pristine; represent vamOS
source edits as local patches applied to the copied kernel tree. Do not reuse
the old AGNOS 4.9 port as source code.

Expected kernel work buckets:

- Kbuild integration: include paths, module/built-in selection, Kconfig symbols.
- Core Linux API drift: V4L2/media registration, debugfs, timers, clocks,
  regulators, pinctrl, runtime PM, DMA/IOMMU helpers.
- Qualcomm glue: CPAS, SMMU, interconnect, SCM, socinfo, and any downstream-only
  helper APIs still referenced by `camera_kt`.
- DTS: SDM845 camera HW blocks, regulator supplies, mclk pinctrl, CCI, CSIPHY,
  CSID/VFE/IFE, ICP/BPS, JPEG/FD if required for node creation.
- Runtime naming: video node names, subdev names, and by-path symlinks.

Prefer compatibility wrappers local to `kernel/spectra-qcom/compat/` for
mainline-only API gaps. Do not scatter broad fake APIs through the kernel tree.

Stub policy:

- A stub is allowed only to unblock build or boot for an unused mici path.
- Every stub must be listed in `kernel/spectra-qcom/compat/README.md`.
- Stubs on the active camera path are not acceptable for a streaming milestone.
- Secure camera, flash, OIS, EEPROM, presil, and unused camera IP can be stubbed
  only after confirming openpilot does not call that path.

## 8. DTS And Hardware Contract

mici uses SDM845 camera hardware. The driver must bind the following functional
blocks or a documented subset sufficient for openpilot:

- CPAS / camera top
- CDM
- CCI
- Four CSIPHY nodes
- CSID / VFE / IFE path for road, wide, and driver cameras
- ICP/BPS path used by openpilot for YUV output
- Four sensor slots, with three actively used by openpilot
- Required camera regulators and mclk pinctrl states

Known device-level expectations:

- Sensors are OS04C10 and OX03C10 family parts.
- openpilot probes sensor chip IDs over CCI at runtime, not through a rich
  upstream V4L2 sensor driver.
- Road/wide/driver camera bring-up must pass sensor probe before any ISP work is
  meaningful.

The DTS worker must preserve platform names when practical:

- `cam-req-mgr`
- `cam_sync`
- `cam-isp`
- `cam-icp`
- `cam-sensor-driver`
- `cam-csiphy-driver`

If exact by-path symlinks differ on mainline, userspace should discover nodes by
`/sys/class/video4linux/*/name` and subdev names rather than relying only on
legacy `/dev/v4l/by-path` strings.

## 9. openpilot UAPI Migration

openpilot currently includes Qualcomm Spectra headers as `<media/cam_*.h>` from
the system or build environment. That is unsafe with a new driver because the
recent Qualcomm UAPI changed opcodes and struct layouts.

Add a vendored UAPI tree in openpilot, for example:

```text
third_party/qcom_spectra_uapi/camera_kt_v1_0_3/media/cam_*.h
third_party/qcom_spectra_uapi/camera_kt_v1_0_3/MANIFEST
```

Then update `system/camerad/SConscript` or the relevant openpilot build config so
camerad includes this directory before system headers.

Add a small version/guard header:

```text
system/camerad/cameras/spectra_uapi_version.h
```

Required compile-time checks:

```c++
static_assert(CAM_COMMON_OPCODE_MAX == CAM_COMMON_OPCODE_BASE + 0xa);
static_assert(CAM_SENSOR_PROBE_CMD == CAM_COMMON_OPCODE_MAX + 1);
static_assert(sizeof(cam_cmd_i2c_info) == 8);
static_assert(sizeof(i2c_rdwr_header) == 8);
static_assert(sizeof(cam_cmd_unconditional_wait) == 8);
static_assert(sizeof(cam_csiphy_info) == 24);
```

These checks deliberately fail if camerad is accidentally built against old
AGNOS headers.

## 10. Required openpilot Code Changes

Keep changes focused in and around:

- `system/camerad/cameras/spectra.cc`
- `system/camerad/cameras/spectra.h`
- `system/camerad/cameras/hw.h`
- `system/camerad/sensors/sensor.h`
- `system/camerad/SConscript`
- New small helper files under `system/camerad/cameras/`

Required fixes:

1. Header source

   Ensure camerad uses vendored Qualcomm `camera_kt` UAPI headers. This fixes
   opcode drift and exact struct-size validation.

2. CSIPHY config

   Update `configCSIPHY()` to use the recent layout:

   - `cam_csiphy_acquire_dev_info.combo_mode = 0`
   - `cphy_dphy_combo_mode = 0`
   - `csiphy_3phase = 0`
   - `mux_mode = 0`
   - `cam_csiphy_info.reserved = 0`
   - `lane_assign = 0x3210`
   - `mipi_flags = 0` for the old DPHY/no-combo path unless driver testing proves
     another flag is needed
   - `lane_cnt = 4`
   - `secure_mode = 0`
   - preserve current settle-time and data-rate behavior

3. ISP input config

   Remove or conditionalize old `.custom_csid = 0x0`. Recent `camera_kt` v0
   `cam_isp_in_port_info` does not have that field. Stay on the v0 acquire path
   initially; the old IFE resource IDs openpilot uses still exist.

4. Sensor probe and power packets

   Replace hardcoded packet byte sizes with computed packet builders. The recent
   `cam_cmd_power`, `cam_cmd_i2c_info`, `i2c_rdwr_header`, and wait-command
   layouts differ from the old AGNOS layouts.

   Packet builders should allocate a byte vector, append typed records using
   `sizeof`, and return both byte length and typed offsets. Avoid pointer math
   that assumes old struct sizes.

5. Req-mgr structs

   Recompile against new headers and explicitly initialize new fields:

   - `cam_req_mgr_sched_request.additional_timeout = 0`
   - `cam_req_mgr_sched_request.reserved = 0`
   - `cam_req_mgr_link_control.init_timeout[...] = 0`

   Keep v1 `CAM_REQ_MGR_LINK`, `CAM_REQ_MGR_ALLOC_BUF`, and `CAM_REQ_MGR_MAP_BUF`
   at first because the recent driver still supports them.

6. Device-node discovery

   Prefer discovery by sysfs name over hardcoded by-path:

   - req-mgr video node: find `/sys/class/video4linux/video*/name == cam-req-mgr`
   - sync video node: find name `cam_sync`
   - subdevs: continue using existing subdev name matching, but make failure logs
     dump all available names.

7. Runtime ABI logging

   Add one startup log line that prints:

   - `CAM_COMMON_OPCODE_MAX`
   - selected sizes for req-mgr, sensor, CSIPHY, ISP structs
   - detected req-mgr and cam_sync node paths

   This makes header/driver mismatches obvious in device logs.

## 11. ABI Drift That Must Be Handled

The following drift is known and must be planned for:

- `CAM_QUERY_CAP_V2` was added.
- `CAM_COMMON_OPCODE_MAX` is now `CAM_COMMON_OPCODE_BASE + 0xa`.
- `CAM_REQ_MGR_*` opcodes shift relative to old AGNOS headers.
- `CAM_SENSOR_PROBE_CMD` shifts because it is based on `CAM_COMMON_OPCODE_MAX`.
- `cam_req_mgr_sched_request` gained `additional_timeout` and `reserved`.
- `cam_req_mgr_link_control` gained `init_timeout[MAX_LINKS_PER_SESSION]`.
- `cam_cmd_i2c_info` is now 8 bytes.
- `cam_cmd_power` has a wider `count` plus extra reserved fields and a flex-array
  style payload.
- `i2c_rdwr_header.count` is now 32-bit and shifts `op_code`.
- `cam_cmd_unconditional_wait` is now 8 bytes.
- `cam_csiphy_info` replaced old lane/combo fields with `reserved`,
  `lane_assign`, and `mipi_flags`.
- `cam_csiphy_acquire_dev_info` gained explicit combo/phase/mux fields.
- `cam_isp_in_port_info` v0 does not carry openpilot's old local `custom_csid`
  member.

Do not paper over this with numeric constants in openpilot. Use matching headers.

## 12. Validation Strategy

Validation must advance through gates. Do not start debugging full camerad until
the lower gates pass.

Gate 1: source pin

- Qualcomm camera-driver submodule is committed and pinned to the selected
  commit.
- Header copy paths are deterministic.
- Kernel build reaches `camera_kt` compilation.

Gate 2: kernel compile

- `./vamos build kernel` completes.
- `CONFIG_SPECTRA_CAMERA` or the chosen equivalent is enabled.
- No active-path stubs remain undocumented.

Gate 3: boot and node creation

- mici boots the new boot image.
- `dmesg` shows camera platform devices probing.
- `/sys/class/video4linux/*/name` contains req-mgr and cam_sync.
- camera subdev names are present.

Gate 4: sensor probe

- openpilot or a standalone probe tool issues `CAM_SENSOR_PROBE_CMD`.
- road, wide, and driver sensor slots return expected chip IDs.
- CCI logs do not show persistent NACK, timeout, or FIFO-empty errors.

Gate 5: RDI/IFE frame

- A single camera streams a raw/RDI or IFE path frame.
- Buffers map through cam_smmu and req-mgr without faults.

Gate 6: full openpilot camera path

- `snapshot.py` captures real road/wide/driver images.
- `camerad` streams incrementing frame IDs with hardware timestamps.
- `dmesg` remains clean under repeated start/stop.

## 13. Parallelization Model

Use independent lanes with explicit interfaces:

- Lane KIMPORT: source submodule contract, build copy, local patch application,
  Kconfig/Makefile.
- Lane KPORT: compile fixes and compatibility wrappers.
- Lane KDTS: SDM845/mici DTS and regulator/pinctrl mapping.
- Lane ONODES: openpilot node discovery and diagnostics.
- Lane OUAPI: openpilot vendored UAPI and compile-time ABI checks.
- Lane OPACKETS: openpilot CSIPHY/ISP/sensor packet builders.
- Lane VALIDATION: scripts for build, node dump, ioctl trace, and on-device gates.

Parallel work rules:

- One worker owns a file at a time. If a lane needs a shared file, it first adds a
  helper file and a small integration patch.
- Avoid multiple workers editing `spectra.cc` directly. Put new logic in helper
  files and make one integration worker wire them in.
- Kernel and openpilot changes should merge only at gate boundaries.
- Every lane must leave a short note in the task file when it changes an
  interface another lane depends on.

## 14. Risks And Mitigations

Risk: recent Qualcomm driver still depends on Qualcomm kernel infrastructure not
present in vamOS mainline.

Mitigation: local compatibility wrappers, documented stubs only for unused paths,
and early compile-gate isolation.

Risk: UAPI headers accidentally come from the OS instead of the vendored tree.

Mitigation: include-path order, compile-time static assertions, startup ABI log.

Risk: device node paths differ from AGNOS.

Mitigation: sysfs name discovery in openpilot rather than fragile by-path strings.

Risk: sensor probe still fails after driver migration.

Mitigation: treat sensor probe as its own gate. Compare CCI transactions, power
sequence timing, reset GPIOs, mclk, regulators, and slave addresses before touching
ISP/BPS.

Risk: parallel workers conflict in `spectra.cc`.

Mitigation: helper-file-first rule and one final openpilot integration worker.

## 15. Definition Of Done

The project is done when:

- The Qualcomm camera-driver submodule identifies the exact imported driver
  commit.
- vamOS builds and boots with the recent Spectra driver enabled.
- openpilot builds camerad against the matching vendored UAPI.
- req-mgr, sync, sensor, CSIPHY, ISP, ICP/BPS flows complete without ABI size or
  opcode errors.
- road, wide, and driver cameras stream in openpilot.
- A clean validation note records the exact vamOS commit, openpilot commit,
  driver source commit, device used, and commands run.
