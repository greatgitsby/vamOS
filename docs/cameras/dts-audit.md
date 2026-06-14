# Spectra SDM845 Device Tree Audit

Date: 2026-06-14

This audit is for the recent Qualcomm `camera_kt` driver pinned under
`kernel/spectra-qcom/camera-driver`. It intentionally does not use the old
vamOS branch DTS changes.

## Sources

- Qualcomm camera driver repo: https://github.com/qualcomm-linux/camera-driver
- Qualcomm `camera_kt` root probe: `kernel/spectra-qcom/camera-driver/camera_kt/drivers/camera_main.c`
- Google SDM845 downstream camera DTS:
  https://android.googlesource.com/kernel/msm/+/1fb11298840bd9d4225abc93b512f7971a9c620f/arch/arm64/boot/dts/qcom/sdm845-camera.dtsi
- Google SDM845 v2 downstream camera DTS:
  https://android.googlesource.com/kernel/msm/+/1fb11298840bd9d4225abc93b512f7971a9c620f/arch/arm64/boot/dts/qcom/sdm845-v2-camera.dtsi
- Google/Motorola newer downstream camera topology example:
  https://android.googlesource.com/kernel/msm-extra/camera-devicetree/+/14fc67c2f5396c0e7c504535d4ebd3f48b9ef9da/kona-camera.dtsi
- Qualcomm camera resource manager docs:
  https://docs.qualcomm.com/bundle/publicresource/topics/80-88500-1/72_Camera_resource_manager_configuration.html
- Mainline CAMSS docs, for contrast:
  https://docs.kernel.org/admin-guide/media/qcom_camss.html
- Legacy openpilot/comma kernel reference:
  `/home/trey/claudes/agnos-builder/agnos-kernel-sdm845/arch/arm64/boot/dts/qcom/`

## Driver Contract

The built driver is not the mainline `qcom,sdm845-camss` media graph driver.
It is Qualcomm's downstream platform-device stack.

`camera_kt/drivers/camera_main.c` matches only a root DT node compatible with
`qcom,camera_kt`. Without that node the boot log is expected to say:

```text
No matching device found for camera_kt driver = -19
```

After the root probe succeeds, `cam_main_probe()` initializes all compiled
submodule drivers and calls `of_platform_populate()` on the camera root. The
child topology therefore needs normal platform nodes using the compatible
strings consumed by `camera_kt`.

The current `qcm6490-camera.mk` build enables:

- base: request manager, sync, SMMU, CPAS, CDM interface, CDM hardware
- sensor: resource manager, CCI, CSIPHY, sensor, actuator, EEPROM, OIS, bridge,
  IR LED, and optional LED flash if its kernel symbols are reachable
- ISP: CSID 17x, CSID-lite 17x, VFE 17x, top TPG, ISP subdev
- ICP: A5, IPE, BPS, ICP subdev
- JPEG
- LRME

It does not enable FD, OPE, TFE, SFE, or custom camera blocks. Do not add those
nodes for the first bring-up unless the build config changes.

The current checked-out openpilot branch is narrower than the compiled kernel
surface. It opens `cam-req-mgr`, `cam_sync`, `cam-isp`, `cam-icp`, three
`cam-sensor-driver` indices, and three `cam-csiphy-driver` indices. Wide and
road use IFE output; driver uses BPS through ICP. Do not add JPEG, LRME, IPE,
FD, OPE, TFE, SFE, custom camera blocks, or a fourth camera slot unless
openpilot starts consuming them.

## Current vamOS SDM845 Baseline

`kernel/linux/arch/arm64/boot/dts/qcom/sdm845.dtsi` already contains mainline
camera resources:

- `camera_mem: camera-mem@8bf00000`
- disabled `camss: camss@acb3000`, compatible `qcom,sdm845-camss`
- disabled `cci: cci@ac4a000`, compatible `qcom,sdm845-cci`
- `clock_camcc: clock-controller@ad00000`
- CCI pinctrl states on GPIO17/18 and GPIO19/20

Those mainline nodes overlap Spectra hardware registers. Keep `camss` and the
mainline `cci` disabled while adding downstream Spectra-compatible nodes.

Mainline CAMCC exposes the SDM845 camera GDSCs as power domains:

- `BPS_GDSC`
- `IPE_0_GDSC`
- `IPE_1_GDSC`
- `IFE_0_GDSC`
- `IFE_1_GDSC`
- `TITAN_TOP_GDSC`

The recent `camera_kt` common SOC code supports `power-domains`, so translate
legacy fake GDSC regulator supplies such as `camss-vdd-supply = <&titan_top_gdsc>`
or `gdscr-supply = <&titan_top_gdsc>` into mainline
`power-domains = <&clock_camcc TITAN_TOP_GDSC>` where possible.

## Legacy openpilot/comma SDM845 Reference

The legacy AGNOS include chain for mici pulls in:

- `sdm845-camera.dtsi`: base downstream Qualcomm SDM845 camera topology
- `sdm845-v2-camera.dtsi`: v2 CSIPHY, SMMU, CPAS, LRME overrides
- `sdm845-camera-sensor-mtp.dtsi`: comma camera regulator and sensor slots
- `comma_mici.dts`: mici board-specific MCLK drive strength reductions

Important comma sensor mapping from `sdm845-camera-sensor-mtp.dtsi`:

| Sensor slot | CSIPHY | CCI master | MCLK GPIO | Reset GPIO | Notes |
| --- | --- | --- | --- | --- | --- |
| 0 | 0 | 0 | TLMM 13 | TLMM 9 | VANA on TLMM 8, rear DVDD GPIO regulator |
| 1 | 1 | 0 | TLMM 14 | TLMM 7 | VANA on TLMM 8, rear DVDD GPIO regulator |
| 2 | 2 | 1 | TLMM 15 | TLMM 12 | VANA on TLMM 8, rear DVDD GPIO regulator |
| 3 | 3 | 1 | TLMM 16 | TLMM 9 | dedicated VANA/VDIG fixed regulators |

Legacy AGNOS also reduces active MCLK0/1/2 drive strength to 2 mA on mici.
Mainline SDM845 pinctrl supports the same `cam_mclk` function on GPIO13-16,
but vamOS does not yet define the camera MCLK/reset/VANA pinctrl states.

## Regression Finding

Commit `5b02808` tested an active `qcom,camera_kt` node directly under `&soc`
with only `qcom,cam-req-mgr` and `qcom,cam-sync` children. The kernel image
built and the generated mici DTB contained the nodes, but the device boot-looped
before Linux printed anything. MDMA `profile-boot` reached ABL `Exit BS`; about
58 seconds later the boot ROM banner appeared again. Commit `f9ca57e` removed
that DTS node and restored boot.

Commit `c6783a6` then tested only the direct active root-under-`&soc` node:
`camera-kt@ac00000` with `compatible = "qcom,camera_kt"`, `reg`, and no
children. That image booted on mici and dmesg reported:

```text
CAM_INFO: CAM-UTIL: cam_main_probe: 320: Spectra camera_kt driver initialized rc : 0
```

Treat the combined req-mgr/sync child shape from `5b02808` as failed, not the
root itself. Continue by changing one child variable at a time.

Commit `12b6ff1` added only the `qcom,cam-sync` child under that proven root.
That image also booted on mici. Sysfs showed
`ac00000.camera-kt:cam-sync` populated and bound under
`/sys/bus/platform/drivers/cam_sync`. No `/dev/video*` or `/dev/media*` nodes
appeared, which matches the driver: sync registers a component and waits for
req-mgr to become the component master.

Commit `4b159fe` added `qcom,cam-req-mgr` under the same root+sync shape. That
image booted on mici, req-mgr bound sync, and userspace nodes appeared:
`/dev/video0` named `cam-req-mgr`, `/dev/video1` named `cam_sync`, plus
`/dev/media0` and `/dev/media1`.

Commit `48cdb8e` made the root an explicit addressable bus with
`#address-cells`, `#size-cells`, and `ranges`, without adding hardware nodes.
That image booted on mici and preserved the req-mgr/sync bindings and
`/dev/video0`/`/dev/video1` nodes. This is the base shape for later children
with `reg` ranges.

Commit `ed1c859` added CPAS as the first real hardware-probing child under the
proven addressable root. The image booted, and the live DT showed
`cam-cpas@ac40000`, but CPAS failed bind before video nodes appeared:

```text
CAM_ERR: CAM-UTIL: cam_soc_util_configure_opp: 1803: OPP add_table failed for dev ac40000.cam-cpas rc -19
```

Commit `535a0be` added an `operating-points-v2` table for the CPAS
`slow_ahb_clk_src` rates. The image booted and passed OPP setup, but CPAS then
failed its default AHB ICC vote because the AHB table had only two usecases and
the driver voted `CAM_SVS_VOTE` at enum level 3:

```text
CAM_ERR: CAM-UTIL: cam_soc_bus_client_update_request: 64: Invalid vote level=3, usecases=2
```

Commit `9703bd1` expanded `cam-ahb-bw-KBps` to eight enum-indexed usecases.
That image booted on mici as `6.18.0-vamos-9703bd1`, `ac40000.cam-cpas` bound
to `cam-cpas`, req-mgr bound both sync and CPAS, and the expected nodes returned:
`/dev/video0` named `cam-req-mgr`, `/dev/video1` named `cam_sync`,
`/dev/media0`, `/dev/media1`, and `/dev/v4l-subdev0`.

Commit `d594129` trimmed the CPAS and virtual CDM client lists to the current
openpilot path and booted on mici as `6.18.0-vamos-d594129`. The virtual CDM
interface bound as `ac00000.camera-kt:cam-cdm-intf -> msm_cam_cdm_intf`, req-mgr
bound sync, CPAS, and CDM interface, and the same req-mgr/sync/CPAS user nodes
remained present.

Commit `c908681` added the openpilot-required SMMU children only: `ife`, `icp`,
`cpas-cdm0`, `cam-secure`, and the ICP firmware device backed by `camera_mem`.
It booted on mici as `6.18.0-vamos-c908681`; all SMMU nodes bound to
`msm_cam_smmu`, and req-mgr/sync/CPAS/CDM stayed present. The boot log warns in
`iommu_set_fault_handler()` for each non-secure CB because mainline 6.18 rejects
fault handlers on DMA-cookie domains; binding continues.

Commit `a26a734` attempted the first real CPAS CDM hardware node
(`qcom,cam170-cpas-cdm0`) with only `ife`/`ife3` CDM clients and an SMMU label
alias for `cpas-cdm`. It built and flashed, but did not boot: a 10-second MDMA
`profile-boot` reached ABL `Exit BS` / `UEFI End` at about 4.55s and printed no
Linux earlycon output. Commit `818c807` reverts only that CPAS CDM increment and
boots on mici as `6.18.0-vamos-818c807`. Treat the `a26a734` shape as rejected;
the next CPAS CDM attempt needs smaller boot-verified slices.

The smaller CPAS CDM slices show the safe boundary. Commit `f2fc7a2` adds only
the SMMU alias `cam-smmu-label = "cpas-cdm0", "cpas-cdm"` and boots as
`6.18.0-vamos-f2fc7a2`. Commit `3655f26` adds the full `qcom,cam170-cpas-cdm0`
node with `status = "disabled"` and boots as `6.18.0-vamos-3655f26`; diagnostics
show no hardware CDM bind and the existing req-mgr/sync/CPAS/virtual-CDM/SMMU
nodes remain present. Commit `70e1533` enables that same node with only the
legacy AGNOS `"ife"` CDM client and still fails before a shell comes up; a
10-second profile reaches ABL `Exit BS` / `UEFI End` at about 4.56s with no
Linux output. Commit `dde427c` reverts the enablement and boots as
`6.18.0-vamos-dde427c`. Therefore the failure is in the enabled CPAS CDM
probe/power/reset path, not the SMMU alias, disabled node shape, or added
`ife3` client name.

The next active hardware checkpoint keeps CPAS CDM disabled and reintroduces
only the openpilot-needed ISP/ICP blocks: `qcom,cam-isp`, CSID/VFE indices 0,
1, and lite index 2, `qcom,cam-icp`, `qcom,a5`, and `qcom,bps`. The IFE, VFE,
CSID, and A5 nodes use mainline CAMCC `power-domains`, but add
`qcom,skip-probe-power-domain-cycle` so the recent `camera_kt` SOC helper does
not briefly enable/disable the camera GDSCs during component bind. Runtime
power-domain enablement remains unchanged; the property only avoids the
probe-time cycle while the DTS hardware contract is being isolated.

Commit `ed981c3` enables the correct mainline Qualcomm GENI earlycon command
line (`earlycon=qcom_geni,0x00a84000,115200n8`) with temporary high-verbosity
boot logging. The first raw serial capture with that command line proves Linux
starts after UEFI and reaches `camera_init`; it then trips
`bps_gdsc status stuck at 'off'` from `dev_pm_domain_attach()` while creating
the `qcom,bps` platform device. That happens before the Spectra driver probe
can honor `qcom,skip-probe-power-domain-cycle`, so the next checkpoint keeps the
BPS node present but intentionally omits `power-domains = <&clock_camcc
BPS_GDSC>`. Treat BPS runtime power as an isolated follow-up after the kernel
boots with the minimal ISP/ICP device graph. Validate this checkpoint with a
kernel build, flash, 30-second serial `uname -a`, and a 30-second serial boot
capture before adding CCI, CSIPHY, or sensor nodes.

The next serial checkpoint for `9275abf` passes the BPS attach point: `qcom,bps`
probes successfully and `camera_init` returns. Deferred component binding then
exposes the next recent-driver contract. CSID and VFE components call
`cam_soc_util_configure_opp()`, which unconditionally calls
`devm_pm_opp_of_add_table()`. Without `operating-points-v2` on the hardware
nodes, CSID/VFE SOC resource setup fails with `-ENODEV`; component rollback then
hits a null `cam_ife_csid_component_unbind()` path. The staged DT now adds
shared OPP tables for CSID and VFE, plus A5/BPS OPP tables, using the same source
clock frequencies already present in each node's `clock-rates`.

## Translation Notes

- Add a downstream root compatible with `"qcom,camera_kt"`. The direct
  root-under-`&soc` shape is now proven to boot when it has no children.
  Do not repeat the failed `5b02808` shape that added req-mgr and sync
  together before each child has been isolated.
  Hardware nodes with `reg` ranges may need an explicit addressable bus layer
  when they are added.
- Add `qcom,cam-req-mgr` and `qcom,cam-sync` early. Legacy AGNOS has req-mgr;
  recent `camera_kt` also includes sync.
- Be careful with sensor nesting. Legacy AGNOS puts sensor slots under `&cam_cci`,
  and newer Google camera-devicetree examples still use `&cam_cci0` and
  `&cam_cci1`. The legacy AGNOS CCI driver populated child nodes, but this
  `camera_kt` CCI driver does not. Either add sensor slots where
  `camera_main.c` can populate them directly, or add a deliberate child
  population hook after verifying the recent driver expects it.
- Use recent driver property names, not every legacy name:
  - resource manager shared GPIOs are `gpios-shared`, not legacy
    `shared-gpios`.
  - sensors use `csiphy-sd-index` and `cci-master` without a `qcom,` prefix.
- Use `camera_mem` for ICP firmware memory unless a later test proves a
  separate `pil_camera_mem` carveout is required.
- Use mainline regulator labels:
  - `pm8998_lvs1` becomes `vreg_lvs1a_1p8`
  - `pmi8998_bob` becomes `vreg_bob`
  - `pm8998_l26` becomes `vreg_l26a_1p2`
  - verify the legacy `pm8998_l1`/MIPI CSI supply choice before enabling all
    CSIPHY power paths, because vamOS labels `vreg_l1a_0p875` at 0.88 V while
    the downstream property says 1.2 V.
- `cam_clk` in sensor `regulator-names` is not just descriptive. The sensor
  utility tries to enable a regulator named `cam_clk` around MCLK sequencing.
  Either preserve a valid supply for it or adjust the sensor power sequence
  deliberately.

## Bring-up Order

1. Keep the direct active `camera-kt` root under `&soc`; it boots on mici in
   commit `c6783a6`.
2. Keep `qcom,cam-sync`; it boots and binds in commit `12b6ff1`. No video node
   is expected yet because sync only registers a component until req-mgr becomes
   the master.
3. Keep `qcom,cam-req-mgr`; it boots, binds sync, and creates `/dev/video0`
   and `/dev/video1` in commit `4b159fe`.
4. Keep the explicit addressable root bus; it boots in commit `48cdb8e`.
5. Keep CPAS with the translated register, clock, power-domain, OPP, AHB ICC,
   and client properties. It boots and binds in commit `9703bd1`.
6. Add the virtual CDM interface with only the parser-required ISP-facing client
   name, then verify the existing req-mgr/sync/CPAS nodes still boot.
7. Add SMMU context banks required by openpilot (`ife`, `icp`, `cpas-cdm0`, and
   `cam-secure`), then verify platform device probes.
8. Keep the SMMU alias and disabled `qcom,cam170-cpas-cdm0` node. Do not enable
   CPAS CDM again until the enabled probe path is fixed or instrumented; even the
   legacy `"ife"`-only client list fails in `70e1533`. After the enabled probe is
   boot-safe, add the recent openpilot-needed `ife3` client as its own checkpoint.
9. Add ISP/ICP/BPS hardware nodes needed by openpilot streaming, with
   `qcom,skip-probe-power-domain-cycle` on power-domain-backed hardware nodes.
   Leave JPEG, LRME, IPE, FD, OPE, TFE, SFE, and custom blocks out.
10. Add CCI, CSIPHY0-2, MCLK/reset/VANA pinctrl, fixed camera regulators, and
   three sensor slots. Verify chip-ID probing with the standalone camera test.
