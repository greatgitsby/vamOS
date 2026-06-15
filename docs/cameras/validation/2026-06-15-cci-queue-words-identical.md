# CCI init/reset + queue words: ALL identical, NACK persists

**Date:** 2026-06-15. Continues the legacy-vs-mainline hunt after the power-up was
made byte-identical (`2026-06-15-powerup-timing-diff.md`) yet the read still NACKs.

## CCI master init / reset / state — matches legacy

Live capture (both kernels instrumented). Per **every** sensor probe, mainline does:
`MSM_CCI_INIT (cmd=0)` → `init_master reset all cmd=0xf73f3f7` → IRQ
`status0=0x1000000` (RST_DONE_ACK) → `init done` (sets `cci_state=ENABLED`) →
`MSM_CCI_I2C_READ (cmd=5)` with `state=0` (= `CCI_STATE_ENABLED`; enum: ENABLED=0,
DISABLED=1) → read → NACK → `RELEASE (cmd=1)`.

So the CCI **is** freshly reset and enabled before each read, exactly like legacy
(which also init/resets per probe-open). The earlier static-read guess of "mainline
does no per-read reset" was wrong — the reset happens per-INIT, one INIT per probe.
IRQ mask (0x7fff7ff7 / 0x110000), RD thresholds (0x30), queue sizes (q0=128/q1=32),
hw_version (0x10070000), cci_clk (37.5 MHz) all match.

## CCI queue command words — BYTE-IDENTICAL

Dumped every word written to `CCI_I2C_M0_Q0_LOAD_DATA` on both kernels for the
OS04C10 chip-ID read (sid=0x36, reg 0x300a, 2 bytes):

| word | legacy 4.9 (ACK) | mainline (NACK) | meaning |
| --- | --- | --- | --- |
| SET_PARAM | `0x00030361` | `0x00030361` | sid 0x36, retries 3, id_map 0 |
| LOCK | `0x00000006` | `0x00000006` | |
| WRITE_DISABLE_P | `0x000a302b` | `0x000a302b` | register-address phase (0x300a, addr_type 2) |
| READ | `0x0000002a` | `0x0000002a` | num_byte 2 |
| UNLOCK | `0x00000007` | `0x00000007` | |

The **exact transaction the CCI executes is identical** — same slave address, same
register-address bytes, same read length, same lock/unlock framing. The sensor
receives a byte-for-byte identical I2C transaction on both kernels.

## State of the investigation — everything software-visible now matches

Matched 1:1 between the working legacy 4.9 and the NACKing mainline camera_kt, all
verified live on the same board:

- power rails (VIO/VANA/VDIG), genpd/TITAN_TOP_GDSC
- MCLK: 24.000 MHz, enable_count=1, continuous through the read
- reset: low→(MCLK)→high deassert + 34 ms settle (full 6-step power-up)
- pinctrl / pad mux (cci_i2c, pull-up) and a physically-clocked transaction
- CCI init + per-probe reset (RST_DONE_ACK), cci_state=ENABLED at read
- cci_clk 37.5 MHz; SCL/SDA/MISC timing regs (0x260038/0x280028/0x160023/0x3e/0x63)
- IRQ masks, RD thresholds, queue sizes
- **the literal 5 CCI queue command words**

Legacy ACKs 3/3; mainline NACKs 3/3. The divergence is **not** in any register,
clock, power rail, GPIO, timing, or command word we can read from the kernel.

## Remaining hypotheses (outside the CCI register/command domain)

Since the transaction the controller drives is provably identical and the board works
in production on legacy (so not electrical):

1. **CPAS / SoC bus or power vote around the CCI block.** Mainline votes
   `CAM_LOWSVS_VOTE` (vote=2) via `cam_soc_util_enable_platform_resource`. Legacy's
   CCI enable may apply a different ICC/CPAS/AHB vote or a CCI-block clock that
   mainline omits — leaving the CCI analog/IO domain underpowered even though the
   digital register writes succeed. Diff legacy `cam_cci_soc`/CPAS enable vs mainline.
2. **CCI controller GPIO/IO sourcing.** Confirm the CCI's own IO/voltage domain (not
   just the pad mux) is powered identically.
3. **A second, sensor-side precondition the legacy *driver* performs that is not a DT
   power step** — e.g. an init register write to the sensor, or a different probe
   opcode sequence — though the queue-word match argues against this for the read
   itself.

The `exec=0x5 read_level=0x0` (queue ran all 5 words, 0 bytes read back) with a NACK
on the address byte means the slave never ACKs — consistent with the sensor's I2C
block not being fully alive despite correct rails/MCLK/reset, i.e. an SoC-side enable
the CCI digital path doesn't capture. Next: diff the CPAS/platform-resource enable.

Diagnostic patches: 0035 (power_setting dump), 0036 (queue-word dump).
