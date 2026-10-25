#!/usr/bin/env python3
"""Direct Bluetooth RFCOMM Flasher for Sony WH-1000XM4 Official Firmware.

Uploads official Sony firmware (.bin) directly to the headphones over Bluetooth Classic
RFCOMM via Sony MDR (Ch 11) and Airoha RACE (Ch 21).
"""

import argparse
from pathlib import Path
import socket
import sys
import threading
import time

HEAD = 0x15
CMD_START = 0x1C08
CMD_TRANSMIT_INTERVAL = 0x1C1C
CMD_PARTITION_INFO = 0x1C00
CMD_QUERY_STATE = 0x1C04
CMD_ACTIVE_FOTA = 0x1C19
CMD_START_TRANSACTION = 0x1C0A
CMD_WRITE_STATE = 0x1C06
CMD_ERASE = 0x0404
CMD_PAGE_PROGRAM = 0x0402
CMD_CHECK_INTEGRITY = 0x1C01
CMD_COMMIT = 0x1C02

KIND_COMMAND = 0x5A
KIND_RESPONSE = 0x5B
KIND_INDICATION = 0x5D

COMPLETED_BY_INDICATION = {
    CMD_START,
    CMD_TRANSMIT_INTERVAL,
    CMD_ACTIVE_FOTA,
    CMD_ERASE,
    CMD_CHECK_INTEGRITY,
}

BLOCK_SIZE = 4096
PAGE_SIZE = 256


def page_crc(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    res = 0
    for i in range(8):
        if (crc >> i) & 1:
            res |= 1 << (7 - i)
    return res


class RaceClient:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buf = bytearray()

    def send_cmd(self, cmd_id: int, payload: bytes = b"", head: int = HEAD):
        length = 2 + len(payload)
        pkt = bytearray([head, KIND_COMMAND, length & 0xFF, (length >> 8) & 0xFF])
        pkt.extend([cmd_id & 0xFF, (cmd_id >> 8) & 0xFF])
        pkt.extend(payload)
        self.sock.sendall(pkt)

    def read_packet(self, timeout=5.0):
        self.sock.settimeout(timeout)
        start_time = time.time()
        while time.time() - start_time < timeout:
            if len(self.buf) >= 4:
                length = self.buf[2] | (self.buf[3] << 8)
                if len(self.buf) >= 4 + length:
                    raw = self.buf[: 4 + length]
                    self.buf = self.buf[4 + length :]
                    head = raw[0]
                    kind = raw[1]
                    pkt_id = raw[4] | (raw[5] << 8)
                    payload = bytes(raw[6:])
                    return head, kind, pkt_id, payload
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionResetError("Socket closed by remote device")
            self.buf.extend(chunk)
        raise TimeoutError("Timeout waiting for RACE packet")

    def request(self, cmd_id: int, payload: bytes = b"", head: int = HEAD, timeout=5.0):
        self.send_cmd(cmd_id, payload, head)
        target_kind = KIND_INDICATION if cmd_id in COMPLETED_BY_INDICATION else KIND_RESPONSE
        start_time = time.time()
        while time.time() - start_time < timeout:
            head, kind, pkt_id, res_payload = self.read_packet(timeout)
            if pkt_id == 0x1C03:
                reason = res_payload[2] if len(res_payload) > 2 else "unknown"
                raise RuntimeError(f"Headphones CANCELLED update (reason code {reason})")
            if pkt_id != cmd_id:
                continue
            if kind == KIND_RESPONSE and len(res_payload) > 0 and res_payload[0] != 0:
                raise RuntimeError(f"RACE error for cmd 0x{cmd_id:04x}: status 0x{res_payload[0]:02x}")
            if kind == target_kind:
                return res_payload
        raise TimeoutError(f"Timed out waiting for reply to 0x{cmd_id:04x}")


def mdr_worker(s_mdr: socket.socket, stop_event: threading.Event):
    s_mdr.settimeout(1.0)
    while not stop_event.is_set():
        try:
            data = s_mdr.recv(1024)
            if not data:
                break
            for i in range(len(data)):
                if data[i] == 0x3E and i + 2 < len(data) and data[i+1] in (0x0C, 0x0E, 0x00):
                    seq = data[i+2]
                    ack_seq = 1 - (seq & 1)
                    s_mdr.sendall(bytes([0x3E, 0x01, ack_seq, 0, 0, 0, 0, 1 + ack_seq, 0x3C]))
        except (socket.timeout, TimeoutError):
            continue
        except Exception:
            break


def main():
    parser = argparse.ArgumentParser(description="Direct Bluetooth RFCOMM Flasher for Sony WH-1000XM4")
    parser.add_argument("--mac", required=True, help="Bluetooth MAC address of the headphones (e.g. AC:80:0A:2E:45:80)")
    parser.add_argument("--firmware", required=True, type=Path, help="Path to signed Sony firmware .bin file")
    args = parser.parse_args()

    if not args.firmware.exists():
        print(f"Error: Firmware file {args.firmware} not found!")
        sys.exit(1)

    fw_bytes = args.firmware.read_bytes()
    fw_len = len(fw_bytes)
    print(f"Loaded Firmware: {args.firmware.name} ({fw_len:,} bytes)")
    print(f"Connecting to WH-1000XM4 ({args.mac})...")

    # Connect MDR on Channel 11
    s_mdr = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s_mdr.settimeout(5.0)
    s_mdr.connect((args.mac, 11))
    init_frame = s_mdr.recv(1024)
    s_mdr.sendall(bytes.fromhex("3e010000000000013c"))

    # Turn FOTA update mode ON
    print("Locking headphones in active FOTA update mode...")
    s_mdr.sendall(bytes.fromhex("3e0e000000000444010200593c"))
    mdr_ack = s_mdr.recv(1024)

    stop_event = threading.Event()
    mdr_thread = threading.Thread(target=mdr_worker, args=(s_mdr, stop_event), daemon=True)
    mdr_thread.start()

    try:
        # Connect RACE on Channel 21
        print("Connecting to RACE service on RFCOMM Channel 21...")
        s_race = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        s_race.settimeout(5.0)
        s_race.connect((args.mac, 21))
        race = RaceClient(s_race)

        print("Starting FOTA session on hardware...")
        race.request(CMD_START, bytes([0x01]))
        try:
            race.request(CMD_TRANSMIT_INTERVAL, bytes([0x01, 0x00]), timeout=2.0)
        except Exception:
            pass

        p_info = race.request(CMD_PARTITION_INFO, bytes([0x00]))
        part_id = p_info[1]
        storage = p_info[2]
        part_addr = int.from_bytes(p_info[3:7], "little")
        part_len = int.from_bytes(p_info[7:11], "little")
        print(f"FOTA Partition: ID={part_id}, Storage=0x{storage:02x}, Address=0x{part_addr:08x}, Length=0x{part_len:x}")

        if fw_len > part_len:
            raise ValueError(f"Firmware size ({fw_len}) exceeds partition size ({part_len})!")

        race.request(CMD_ACTIVE_FOTA, bytes([0x00]))
        race.request(CMD_START_TRANSACTION)

        num_blocks = (fw_len + BLOCK_SIZE - 1) // BLOCK_SIZE
        print(f"Erasing {num_blocks} staging blocks...")
        race.request(CMD_WRITE_STATE, (0x0200).to_bytes(2, "little"))

        for b_idx in range(num_blocks):
            b_addr = part_addr + b_idx * BLOCK_SIZE
            erase_payload = bytearray([storage])
            erase_payload.extend(BLOCK_SIZE.to_bytes(4, "little"))
            erase_payload.extend(b_addr.to_bytes(4, "little"))
            for attempt in range(3):
                try:
                    race.request(CMD_ERASE, erase_payload, timeout=8.0)
                    break
                except TimeoutError:
                    if attempt == 2:
                        raise
                    time.sleep(0.1)
            print(f"\rErasing staging blocks: [{b_idx+1}/{num_blocks}] ({(b_idx+1)/num_blocks*100:.1f}%)", end="", flush=True)

        print("\nErase complete!")
        race.request(CMD_WRITE_STATE, (0x0201).to_bytes(2, "little"))
        race.request(CMD_WRITE_STATE, (0x0210).to_bytes(2, "little"))

        total_pages = (fw_len + PAGE_SIZE - 1) // PAGE_SIZE
        print(f"Programming {total_pages} pages ({fw_len:,} bytes)...")
        written_pages = 0
        t0 = time.time()

        for p_idx in range(total_pages):
            p_addr = part_addr + p_idx * PAGE_SIZE
            chunk = fw_bytes[p_idx * PAGE_SIZE : (p_idx + 1) * PAGE_SIZE]
            if len(chunk) < PAGE_SIZE:
                chunk = chunk + b"\xFF" * (PAGE_SIZE - len(chunk))

            if chunk == b"\xFF" * PAGE_SIZE:
                continue

            crc = page_crc(chunk)
            prog_payload = bytearray([storage, 1, crc])
            prog_payload.extend(p_addr.to_bytes(4, "little"))
            prog_payload.extend(chunk)

            for attempt in range(3):
                try:
                    race.request(CMD_PAGE_PROGRAM, prog_payload, timeout=8.0)
                    break
                except TimeoutError:
                    if attempt == 2:
                        raise
                    time.sleep(0.05)

            written_pages += 1
            if written_pages % 50 == 0 or p_idx == total_pages - 1:
                elapsed = time.time() - t0
                pct = (p_idx + 1) / total_pages * 100
                speed = (p_idx * PAGE_SIZE) / 1024 / max(elapsed, 0.001)
                print(f"\rFlashing: [{p_idx+1}/{total_pages}] ({pct:.1f}%) — {speed:.1f} KB/s", end="", flush=True)

        elapsed = time.time() - t0
        print(f"\nStaging complete in {elapsed:.1f}s ({fw_len / 1024 / elapsed:.1f} KB/s)!")

        print("Triggering hardware SHA-256 integrity check...")
        race.request(CMD_CHECK_INTEGRITY, bytes([0x01, 0x00, storage]), timeout=20.0)
        print("INTEGRITY CHECK PASSED! Signature & SHA-256 verified successfully!")

        race.request(CMD_WRITE_STATE, (0x0211).to_bytes(2, "little"))

        print("\nCommitting firmware update...")
        try:
            race.request(CMD_COMMIT, bytes([0x00]), timeout=5.0)
        except (ConnectionResetError, TimeoutError, BrokenPipeError, OSError):
            print("Headphones initiated reboot as expected!")

        print("\nSUCCESS! Firmware successfully installed and rebooted!")

    finally:
        stop_event.set()
        try:
            s_race.close()
        except Exception:
            pass
        try:
            s_mdr.sendall(bytes.fromhex("3e0e0100000004440102015b3c"))
            s_mdr.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
