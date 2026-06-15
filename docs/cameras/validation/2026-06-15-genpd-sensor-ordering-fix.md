# Sensor I2C NACK root cause: genpd power-domain on sensor nodes

**Date:** 2026-06-15. Follows
[`2026-06-14-sensor-nack-rootcause.md`](./2026-06-14-sensor-nack-rootcause.md).

## The gap between legacy AGNOS 4.9 and mainline vamOS

The previous investigation proved every steady-state electrical precondition was
correct at the pad (rails, 24 MHz MCLK, CCI pull-ups, a full 400 kHz transaction
clocked out, reset deasserted) yet the OS04C10 still address-NACKs on mainline,
while the **same board probes 3/3 on the legacy AGNOS 4.9 kernel**. That localized
the divergence to a **power-up *sequencing* difference**, not a static DTS gap.

This note names the difference: the **`power-domains` (genpd) property on the
sensor DT nodes**, which legacy never had.

### Evidence

- Legacy AGNOS `comma_mici.dts`: **zero `power-domains` properties** in the whole
  device tree (`grep -c power-domains` -> 0). Legacy sensor nodes
  (`sdm845-camera-sensor-mtp.dtsi`) bring up `TITAN_TOP_GDSC` purely through the
  `cam_clk` **regulator** (`cam_clk-supply = <&titan_top_gdsc>`,
  `regulator-names = "...","cam_clk"`), enabled at the **MCLK** power step.
- vamOS mainline `sdm845-comma-common.dtsi` sensor nodes carried **both**
  mechanisms: the legacy `cam_clk` regulator **and** an added
  `power-domains = <&clock_camcc TITAN_TOP_GDSC>`.
- In the recent `camera_kt` driver, `cam_sensor_core_power_up()` calls
  `cam_soc_util_power_domain_enable_default()` inside the **regulator** case
  (`cam_sensor_util.c`). With a `power-domains` on the node, `num_genpd == 1`, so
  that helper does `dev_pm_genpd_set_performance_state(dev, INT_MAX)` +
  `pm_runtime_get_sync(dev)` — i.e. it powers `TITAN_TOP_GDSC` on at the **first
  regulator step (VIO)**, far earlier than legacy's MCLK-step enable. The breadcrumb
  in patch 0028 observed it returning `rc=1` ("already on") around VANA/VDIG.
- Net effect: `TITAN_TOP_GDSC` comes up at the wrong point relative to
  VANA/VDIG/reset-deassert. Steady state ends up correct, but the **transient
  ordering around reset-deassert** differs from legacy, leaving the sensor's
  internal I2C block unresponsive -> address NACK.

## The fix (this iteration)

Remove the `power-domains` property from `cam_sensor0/1/2` in
`kernel/dts/sdm845-comma-common.dtsi`. With no `power-domains`, `num_genpd == 0`
and `cam_soc_util_power_domain_enable_default()` is a no-op, so `TITAN_TOP_GDSC`
is brought up **only** via the `cam_clk` regulator at the MCLK step — byte-for-byte
the legacy AGNOS 4.9 sensor power sequence.

Notes:
- Only the **sensor** nodes lose `power-domains`. CSIPHY/IFE/ICP/BPS/IPE nodes keep
  theirs (22 `power-domains` properties remain; those blocks are genpd-managed on
  mainline and not part of this NACK path).
- Patch `0028-cam-sensor-defer-power-domain-to-mclk.patch` is now redundant (its
  reordering only matters when `num_genpd >= 1`). It is left applied for this
  verification flash so its `pr_emerg` breadcrumbs log the no-op /
  "power-domains not defined" path, confirming `num_genpd == 0`. Strip 0028 + the
  temporary breadcrumb/diagnostic patches once the NACK clears.
- `qcom,skip-probe-power-domain-cycle` was never present on the sensor nodes (it is
  on CSIPHY nodes), so nothing to remove there. The driver does not implement that
  property anyway (no source reference), so it was a no-op on sensors regardless.

Also restored the Qualcomm submodule to pristine: a stray uncommitted edit in
`cam_cci_core.c` had deleted `#define CCI_MASTER_LOCK_TIMEOUT` while three call
sites still reference it — that alone would break the build.

## Verification

Build kernel, flash mici, run the probe:

```bash
cd ~/claudes/vamOS && ./vamos build kernel
/home/trey/.claude/skills/mici/scripts/mdma.py reboot-qdl
./vamos flash kernel
/home/trey/.claude/skills/mici/scripts/mdma.py reboot
# on device:
cd /data/openpilot && system/camerad/spectra_camera_test --sensor os04c10 --probe-only
# expect: probe summary: 3/3 cameras OK using OS04C10
```

## Result — 2026-06-15, kernel #194 (`6.18.0-vamos-a3cc6c4`)

Built, flashed, booted clean (no boot stall from the DTS change). Verified in the
built DTB that `cam-sensor0/1/2` carry **no `power-domains`** while the other 90+
genpd consumers are untouched. Ran `spectra_camera_test --sensor os04c10
--probe-only`: still **0/3** — the genpd change did **NOT** clear the NACK.

**The genpd enable is now confirmed a no-op** (good): patch 0028's breadcrumb prints
`power domain enable before-mclk ... rc=0` for every sensor — previously `rc=1`
("already on"). With `num_genpd == 0` the helper early-returns; `TITAN_TOP_GDSC` is
now brought up purely by the `cam_clk` regulator, exactly like legacy.

**The NACK is byte-for-byte identical to before the fix:**

```
irq nack cci=0 master=0 queue=1 status0=0x10000000 status1=0x0 cur=0x2 exec=0x5 read_level=0x0
M0_Q1 NACK ERROR: 0x10000000
read status error master=0 status=0xffffffea slave=0x20   (EINVAL / -22)
sda_transitions=23 scl_transitions=80   (master 0)
... M1_Q1 NACK ERROR: 0x40000000 for master 1 / slave 0x6c, same shape
```

Same SDA/SCL transition counts, same `cur=0x2 exec=0x5 read_level=0x0` (queue
executes 5 words but reads back 0 bytes), same halt→reset→`0xffffffea`.

### Conclusion

**genpd power-domain ordering is RULED OUT as the NACK cause.** It was the strongest
remaining *power-sequencing* hypothesis; eliminating it (with zero change to the CCI
signature) moves the root cause decisively to the **CCI controller programming /
read-completion path in the recent `camera_kt` driver vs legacy 4.9** — the
`read_level=0x0` after `exec=0x5` is the tell (the controller ran the transaction
words but latched no read data → the addressed slave never ACKed *as the recent
driver programmed the transaction*).

### Keep the fix anyway

The DTS change stays: removing `power-domains` from the sensor nodes removes a
genuine legacy/mainline divergence, makes the sensor power model match the
known-good legacy 4.9 path 1:1, and simplifies reasoning about every later probe.
Patch `0028` is now dead weight (its reorder only mattered when `num_genpd>=1`) and
should be dropped along with the temporary breadcrumb/diagnostic patch stack once a
working probe is reached.

## Next: CCI read-completion path (the surviving hypothesis)

This is exactly the prior note's "Still plausible" #1, now the *only* live lead.
Diff the recent driver's `cam_cci_read` against legacy 4.9 for:
- **report_q vs rd_done completion** — `exec=0x5, read_level=0x0` means the queue
  executed but the read FIFO drained nothing. Legacy may wait on a different
  completion / read the report queue differently.
- **SET_PARAM id_map / retries / second SET_PARAM** before the read.
- **MISC_CTL / THZ / glitch-filter / half-cycle** programming.
- **freq mode actually programmed** (FAST vs STANDARD reconcile).

The decisive experiment remains: flash legacy 4.9, instrument its `cam_cci_read`
register writes for a *successful* 0x6c chip-ID read, and diff word-for-word against
the mainline #194 CCI breadcrumbs.
