# Sensor probe I2C NACK - root-cause research

Gate 4 (sensor probe) blocker on the recent `camera_kt` Spectra stack. The DTS
creates camera nodes and the standalone camera probe reaches the CCI chip-ID
read, but every OS04C10 sensor still address-NACKs.

## Current checkpoint - 2026-06-15

Tested on mici with kernel:

```text
Linux none 6.18.0-vamos-9985ba6 #191 SMP PREEMPT Mon Jun 15 13:51:17 UTC 2026 aarch64
```

Standalone probe:

```bash
cd /data/openpilot
system/camerad/spectra_camera_test --sensor os04c10 --probe-only
```

Result:

```text
camera 0 (wide)   slave=0x6c: PROBE FAILED
camera 1 (road)   slave=0x20: PROBE FAILED
camera 2 (driver) slave=0x6c: PROBE FAILED
probe summary: 0/3 cameras OK using OS04C10
```

All three failures are true CCI address NACKs, not timeouts.

## Power and pinctrl evidence

The current mainline DTS has been aligned with the legacy AGNOS camera power
wiring where it matters for the three openpilot cameras:

- Active CAM MCLK0/1/2 drive strength is 2 mA, matching the mici override in
  `agnos-builder/agnos-kernel-sdm845/.../comma_mici.dts`.
- Sensor 0 active pinctrl includes MCLK0 + RESET0, matching legacy rear sensor
  wiring. Sensor 1 remains MCLK1-only, matching legacy. Sensor 2 includes MCLK2
  + the legacy `cam_sensor_front_active` gpio28 state.
- Shared VANA gpio8 is owned by `cam_res_mgr` and selected through the
  cam-res-mgr pinctrl state. Sensor nodes still list gpio8 in their req table
  because the driver expects it there, but the shared-gpio refcount path drives
  it.
- Rear DVDD is the fixed `camera_rear_ldo` at 1.05 V on PM8998 GPIO12, with the
  PMIC GPIO configured as normal-function, pulldown, push-pull, high strength,
  output-low before regulator enable.
- CCI suspend pinctrl is back to mainline/legacy pull-down; active CCI pinctrl is
  cci_i2c + pull-up + 2 mA.

Live powered-hold snapshot for wide camera:

```text
cam-sensor0-cam_clk   enabled
bob                   3328mV
  cam-sensor0-cam_vana enabled, 80mA, 3328-3584mV
lvs1                  1800mV
  cam-sensor0-cam_vio  enabled
camera_rear_ldo       enabled, 1050mV
  cam-sensor0-cam_vdig enabled, 105mA, 1050mV
```

Pinmux during the same powered hold:

```text
pin 8  GPIO_8  ac00000.camera-kt:cam-res-mgr gpio
pin 9  GPIO_9  ac00000.camera-kt:cam-sensor0 gpio
pin 13 GPIO_13 ac00000.camera-kt:cam-sensor0 cam_mclk
pin 17 GPIO_17 ac4a000.cci cci_i2c
pin 18 GPIO_18 ac4a000.cci cci_i2c
pm8998 gpio12 camera-rear-ldo function normal
```

Driver-camera slot 2 powered-hold pinmux also verifies the legacy extra gpio28
state:

```text
pin 8  GPIO_8  ac00000.camera-kt:cam-res-mgr gpio
pin 12 GPIO_12 GPIO 3400000.pinctrl:564
pin 15 GPIO_15 ac00000.camera-kt:cam-sensor2 cam_mclk
pin 19 GPIO_19 ac4a000.cci cci_i2c
pin 20 GPIO_20 ac4a000.cci cci_i2c
pin 28 GPIO_28 ac00000.camera-kt:cam-sensor2 gpio
```

Per-slot pre-match breadcrumbs show VANA high, reset high, CCI idle high, and a
toggling 24 MHz MCLK pad for slots 0, 1, and 2.

## CCI bus evidence

Patch `0033-cam-cci-sample-sda-scl-during-read.patch` maps TLMM before
`CCI_QUEUE_START` and samples SDA/SCL immediately after starting the queue.

Wide / master 0:

```text
sample read-after-queue-start-tight master=0
sda_gpio=17 sda_ones=936 sda_zeros=88  sda_transitions=31
scl_gpio=18 scl_ones=927 scl_zeros=97  scl_transitions=80
irq nack cci=0 master=0 queue=1 status0=0x10000000
read status error master=0 status=0xffffffea slave=0x6c
```

Road / master 0, slave 0x20:

```text
sda_transitions=23 scl_transitions=80
irq nack cci=0 master=0 queue=1 status0=0x10000000
```

Driver / master 1:

```text
sample read-after-queue-start-tight master=1
sda_gpio=19 sda_transitions=31
scl_gpio=20 scl_transitions=80
irq nack cci=0 master=1 queue=1 status0=0x40000000
```

This proves the CCI controller is physically driving both CCI buses. The failure
is now cleanly beyond "pad not muxed", "MCLK missing", or "CCI never left the
SoC": an address transaction reaches the bus and the sensor does not ACK.

## Updated root-cause ranking

### Ruled out or strongly downgraded

- **Missing active pinctrl for power/reset/MCLK.** Runtime pinmux now matches the
  legacy-good wiring for wide and driver, including slot 2 gpio28.
- **MCLK not oscillating.** CAMCC reports MCLK branch enabled and the pad sampler
  sees transitions after CCI init for all three slots.
- **CCI pads not toggling.** Tight TLMM sampling sees SDA/SCL transitions on
  both CCI masters during the chip-ID read.
- **Basic power sequence ordering.** VIO -> VANA -> VDIG -> reset-low -> MCLK ->
  reset-high + 34 ms matches the known-good legacy sequence and all regulator /
  GPIO steps return success.

### Still plausible

- **CCI controller/programming semantic mismatch vs legacy.** The queue executes
  and NACKs, but the recent driver may still differ from the legacy 4.9 driver in
  subtle command ordering, reset/error handling, retry/id-map semantics, or
  master setup that changes the transaction the sensor sees.
- **Analog/electrical issue not visible from kernel debug.** Rails, GPIO latches,
  MCLK, and CCI pad transitions all look correct from software. A scope could
  still reveal voltage, edge, or reset timing differences at the sensor side.
- **A hidden power/reset dependency outside the three obvious rails.** Current
  evidence argues against this, but it is not completely impossible without
  measuring at the module.

## Next actions

1. Compare legacy `cam_cci_read()` / IRQ / master-init code against the patched
   recent driver at the command-word and register-write level.
2. Capture the exact legacy CCI read queue programming for a successful
   OS04C10 chip-ID read, then diff it against the #191 mainline breadcrumb.
3. Keep the current power/pinctrl DTS shape; do not chase broad DT changes unless
   a new measurement contradicts the powered-hold snapshots.
4. Once the NACK clears, remove the temporary verbose breadcrumbs and proceed to
   CSIPHY/IFE frame streaming with `spectra_camera_test`.
