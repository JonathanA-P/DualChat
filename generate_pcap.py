#!/usr/bin/env python3
"""
generate_pcap.py - Menghasilkan file rekaman paket jaringan standar (.pcap)
untuk analisis Wireshark pada proyek DualChat (TCP Port 5000 & UDP Port 5001).
"""

import json
import struct
import socket
import time
from pathlib import Path

def checksum(data: bytes) -> int:
    if len(data) % 2 == 1:
        data += b'\x00'
    s = sum(struct.unpack('!%dH' % (len(data) // 2), data))
    s = (s >> 16) + (s & 0xffff)
    s += (s >> 16)
    return (~s) & 0xffff

def make_eth():
    # Destination MAC, Source MAC, EtherType (0x0800 = IPv4)
    return b'\x02\x00\x00\x00\x00\x01\x02\x00\x00\x00\x00\x02\x08\x00'

def make_ip(src_ip: str, dst_ip: str, proto: int, payload_len: int, ident: int = 0x1234):
    total_len = 20 + payload_len
    hdr_no_csum = struct.pack(
        '!BBHHHBBH4s4s',
        0x45, 0, total_len, ident, 0x4000, 64, proto, 0,
        socket.inet_aton(src_ip), socket.inet_aton(dst_ip)
    )
    csum = checksum(hdr_no_csum)
    return struct.pack(
        '!BBHHHBBH4s4s',
        0x45, 0, total_len, ident, 0x4000, 64, proto, csum,
        socket.inet_aton(src_ip), socket.inet_aton(dst_ip)
    )

def make_udp(src_port: int, dst_port: int, src_ip: str, dst_ip: str, payload: bytes):
    length = 8 + len(payload)
    psh = struct.pack('!4s4sBBH', socket.inet_aton(src_ip), socket.inet_aton(dst_ip), 0, 17, length)
    udp_hdr = struct.pack('!HHHH', src_port, dst_port, length, 0)
    csum = checksum(psh + udp_hdr + payload) or 0xffff
    return struct.pack('!HHHH', src_port, dst_port, length, csum) + payload

def make_tcp(src_port: int, dst_port: int, src_ip: str, dst_ip: str, seq: int, ack: int, flags: int, payload: bytes):
    offset_res = (5 << 4)
    psh = struct.pack('!4s4sBBH', socket.inet_aton(src_ip), socket.inet_aton(dst_ip), 0, 6, 20 + len(payload))
    tcp_hdr = struct.pack('!HHIIBBHHH', src_port, dst_port, seq, ack, offset_res, flags, 65535, 0, 0)
    csum = checksum(psh + tcp_hdr + payload)
    return struct.pack('!HHIIBBHHH', src_port, dst_port, seq, ack, offset_res, flags, 65535, csum, 0) + payload

def write_pcap_file(filepath: Path, packets: list[tuple[float, bytes]]):
    # Standard libpcap global header: magic=0xa1b2c3d4, v2.4, thiszone=0, sigfigs=0, snaplen=65535, network=1 (Ethernet)
    pcap_hdr = struct.pack('<IHHiIII', 0xa1b2c3d4, 2, 4, 0, 0, 65535, 1)
    with open(filepath, 'wb') as f:
        f.write(pcap_hdr)
        for ts, pkt in packets:
            sec = int(ts)
            usec = int((ts - sec) * 1000000)
            f.write(struct.pack('<IIII', sec, usec, len(pkt), len(pkt)))
            f.write(pkt)

def build_all_captures():
    results_dir = Path(__file__).resolve().parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time() - 60.0
    eth = make_eth()

    tcp_packets: list[tuple[float, bytes]] = []
    udp_packets: list[tuple[float, bytes]] = []
    all_packets: list[tuple[float, bytes]] = []

    ip_c1 = "10.0.2.15"  # Emulator 1 (jo)
    ip_c2 = "10.0.2.16"  # Emulator 2 (Salma)
    ip_srv = "10.0.2.2"  # Host Server

    # =========================================================================
    # 1. PERCAKAPAN TCP (PORT 5000)
    # =========================================================================
    p_tcp_jo = 35960
    p_tcp_salma = 51172
    p_srv_tcp = 5000

    t = t0

    # A. 3-Way Handshake jo -> Server
    # Packet 1: SYN
    ip = make_ip(ip_c1, ip_srv, 6, 20, 0x101)
    tcp = make_tcp(p_tcp_jo, p_srv_tcp, ip_c1, ip_srv, 1000, 0, 0x02, b'')  # SYN
    tcp_packets.append((t, eth + ip + tcp))

    # Packet 2: SYN-ACK
    t += 0.0006
    ip = make_ip(ip_srv, ip_c1, 6, 20, 0x201)
    tcp = make_tcp(p_srv_tcp, p_tcp_jo, ip_srv, ip_c1, 5000, 1001, 0x12, b'')  # SYN-ACK
    tcp_packets.append((t, eth + ip + tcp))

    # Packet 3: ACK
    t += 0.0003
    ip = make_ip(ip_c1, ip_srv, 6, 20, 0x102)
    tcp = make_tcp(p_tcp_jo, p_srv_tcp, ip_c1, ip_srv, 1001, 5001, 0x10, b'')  # ACK
    tcp_packets.append((t, eth + ip + tcp))

    # B. jo sends JOIN
    t += 0.010
    jo_join_bytes = json.dumps({"type": "JOIN", "seq": 0, "ts": t, "user": "jo", "data": ""}, separators=(',', ':')).encode('utf-8') + b'\n'
    ip = make_ip(ip_c1, ip_srv, 6, 20 + len(jo_join_bytes), 0x103)
    tcp = make_tcp(p_tcp_jo, p_srv_tcp, ip_c1, ip_srv, 1001, 5001, 0x18, jo_join_bytes)  # PSH-ACK
    tcp_packets.append((t, eth + ip + tcp))

    # Server sends ACK_JOIN
    t += 0.0008
    ack_join_bytes = json.dumps({"type": "ACK_JOIN", "seq": 0, "ts": t, "user": "server", "data": "Selamat datang jo"}, separators=(',', ':')).encode('utf-8') + b'\n'
    ip = make_ip(ip_srv, ip_c1, 6, 20 + len(ack_join_bytes), 0x202)
    tcp = make_tcp(p_srv_tcp, p_tcp_jo, ip_srv, ip_c1, 5001, 1001 + len(jo_join_bytes), 0x18, ack_join_bytes)
    tcp_packets.append((t, eth + ip + tcp))

    # C. Salma 3-Way Handshake & JOIN
    t += 0.100
    ip = make_ip(ip_c2, ip_srv, 6, 20, 0x301)
    tcp = make_tcp(p_tcp_salma, p_srv_tcp, ip_c2, ip_srv, 2000, 0, 0x02, b'')
    tcp_packets.append((t, eth + ip + tcp))

    t += 0.0005
    ip = make_ip(ip_srv, ip_c2, 6, 20, 0x203)
    tcp = make_tcp(p_srv_tcp, p_tcp_salma, ip_srv, ip_c2, 6000, 2001, 0x12, b'')
    tcp_packets.append((t, eth + ip + tcp))

    t += 0.0003
    ip = make_ip(ip_c2, ip_srv, 6, 20, 0x302)
    tcp = make_tcp(p_tcp_salma, p_srv_tcp, ip_c2, ip_srv, 2001, 6001, 0x10, b'')
    tcp_packets.append((t, eth + ip + tcp))

    t += 0.005
    salma_join_bytes = json.dumps({"type": "JOIN", "seq": 0, "ts": t, "user": "Salma", "data": ""}, separators=(',', ':')).encode('utf-8') + b'\n'
    ip = make_ip(ip_c2, ip_srv, 6, 20 + len(salma_join_bytes), 0x303)
    tcp = make_tcp(p_tcp_salma, p_srv_tcp, ip_c2, ip_srv, 2001, 6001, 0x18, salma_join_bytes)
    tcp_packets.append((t, eth + ip + tcp))

    # Server ACK_JOIN to Salma & broadcast "Salma bergabung" to jo
    t += 0.0008
    ack_join_salma = json.dumps({"type": "ACK_JOIN", "seq": 0, "ts": t, "user": "server", "data": "Selamat datang Salma"}, separators=(',', ':')).encode('utf-8') + b'\n'
    ip = make_ip(ip_srv, ip_c2, 6, 20 + len(ack_join_salma), 0x204)
    tcp = make_tcp(p_srv_tcp, p_tcp_salma, ip_srv, ip_c2, 6001, 2001 + len(salma_join_bytes), 0x18, ack_join_salma)
    tcp_packets.append((t, eth + ip + tcp))

    notif_salma_bytes = json.dumps({"type": "MSG", "seq": 1, "ts": t, "user": "server", "data": "Salma bergabung"}, separators=(',', ':')).encode('utf-8') + b'\n'
    ip = make_ip(ip_srv, ip_c1, 6, 20 + len(notif_salma_bytes), 0x205)
    tcp = make_tcp(p_srv_tcp, p_tcp_jo, ip_srv, ip_c1, 5001 + len(ack_join_bytes), 1001 + len(jo_join_bytes), 0x18, notif_salma_bytes)
    tcp_packets.append((t + 0.0002, eth + ip + tcp))

    # D. jo sends DM to Salma: "[DM:Salma] halo Salma"
    t += 0.150
    msg_jo_bytes = json.dumps({"type": "MSG", "seq": 1, "ts": t, "user": "jo", "data": "[DM:Salma] halo Salma"}, separators=(',', ':')).encode('utf-8') + b'\n'
    ip = make_ip(ip_c1, ip_srv, 6, 20 + len(msg_jo_bytes), 0x104)
    tcp = make_tcp(p_tcp_jo, p_srv_tcp, ip_c1, ip_srv, 1001 + len(jo_join_bytes), 5001 + len(ack_join_bytes) + len(notif_salma_bytes), 0x18, msg_jo_bytes)
    tcp_packets.append((t, eth + ip + tcp))

    # Server forwards to Salma
    t += 0.0007
    ip = make_ip(ip_srv, ip_c2, 6, 20 + len(msg_jo_bytes), 0x206)
    tcp = make_tcp(p_srv_tcp, p_tcp_salma, ip_srv, ip_c2, 6001 + len(ack_join_salma), 2001 + len(salma_join_bytes), 0x18, msg_jo_bytes)
    tcp_packets.append((t, eth + ip + tcp))

    # Salma replies to jo: "[DM:jo] okee jo"
    t += 0.120
    msg_salma_bytes = json.dumps({"type": "MSG", "seq": 1, "ts": t, "user": "Salma", "data": "[DM:jo] okee jo"}, separators=(',', ':')).encode('utf-8') + b'\n'
    ip = make_ip(ip_c2, ip_srv, 6, 20 + len(msg_salma_bytes), 0x304)
    tcp = make_tcp(p_tcp_salma, p_srv_tcp, ip_c2, ip_srv, 2001 + len(salma_join_bytes), 6001 + len(ack_join_salma) + len(msg_jo_bytes), 0x18, msg_salma_bytes)
    tcp_packets.append((t, eth + ip + tcp))

    # Server forwards to jo
    t += 0.0006
    ip = make_ip(ip_srv, ip_c1, 6, 20 + len(msg_salma_bytes), 0x207)
    tcp = make_tcp(p_srv_tcp, p_tcp_jo, ip_srv, ip_c1, 5001 + len(ack_join_bytes) + len(notif_salma_bytes), 1001 + len(jo_join_bytes) + len(msg_jo_bytes), 0x18, msg_salma_bytes)
    tcp_packets.append((t, eth + ip + tcp))

    # E. PING / PONG (Latency RTT measurement)
    t += 0.080
    ping_bytes = json.dumps({"type": "PING", "seq": 2, "ts": t, "user": "jo", "data": ""}, separators=(',', ':')).encode('utf-8') + b'\n'
    ip = make_ip(ip_c1, ip_srv, 6, 20 + len(ping_bytes), 0x105)
    tcp = make_tcp(p_tcp_jo, p_srv_tcp, ip_c1, ip_srv, 1001 + len(jo_join_bytes) + len(msg_jo_bytes), 5001 + len(ack_join_bytes) + len(notif_salma_bytes) + len(msg_salma_bytes), 0x18, ping_bytes)
    tcp_packets.append((t, eth + ip + tcp))

    t += 0.0006
    pong_bytes = json.dumps({"type": "PONG", "seq": 2, "ts": t, "user": "jo", "data": ""}, separators=(',', ':')).encode('utf-8') + b'\n'
    ip = make_ip(ip_srv, ip_c1, 6, 20 + len(pong_bytes), 0x208)
    tcp = make_tcp(p_srv_tcp, p_tcp_jo, ip_srv, ip_c1, 5001 + len(ack_join_bytes) + len(notif_salma_bytes) + len(msg_salma_bytes), 1001 + len(jo_join_bytes) + len(msg_jo_bytes) + len(ping_bytes), 0x18, pong_bytes)
    tcp_packets.append((t, eth + ip + tcp))

    # F. TCP 4-Way Handshake Connection Termination (jo disconnects)
    t += 0.200
    ip = make_ip(ip_c1, ip_srv, 6, 20, 0x106)
    tcp = make_tcp(p_tcp_jo, p_srv_tcp, ip_c1, ip_srv, 1001 + len(jo_join_bytes) + len(msg_jo_bytes) + len(ping_bytes), 5001 + len(ack_join_bytes) + len(notif_salma_bytes) + len(msg_salma_bytes) + len(pong_bytes), 0x11, b'')  # FIN-ACK
    tcp_packets.append((t, eth + ip + tcp))

    t += 0.0005
    ip = make_ip(ip_srv, ip_c1, 6, 20, 0x209)
    tcp = make_tcp(p_srv_tcp, p_tcp_jo, ip_srv, ip_c1, 5001 + len(ack_join_bytes) + len(notif_salma_bytes) + len(msg_salma_bytes) + len(pong_bytes), 1001 + len(jo_join_bytes) + len(msg_jo_bytes) + len(ping_bytes) + 1, 0x10, b'')  # ACK
    tcp_packets.append((t, eth + ip + tcp))

    t += 0.0004
    ip = make_ip(ip_srv, ip_c1, 6, 20, 0x20A)
    tcp = make_tcp(p_srv_tcp, p_tcp_jo, ip_srv, ip_c1, 5001 + len(ack_join_bytes) + len(notif_salma_bytes) + len(msg_salma_bytes) + len(pong_bytes), 1001 + len(jo_join_bytes) + len(msg_jo_bytes) + len(ping_bytes) + 1, 0x11, b'')  # FIN-ACK
    tcp_packets.append((t, eth + ip + tcp))

    t += 0.0003
    ip = make_ip(ip_c1, ip_srv, 6, 20, 0x107)
    tcp = make_tcp(p_tcp_jo, p_srv_tcp, ip_c1, ip_srv, 1001 + len(jo_join_bytes) + len(msg_jo_bytes) + len(ping_bytes) + 1, 5001 + len(ack_join_bytes) + len(notif_salma_bytes) + len(msg_salma_bytes) + len(pong_bytes) + 1, 0x10, b'')  # ACK final
    tcp_packets.append((t, eth + ip + tcp))


    # =========================================================================
    # 2. PERCAKAPAN UDP (PORT 5001) - 0-RTT Connectionless
    # =========================================================================
    p_udp_jo = 36851
    p_udp_salma = 39853
    p_srv_udp = 5001

    t = t0 + 1.0  # Mulai 1 detik setelah sesi TCP

    # A. jo JOIN via UDP (0-RTT: Langsung datagram tanpa SYN/ACK)
    u_jo_join = json.dumps({"type": "JOIN", "seq": 0, "ts": t, "user": "jo", "data": ""}, separators=(',', ':')).encode('utf-8')
    ip = make_ip(ip_c1, ip_srv, 17, 8 + len(u_jo_join), 0x401)
    udp = make_udp(p_udp_jo, p_srv_udp, ip_c1, ip_srv, u_jo_join)
    udp_packets.append((t, eth + ip + udp))

    # Server ACK_JOIN
    t += 0.0005
    u_ack_jo = json.dumps({"type": "ACK_JOIN", "seq": 0, "ts": t, "user": "server", "data": "Selamat datang jo"}, separators=(',', ':')).encode('utf-8')
    ip = make_ip(ip_srv, ip_c1, 17, 8 + len(u_ack_jo), 0x501)
    udp = make_udp(p_srv_udp, p_udp_jo, ip_srv, ip_c1, u_ack_jo)
    udp_packets.append((t, eth + ip + udp))

    # B. Salma JOIN via UDP
    t += 0.050
    u_salma_join = json.dumps({"type": "JOIN", "seq": 0, "ts": t, "user": "Salma", "data": ""}, separators=(',', ':')).encode('utf-8')
    ip = make_ip(ip_c2, ip_srv, 17, 8 + len(u_salma_join), 0x601)
    udp = make_udp(p_udp_salma, p_srv_udp, ip_c2, ip_srv, u_salma_join)
    udp_packets.append((t, eth + ip + udp))

    # Server ACK_JOIN to Salma
    t += 0.0005
    u_ack_salma = json.dumps({"type": "ACK_JOIN", "seq": 0, "ts": t, "user": "server", "data": "Selamat datang Salma"}, separators=(',', ':')).encode('utf-8')
    ip = make_ip(ip_srv, ip_c2, 17, 8 + len(u_ack_salma), 0x502)
    udp = make_udp(p_srv_udp, p_udp_salma, ip_srv, ip_c2, u_ack_salma)
    udp_packets.append((t, eth + ip + udp))

    # Server notification to jo
    t += 0.0002
    u_notif_salma = json.dumps({"type": "MSG", "seq": 13, "ts": t, "user": "server", "data": "Salma bergabung"}, separators=(',', ':')).encode('utf-8')
    ip = make_ip(ip_srv, ip_c1, 17, 8 + len(u_notif_salma), 0x503)
    udp = make_udp(p_srv_udp, p_udp_jo, ip_srv, ip_c1, u_notif_salma)
    udp_packets.append((t, eth + ip + udp))

    # C. jo sends Message via UDP: "[DM:Salma] halo lewat UDP"
    t += 0.080
    u_msg_jo = json.dumps({"type": "MSG", "seq": 1, "ts": t, "user": "jo", "data": "[DM:Salma] halo lewat UDP"}, separators=(',', ':')).encode('utf-8')
    ip = make_ip(ip_c1, ip_srv, 17, 8 + len(u_msg_jo), 0x402)
    udp = make_udp(p_udp_jo, p_srv_udp, ip_c1, ip_srv, u_msg_jo)
    udp_packets.append((t, eth + ip + udp))

    # Server forwards to Salma (No transport ACK!)
    t += 0.0004
    ip = make_ip(ip_srv, ip_c2, 17, 8 + len(u_msg_jo), 0x504)
    udp = make_udp(p_srv_udp, p_udp_salma, ip_srv, ip_c2, u_msg_jo)
    udp_packets.append((t, eth + ip + udp))

    # D. Salma replies: "[DM:jo] okee diterima via UDP"
    t += 0.060
    u_msg_salma = json.dumps({"type": "MSG", "seq": 2, "ts": t, "user": "Salma", "data": "[DM:jo] okee diterima via UDP"}, separators=(',', ':')).encode('utf-8')
    ip = make_ip(ip_c2, ip_srv, 17, 8 + len(u_msg_salma), 0x602)
    udp = make_udp(p_udp_salma, p_srv_udp, ip_c2, ip_srv, u_msg_salma)
    udp_packets.append((t, eth + ip + udp))

    # Server forwards to jo
    t += 0.0004
    ip = make_ip(ip_srv, ip_c1, 17, 8 + len(u_msg_salma), 0x505)
    udp = make_udp(p_srv_udp, p_udp_jo, ip_srv, ip_c1, u_msg_salma)
    udp_packets.append((t, eth + ip + udp))

    # E. UDP PING / PONG (Sub-millisecond RTT)
    t += 0.050
    u_ping = json.dumps({"type": "PING", "seq": 3, "ts": t, "user": "jo", "data": ""}, separators=(',', ':')).encode('utf-8')
    ip = make_ip(ip_c1, ip_srv, 17, 8 + len(u_ping), 0x403)
    udp = make_udp(p_udp_jo, p_srv_udp, ip_c1, ip_srv, u_ping)
    udp_packets.append((t, eth + ip + udp))

    t += 0.0003
    u_pong = json.dumps({"type": "PONG", "seq": 3, "ts": t, "user": "jo", "data": ""}, separators=(',', ':')).encode('utf-8')
    ip = make_ip(ip_srv, ip_c1, 17, 8 + len(u_pong), 0x506)
    udp = make_udp(p_srv_udp, p_udp_jo, ip_srv, ip_c1, u_pong)
    udp_packets.append((t, eth + ip + udp))

    # F. BURST TEST (10 Paket Beruntun untuk Wireshark IO Graph)
    t += 0.100
    for burst_idx in range(1, 11):
        b_data = json.dumps({"type": "MSG", "seq": 10 + burst_idx, "ts": t, "user": "jo", "data": f"[BURST #{burst_idx}] Uji Throughput UDP Wireshark"}, separators=(',', ':')).encode('utf-8')
        ip = make_ip(ip_c1, ip_srv, 17, 8 + len(b_data), 0x410 + burst_idx)
        udp = make_udp(p_udp_jo, p_srv_udp, ip_c1, ip_srv, b_data)
        udp_packets.append((t, eth + ip + udp))

        # Server forwards to Salma
        ip_fwd = make_ip(ip_srv, ip_c2, 17, 8 + len(b_data), 0x510 + burst_idx)
        udp_fwd = make_udp(p_srv_udp, p_udp_salma, ip_srv, ip_c2, b_data)
        udp_packets.append((t + 0.0003, eth + ip_fwd + udp_fwd))

        t += 0.004

    # Gabungkan seluruh paket dalam urutan waktu deterministik
    all_packets = sorted(tcp_packets + udp_packets, key=lambda x: x[0])

    # Tulis file PCAP
    pcap_combined = results_dir / "dualchat_capture.pcap"
    pcap_tcp = results_dir / "dualchat_tcp.pcap"
    pcap_udp = results_dir / "dualchat_udp.pcap"

    write_pcap_file(pcap_combined, all_packets)
    write_pcap_file(pcap_tcp, tcp_packets)
    write_pcap_file(pcap_udp, udp_packets)

    print(f"[OK] Berhasil membuat 3 file capture Wireshark:")
    print(f"  - {pcap_combined} ({len(all_packets)} paket TCP & UDP)")
    print(f"  - {pcap_tcp} ({len(tcp_packets)} paket TCP)")
    print(f"  - {pcap_udp} ({len(udp_packets)} paket UDP)")

if __name__ == "__main__":
    build_all_captures()
