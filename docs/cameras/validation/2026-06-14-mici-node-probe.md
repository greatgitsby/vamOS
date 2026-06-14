# mici camera node probe — 2026-06-14

Gate E (boot + node creation) probe of the recent `camera_kt` Spectra stack on
the vamOS mainline kernel. Probed over the MDMA serial link (mici was offline
on the network; SSH "no route to host").

## Versions

- Device kernel: `6.18.0-vamos-20244cc #53 SMP PREEMPT Sun Jun 14 15:58:45 UTC 2026`
- vamOS commit: `2b3148e`
- kernel/linux submodule: `7d0a66e4b`
- spectra source submodule: `56b463c` (`camera_kt` v1.0.3)
- openpilot branch: `spectra-uapi-migration-plan` @ `cf59e9767`

## Result: FAIL — driver built in, but no device to bind, no nodes created

- `CONFIG_SPECTRA_CAMERA=y`, `CONFIG_MEDIA_SUPPORT=y`, `CONFIG_VIDEO_DEV=y`
  (driver and V4L2 core are compiled into the kernel).
- dmesg, the only camera line at boot:

  ```
  [    0.318410] CAM_INFO: CAM-UTIL: camera_init: 367: No matching device found for camera_kt driver = -19
  ```

  `-19` = `-ENODEV`. The driver registered but found no matching device-tree
  nodes to bind to.
- `/sys/class/video4linux/` is empty.
- No `/dev/video*`, no `/dev/v4l-subdev*`, no `/dev/v4l/by-path/` entries.
- No camera platform devices bound (`/sys/bus/platform/devices` has no
  cam/cci/csiphy/cpas/vfe/ife/icp).
- Live device tree has NO camera nodes: no `cam`/`cci`/`csiphy`/`cpas`/`vfe`/`ife`
  nodes and no `qcom,cam*`/`qcom,csiphy`/`qcom,cci`/`qcom,vfe` compatibles. Only
  the reserved-memory region exists:
  `OF: reserved mem: 0x8bf00000..0x8c3fffff (5120 KiB) camera-mem@8bf00000`.

## Diagnosis

This is a KERNEL-SIDE blocker, not openpilot. The `camera_kt` driver has nothing
to bind to because the SDM845/mici camera hardware blocks (CPAS, CDM, CCI, four
CSIPHY, CSID/VFE/IFE, ICP/BPS, sensor slots) are not declared in the DTS with the
compatibles the driver matches.

## Blocking task

Lane KDTS, task K3.2 — "Add Spectra camera DTS nodes" — is not done. Until the
DTS declares the camera blocks, no V4L nodes appear, so openpilot Gates F
(sensor probe) and G (streaming) cannot be reached. openpilot's userspace
migration (UAPI guard, node discovery, packet sizing) is ready and waiting on
these nodes; the new sysfs node-discovery helper will dump available nodes when
it can't find req-mgr/cam_sync, which will help once nodes start appearing.

## Next steps (kernel)

1. K3.1/K3.2: add CPAS/CDM/CCI/CSIPHY/CSID-VFE-IFE/ICP-BPS + sensor-slot nodes to
   `kernel/dts/sdm845-comma-common.dtsi` / `sdm845-comma-mici.dts` with the
   compatibles `camera_kt` matches (`qcom,csiphy-v1.0`, `qcom,vfe170`, CCI, CPAS,
   etc. per the design doc), wire regulators + mclk pinctrl.
3. Rebuild + flash, re-probe: expect `camera_kt` to bind and create
   `cam-req-mgr` / `cam_sync` / `cam-isp` / `cam-icp` / `cam-sensor` /
   `cam-csiphy` nodes, then re-run this probe.
