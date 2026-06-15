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

## CPAS / platform-resource / clock-vote diff — also matches

Mapped both drivers' CCI block enable path (cam_cci_soc.c + cam_soc_util):
- **Clock vote:** both `cam_soc_util_enable_platform_resource(..., CAM_LOWSVS_VOTE, ...)`
  (vote=2). Live: `vote=2 applied_src=37500000`. Match.
- **CPAS:** both call `cam_cpas_start` with AHB `CAM_SVS_VOTE` (patch 0006 raised
  mainline LOWSVS→SVS to match legacy) + default AXI bw. Match.
- **CCI DT node clocks:** identical 6 clocks (camnoc_axi/soc_ahb/slow_ahb/cpas_ahb/
  cci_clk/cci_clk_src), identical rates (`...37500000`), `clock-cntl-level="lowsvs"`,
  `src-clock-name="cci_clk_src"`. Match.
- **CCI pad pinctrl:** legacy `drive-strength=<2>` (2mA) + `bias-pull-up`; mainline
  live pad read `pull=3 (pull-up) drv=0` (=2mA). Match at the register.
- Only CCI-node difference: GDSC via regulator `gdscr-supply` (legacy) vs genpd
  `power-domains` (mainline) — both leave TITAN_TOP powered (registers writable,
  pads toggle). Not material.

## CCI init/reset lifecycle — a real off-by-one, but NOT the NACK cause

Clean-buffer capture of the full 3-sensor probe shows the openpilot probe opens CCI
per sensor per master. Two quirks found:
1. The **very first read** (sid=0x36, master 0) fires with `ref=1` and **no preceding
   CCI INIT/reset** — a CCI open leaked before the probe, so `ref_count` was already 1
   and `cam_cci_init_master`'s reset (guarded by `is_initilized` / `ref_count==1`) was
   skipped for that first read.
2. Subsequent reads **do** get a full `init_master reset all (0xf73f3f7)` + `reset
   done` before the read (confirmed in the log).

But reads #2 and #3, which **do** get a fresh master reset, **NACK identically** to
read #1. So the missing-first-reset is a real lifecycle bug worth fixing for
correctness, but it is **not** the cause of the NACK — a freshly-reset master with the
identical queue still NACKs the address.

## Cold-boot + clock-topology checks (2026-06-15, deep dive)

- **Cold-boot first-touch:** on legacy, `spectra_camera_test_legacy` as the *very first*
  camera touch after a cold boot (no camerad, no warm-up) probes **3/3 OK**. So the
  legacy success is NOT a one-time userspace precondition — it is established by the
  legacy *kernel* alone. Confirms the divergence is kernel-side.
- **CAMCC clock topology — matches during active use.** A first idle reading showed
  mainline `cci_clk_src=19.2 MHz/bi_tcxo` (vs legacy 37.5/PLL0) — but that is just the
  **parked** state. Tight-sampled through the active probe window, mainline
  `cci_clk_src` is **37,500,000 Hz from cam_cc_pll0_out_even** continuously while a read
  is in flight (parks back to 19.2/TCXO only when idle). mclk0_src = 24 MHz from
  pll2_out_even on both. So the clock the CCI runs at during the transaction matches
  legacy. (Initial 19.2 reading was a false alarm — verified and corrected.)
- **titan_top GDSCR config bits differ** (informational): legacy idle `0x0022F001` vs
  mainline `0x00282001`; mainline shows PWR_ON (bit31) = 1 during the probe
  (`0xF8282000`), so titan_top IS powered when active. The differing mid bits
  (legacy `0xF000` in [15:12] vs mainline `0x2000`) are GDSC power-up-delay /
  enable-rest fields, not obviously tied to an I2C address ACK.

## Bottom line

Every software-controllable layer — power, genpd, MCLK, reset+settle, CCI
init+reset+state, all clocks, CPAS/AHB/AXI votes, timing registers, IRQ masks, pad
drive/pull, and the literal I2C command words — now provably matches the working
legacy 4.9 on the same board, and the OS04C10 still NACKs its address on mainline
only. The difference is not anything the kernel programs into the CCI or the clock/
power tree. Open frontier: a sensor-side or SoC-side precondition that legacy
establishes outside the per-probe CCI path (e.g. a one-time sensor/SoC init the legacy
stack does at boot/camerad-start that the standalone probe inherits on legacy but not
on mainline), or a hardware-version-gated CCI behavior. Recommend a focused diff of
what the legacy *system* (boot + camerad init) does to the sensor/CCI once, before any
probe, that the mainline image does not.
