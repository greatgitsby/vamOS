#!/usr/bin/env python3
"""OS04C10/OX03C10 chip-id probe via the camera_kt v1.0.3 Spectra UAPI.

Byte-for-byte replica of openpilot camerad's sensors_init() probe packet
(openpilot system/camerad/cameras/spectra.cc): the same power-up sequence
(vio/vana/vdig -> reset low -> MCLK 24MHz -> reset high -> 34ms settle) and
the same chip-id read over CCI, submitted with CAM_SENSOR_PROBE_CMD.

Pure python + ioctl: no openpilot build, no C toolchain, works over the
MDMA serial link on the mainline kernel. Struct layouts match
kernel/spectra-qcom/camera-driver/camera_kt/include/uapi/camera/media/
(all sensor structs are __attribute__((packed))).

Usage:
    sensor_probe.py [--slots 0,1,2] [--sensor os04c10|ox03c10]
Exit code = number of slots that failed to probe.
"""
import argparse
import ctypes
import fcntl
import glob
import mmap
import os
import struct
import sys

# ---- UAPI constants (camera_kt v1.0.3) ----
VIDIOC_CAM_CONTROL = (3 << 30) | (24 << 16) | (ord('V') << 8) | 192  # _IOWR('V', BASE_VIDIOC_PRIVATE, struct cam_control[24])
CAM_HANDLE_USER_POINTER = 1
CAM_HANDLE_MEM_HANDLE = 2
CAM_SENSOR_PROBE_CMD = 0x100 + 0xA + 1      # CAM_COMMON_OPCODE_MAX + 1
CAM_REQ_MGR_ALLOC_BUF = 0x100 + 0xA + 9
CAM_REQ_MGR_RELEASE_BUF = 0x100 + 0xA + 11
CAM_MEM_FLAGS = (1 << 3) | (1 << 4) | (1 << 6)  # KMD_ACCESS | UMD_ACCESS | CMD_BUF_TYPE
CAM_CMD_BUF_I2C = 0x7
CAM_CMD_BUF_LEGACY = 0xA
CSL_DEVICE_TYPE_IMAGE_SENSOR = 0x01 << 24   # openpilot spectra.h
CAM_SENSOR_PACKET_OPCODE_SENSOR_PROBE = 3
# enum camera_sensor_cmd_type
CMD_TYPE_PROBE, CMD_TYPE_PWR_UP, CMD_TYPE_PWR_DOWN, CMD_TYPE_I2C_INFO = 1, 2, 3, 4
CMD_TYPE_WAIT = 9
WAIT_OP_SW_UCND = 3
I2C_TYPE_WORD = 2
I2C_FAST_MODE = 1

SENSORS = {
    # slave addrs per slot, probe reg, expected chip id, mclk Hz  (openpilot sensors/)
    'os04c10': {'slave': [0x6C, 0x20, 0x6C], 'reg': 0x300A, 'chip_id': 0x5304, 'mclk': 24000000},
    'ox03c10': {'slave': [0x6C, 0x20, 0x6C], 'reg': 0x300A, 'chip_id': 0x5803, 'mclk': 24000000},
}


def cam_control(fd, op_code, handle, size):
    """do_cam_control(): size==0 -> handle is a mem handle, else user pointer."""
    if size == 0:
        buf = bytearray(struct.pack('<IIIIQ', op_code, 8, CAM_HANDLE_MEM_HANDLE, 0, handle))
    else:
        buf = bytearray(struct.pack('<IIIIQ', op_code, size, CAM_HANDLE_USER_POINTER, 0, handle))
    try:
        fcntl.ioctl(fd, VIDIOC_CAM_CONTROL, buf)
        return 0
    except OSError as e:
        return -e.errno


def alloc_buf(video0_fd, length):
    """CAM_REQ_MGR_ALLOC_BUF -> (buf_handle, mmap). No MMU handles (CPU-only cmd buf)."""
    cmd = ctypes.create_string_buffer(104)
    struct.pack_into('<QQ', cmd, 0, length, 8)                # len, align
    struct.pack_into('<II', cmd, 80, 0, CAM_MEM_FLAGS)        # num_hdl, flags
    ret = cam_control(video0_fd, CAM_REQ_MGR_ALLOC_BUF, ctypes.addressof(cmd), 104)
    if ret != 0:
        raise OSError(-ret, f'ALLOC_BUF({length}) failed')
    buf_handle, fd, _vaddr = struct.unpack_from('<IiQ', cmd, 88)
    mem = mmap.mmap(fd, 4096)  # dma-buf is page-sized; kernel parses only claimed length
    os.close(fd)
    return buf_handle, mem


def release_buf(video0_fd, handle):
    buf = ctypes.create_string_buffer(struct.pack('<iI', handle, 0), 8)
    cam_control(video0_fd, CAM_REQ_MGR_RELEASE_BUF, ctypes.addressof(buf), 8)


def power_block(cmd_type, settings):
    b = struct.pack('<IBBH', len(settings), 0, cmd_type, 0)
    for seq_type, lo, hi in settings:
        b += struct.pack('<HHII', seq_type, 0, lo, hi)
    return b


def wait_block(ms):
    return struct.pack('<hHBBH', ms, 0, WAIT_OP_SW_UCND, CMD_TYPE_WAIT, 0)


def build_power_blob(mclk_hz):
    """Exact sensors_init() sequence. camerad claims 196 bytes; the kernel parses
    only that much, which truncates after the reset-low block (rails stay on) --
    replicated faithfully, including writing the full 248-byte sequence."""
    return b''.join([
        power_block(CMD_TYPE_PWR_UP, [(3, 0, 0), (1, 0, 0), (2, 0, 0), (8, 0, 0)]), wait_block(1),
        power_block(CMD_TYPE_PWR_UP, [(0, mclk_hz, 0)]), wait_block(1),
        power_block(CMD_TYPE_PWR_UP, [(8, 1, 0)]), wait_block(34),
        # probe happens here
        power_block(CMD_TYPE_PWR_DOWN, [(0, 0, 0)]), wait_block(1),
        power_block(CMD_TYPE_PWR_DOWN, [(8, 1, 0)]), wait_block(1),
        power_block(CMD_TYPE_PWR_DOWN, [(8, 0, 0)]), wait_block(1),
        power_block(CMD_TYPE_PWR_DOWN, [(2, 0, 0), (1, 0, 0), (3, 0, 0)]),
    ])


def open_video0():
    nodes = sorted(glob.glob('/dev/v4l/by-path/*cam-req-mgr-video-index0'))
    if not nodes:
        sys.exit('no cam-req-mgr video node found')
    return os.open(nodes[0], os.O_RDWR | os.O_NONBLOCK)


def open_subdev(name, index):
    for i in range(64):
        try:
            with open(f'/sys/class/video4linux/v4l-subdev{i}/name') as f:
                if f.read().strip().startswith(name):
                    if index == 0:
                        return os.open(f'/dev/v4l-subdev{i}', os.O_RDWR | os.O_NONBLOCK)
                    index -= 1
        except FileNotFoundError:
            break
    return -1


def probe_slot(video0_fd, slot, sensor):
    sensor_fd = open_subdev('cam-sensor-driver', slot)
    if sensor_fd < 0:
        print(f'slot {slot}: no cam-sensor-driver subdev')
        return False
    handles = []
    try:
        # cmd buf 0: i2c info + probe   (8 + 20 = 28 bytes)
        i2c_hdl, i2c_mem = alloc_buf(video0_fd, 28)
        handles.append(i2c_hdl)
        i2c_mem[0:8] = struct.pack('<IBBH', sensor['slave'][slot], I2C_FAST_MODE, CMD_TYPE_I2C_INFO, 0)
        i2c_mem[8:28] = struct.pack('<BBBBIIIHH', I2C_TYPE_WORD, I2C_TYPE_WORD, 3, CMD_TYPE_PROBE,
                                    sensor['reg'], sensor['chip_id'], 0, slot, 0)

        # cmd buf 1: power sequence (claimed length 196, matching camerad)
        pwr_hdl, pwr_mem = alloc_buf(video0_fd, 196)
        handles.append(pwr_hdl)
        blob = build_power_blob(sensor['mclk'])
        pwr_mem[0:len(blob)] = blob

        # the packet: header + 2 cmd buf descs
        size = 56 + 2 * 24 + 8  # sizeof(cam_packet) incl payload[1] + 2 descs = 112
        pkt_hdl, pkt_mem = alloc_buf(video0_fd, size)
        handles.append(pkt_hdl)
        pkt_mem[0:24] = struct.pack('<IIQII', CSL_DEVICE_TYPE_IMAGE_SENSOR | CAM_SENSOR_PACKET_OPCODE_SENSOR_PROBE,
                                    size, 0, 0, 0)
        pkt_mem[24:56] = struct.pack('<IIIIIIiI', 0, 2, 0, 0, 0, 0, -1, 0)
        pkt_mem[56:80] = struct.pack('<iIIIII', i2c_hdl, 0, 28, 28, CAM_CMD_BUF_LEGACY, 0)
        pkt_mem[80:104] = struct.pack('<iIIIII', pwr_hdl, 0, 196, 196, CAM_CMD_BUF_I2C, 0)

        ret = cam_control(sensor_fd, CAM_SENSOR_PROBE_CMD, pkt_hdl, 0)
        ok = (ret == 0)
        print(f'slot {slot} slave 0x{sensor["slave"][slot]:02x}: '
              f'{"PROBE OK (chip id matched)" if ok else f"PROBE FAIL ret={ret} ({os.strerror(-ret)})"}')
        return ok
    finally:
        for h in handles:
            release_buf(video0_fd, h)
        os.close(sensor_fd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--slots', default='0,1,2')
    ap.add_argument('--sensor', default='os04c10', choices=SENSORS.keys())
    args = ap.parse_args()

    sensor = SENSORS[args.sensor]
    video0_fd = open_video0()
    fails = 0
    for slot in [int(s) for s in args.slots.split(',')]:
        if not probe_slot(video0_fd, slot, sensor):
            fails += 1
    os.close(video0_fd)
    print(f'result: {3 - fails if len(args.slots.split(",")) == 3 else "?"}/3 ok' if len(args.slots.split(',')) == 3
          else f'result: fails={fails}')
    sys.exit(fails)


if __name__ == '__main__':
    main()
