"""
benchmark.py - Client benchmark otomatis dengan workload deterministik untuk DualChat.

Tujuan:
Menjalankan beban kerja (workload) yang tetap dan dapat diulang tanpa input manual
di atas TCP maupun UDP, lalu menghasilkan log CSV dengan skema kolom identik
untuk dianalisis (RTT, throughput, packet loss, out-of-order, duplikat).

Mode yang didukung:
1. `--mode ping`:
   Mengirim COUNT pesan PING berurutan sesuai interval, menunggu PONG (timeout 2 detik),
   dan menghitung RTT = (t_recv - ts) * 1000 ms.
2. `--mode msg --role sender`:
   Mengirim COUNT pesan MSG dengan seq 0..COUNT-1 dan payload deterministik ('x' * size).
3. `--mode msg --role receiver`:
   Bergabung (JOIN) lalu mencatat setiap pesan MSG yang masuk sampai tidak ada pesan
   baru selama 3 detik, kemudian menghitung statistik penerimaan dan throughput.
"""

from __future__ import annotations

import argparse
import logging
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from chat_core import (
    BENCH_CSV_COLUMNS,
    DEFAULT_HOST,
    DEFAULT_TCP_PORT,
    DEFAULT_UDP_PORT,
    CsvLogger,
     encode_message,
    make_message,
)
from transport import TcpTransport, UdpTransport, create_transport


def _perform_join(
    transport: TcpTransport | UdpTransport,
    proto: str,
    host: str,
    port: int,
    user: str,
    join_timeout: float = 2.0,
) -> bool:
    """
    Menghubungkan socket ke server dan melakukan handshake aplikasi (JOIN -> ACK_JOIN).
    """
    transport.client_connect(host, port)
    join_msg = make_message(msg_type="JOIN", seq=0, user=user, data="")
    transport.client_send(join_msg)

    deadline = time.monotonic() + join_timeout
    while True:
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0.0:
            return False
        reply = transport.client_recv(timeout=remaining)
        if reply is None:
            return False
        if reply.get("type") == "ACK_JOIN":
            return True
        if reply.get("type") == "ERR":
            raise RuntimeError(f"JOIN ditolak oleh server: {reply.get('data')}")


def _send_leave_safely(
    transport: TcpTransport | UdpTransport,
    user: str,
    seq: int,
) -> None:
    """Mengirim pesan LEAVE sebelum menutup koneksi benchmark."""
    try:
        leave_msg = make_message(msg_type="LEAVE", seq=seq, user=user, data="")
        transport.client_send(leave_msg)
    except Exception:
        pass


def run_benchmark(
    proto: str,
    host: str,
    port: int,
    user: str,
    mode: str,
    role: str | None = None,
    count: int = 200,
    size: int = 64,
    interval: float = 0.0,
    scenario: str = "S1",
    run_id: int = 1,
    out_dir: str | Path = "results",
    receiver_idle_timeout: float = 3.0,
) -> dict[str, Any]:
    """
    Menjalankan satu sesi benchmark (`ping`, `msg sender`, atau `msg receiver`)
    dan mengembalikan dictionary ringkasan metrik serta path file CSV yang dihasilkan.
    """
    proto = proto.strip().lower()
    mode = mode.strip().lower()
    suffix = "ping" if mode == "ping" else (role or "sender").strip().lower()

    out_path = Path(out_dir)
    os.makedirs(out_path, exist_ok=True)
    csv_filename = f"bench_{proto}_{scenario}_run{run_id}_{suffix}.csv"
    csv_filepath = out_path / csv_filename

    logger = logging.getLogger(f"DualChat.Bench.{proto.upper()}.{suffix.upper()}")
    transport = create_transport(proto=proto, logger=logger)

    # Statistik metrik
    sent_count = 0
    recv_count = 0
    lost_count = 0
    ooo_count = 0
    dup_count = 0
    total_bytes = 0
    rtts_ms: list[float] = []

    with CsvLogger(filepath=csv_filepath, columns=BENCH_CSV_COLUMNS) as csv_logger:
        try:
            joined = _perform_join(
                transport=transport,
                proto=proto,
                host=host,
                port=port,
                user=user,
                join_timeout=2.0,
            )
            if not joined:
                raise RuntimeError(f"Gagal JOIN ke server {host}:{port} ({proto.upper()})")

            t_start = time.time()

            if mode == "ping":
                # ---------------------------------------------------------
                # MODE PING: Kirim COUNT PING berurutan, tunggu PONG <= 2s
                # ---------------------------------------------------------
                for seq in range(count):
                    t_send = time.time()
                    # Spesifikasi protokol: data kosong untuk PING/PONG/LEAVE
                    ping_msg = make_message(
                        msg_type="PING",
                        seq=seq,
                        user=user,
                        data="",
                        ts=t_send,
                    )
                    wire_size = transport.client_send(ping_msg)
                    sent_count += 1

                    # Tunggu PONG yang sesuai dengan seq ini maksimal 2.0 detik
                    deadline = time.monotonic() + 2.0
                    matched_pong: dict[str, Any] | None = None
                    t_recv: float = 0.0

                    while True:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        incoming = transport.client_recv(timeout=remaining)
                        if incoming is None:
                            break
                        if incoming.get("type") == "PONG":
                            in_seq = int(incoming.get("seq", -1))
                            if in_seq == seq:
                                t_recv = time.time()
                                matched_pong = incoming
                                break

                    if matched_pong is not None:
                        recv_count += 1
                        pong_size = len(encode_message(matched_pong))
                        total_bytes += wire_size + pong_size
                        rtt_val = (t_recv - float(matched_pong["ts"])) * 1000.0
                        rtts_ms.append(rtt_val)
                        csv_logger.log_row(
                            {
                                "proto": proto,
                                "scenario": scenario,
                                "run": run_id,
                                "event": "RECV",
                                "type": "PONG",
                                "seq": seq,
                                "size_bytes": pong_size,
                                "t_send": f"{t_send:.6f}",
                                "t_recv": f"{t_recv:.6f}",
                                "rtt_ms": f"{rtt_val:.4f}",
                            }
                        )
                    else:
                        # PONG tidak datang dalam 2 detik -> catat TIMEOUT tanpa retry
                        lost_count += 1
                        csv_logger.log_row(
                            {
                                "proto": proto,
                                "scenario": scenario,
                                "run": run_id,
                                "event": "TIMEOUT",
                                "type": "PING",
                                "seq": seq,
                                "size_bytes": wire_size,
                                "t_send": f"{t_send:.6f}",
                                "t_recv": "",
                                "rtt_ms": "",
                            }
                        )

                    if interval > 0 and seq < count - 1:
                        time.sleep(interval)

                t_end = time.time()
                _send_leave_safely(transport, user=user, seq=count)

            elif mode == "msg" and suffix == "sender":
                # ---------------------------------------------------------
                # MODE MSG - SENDER: Kirim COUNT MSG dengan seq 0..COUNT-1
                # Payload deterministik: 'x' * size
                # ---------------------------------------------------------
                payload = "x" * size
                for seq in range(count):
                    t_send = time.time()
                    msg_dict = make_message(
                        msg_type="MSG",
                        seq=seq,
                        user=user,
                        data=payload,
                        ts=t_send,
                    )
                    sent_bytes = transport.client_send(msg_dict)
                    sent_count += 1
                    total_bytes += sent_bytes

                    csv_logger.log_row(
                        {
                            "proto": proto,
                            "scenario": scenario,
                            "run": run_id,
                            "event": "SEND",
                            "type": "MSG",
                            "seq": seq,
                            "size_bytes": sent_bytes,
                            "t_send": f"{t_send:.6f}",
                            "t_recv": "",
                            "rtt_ms": "",
                        }
                    )

                    if interval > 0 and seq < count - 1:
                        time.sleep(interval)

                t_end = time.time()
                _send_leave_safely(transport, user=user, seq=count)

            elif mode == "msg" and suffix == "receiver":
                # ---------------------------------------------------------
                # MODE MSG - RECEIVER: Catat setiap MSG sampai idle 3 detik
                # ---------------------------------------------------------
                seen_seqs: set[int] = set()
                max_seq_seen: int = -1
                first_recv_time: float | None = None
                last_recv_time: float | None = None

                while True:
                    incoming = transport.client_recv(timeout=receiver_idle_timeout)
                    if incoming is None:
                        # Tidak ada pesan baru selama receiver_idle_timeout (default 3 detik)
                        break

                    if incoming.get("type") != "MSG":
                        continue

                    # Abaikan pesan sistem dari server (mis. "<user> bergabung" / "keluar")
                    sender_user = incoming.get("user", "")
                    if sender_user == "server":
                        continue

                    t_recv = time.time()
                    if first_recv_time is None:
                        first_recv_time = t_recv
                    last_recv_time = t_recv

                    seq = int(incoming["seq"])
                    t_send = float(incoming["ts"])
                    msg_size = len(encode_message(incoming))
                    one_way_ms = (t_recv - t_send) * 1000.0

                    recv_count += 1
                    total_bytes += msg_size

                    if seq in seen_seqs:
                        dup_count += 1
                    else:
                        if seq < max_seq_seen:
                            ooo_count += 1
                        else:
                            max_seq_seen = seq
                        seen_seqs.add(seq)

                    csv_logger.log_row(
                        {
                            "proto": proto,
                            "scenario": scenario,
                            "run": run_id,
                            "event": "RECV",
                            "type": "MSG",
                            "seq": seq,
                            "size_bytes": msg_size,
                            "t_send": f"{t_send:.6f}",
                            "t_recv": f"{t_recv:.6f}",
                            "rtt_ms": f"{one_way_ms:.4f}",
                        }
                    )

                # Hitung jumlah pesan yang hilang berdasarkan ekspektasi count
                unique_received = len(seen_seqs)
                lost_count = max(0, count - unique_received)

                if first_recv_time is not None and last_recv_time is not None:
                    t_start = first_recv_time
                    t_end = max(last_recv_time, first_recv_time + 1e-6)
                else:
                    t_end = time.time()

                _send_leave_safely(transport, user=user, seq=1)

            else:
                raise ValueError(f"Kombinasi mode={mode!r} dan role={role!r} tidak valid")

        finally:
            transport.close()

    duration_sec = max(1e-6, t_end - t_start)
    throughput_bps = total_bytes / duration_sec

    rtt_avg = statistics.mean(rtts_ms) if rtts_ms else 0.0
    rtt_min = min(rtts_ms) if rtts_ms else 0.0
    rtt_max = max(rtts_ms) if rtts_ms else 0.0
    rtt_std = statistics.stdev(rtts_ms) if len(rtts_ms) > 1 else 0.0

    summary: dict[str, Any] = {
        "proto": proto,
        "mode": mode,
        "role": suffix,
        "scenario": scenario,
        "run": run_id,
        "sent": sent_count,
        "received": recv_count,
        "lost": lost_count,
        "out_of_order": ooo_count,
        "duplicate": dup_count,
        "rtt_avg_ms": rtt_avg,
        "rtt_min_ms": rtt_min,
        "rtt_max_ms": rtt_max,
        "rtt_stddev_ms": rtt_std,
        "duration_sec": duration_sec,
        "total_bytes": total_bytes,
        "throughput_bps": throughput_bps,
        "csv_path": str(csv_filepath),
    }

    _print_summary(summary)
    return summary


def _print_summary(summary: dict[str, Any]) -> None:
    """Mencetak ringkasan hasil benchmark ke layar konsol."""
    print("========================================================")
    print(
        f" RINGKASAN BENCHMARK | Proto: {summary['proto'].upper()} | "
        f"Mode: {summary['mode'].upper()} ({summary['role']}) | "
        f"Scenario: {summary['scenario']} (Run {summary['run']})"
    )
    print("--------------------------------------------------------")
    print(f" Terkirim       : {summary['sent']}")
    print(f" Diterima       : {summary['received']}")
    print(f" Hilang         : {summary['lost']}")
    print(f" Out-of-order   : {summary['out_of_order']}")
    print(f" Duplikat       : {summary['duplicate']}")
    if summary["mode"] == "ping":
        print(
            f" RTT (ms)       : avg={summary['rtt_avg_ms']:.3f} | "
            f"min={summary['rtt_min_ms']:.3f} | "
            f"max={summary['rtt_max_ms']:.3f} | "
            f"stddev={summary['rtt_stddev_ms']:.3f}"
        )
    print(f" Durasi         : {summary['duration_sec']:.4f} detik")
    print(f" Throughput     : {summary['throughput_bps']:.2f} byte/detik")
    print(f" File CSV       : {summary['csv_path']}")
    print("========================================================")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Mem-parsing argumen CLI untuk benchmark client."""
    parser = argparse.ArgumentParser(
        description="Benchmark Client Otomatis DualChat (TCP vs UDP)"
    )
    parser.add_argument(
        "--proto",
        required=True,
        choices=["tcp", "udp"],
        help="Protokol transport: tcp atau udp",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Alamat IP server (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Port server (default: {DEFAULT_TCP_PORT} untuk TCP, {DEFAULT_UDP_PORT} untuk UDP)",
    )
    parser.add_argument(
        "--user",
        default=None,
        help="Username untuk client benchmark (default dibuat otomatis sesuai role/mode)",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=["msg", "ping"],
        help="Mode benchmark: msg atau ping",
    )
    parser.add_argument(
        "--role",
        choices=["sender", "receiver"],
        default="sender",
        help="Peran untuk mode msg: sender atau receiver (default: sender)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=200,
        help="Jumlah pesan yang dikirim/diharapkan (default: 200)",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=64,
        help="Ukuran payload data dalam byte untuk mode msg (default: 64)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.0,
        help="Jeda antar pengiriman pesan dalam detik (default: 0.0)",
    )
    parser.add_argument(
        "--scenario",
        default="S1",
        help="Label skenario pengujian, misal S1, S2 (default: S1)",
    )
    parser.add_argument(
        "--run",
        type=int,
        default=1,
        help="Nomor ulangan eksperimen (default: 1)",
    )
    parser.add_argument(
        "--out-dir",
        default="results/",
        help="Direktori output file CSV (default: results/)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Tampilkan log debug di konsol",
    )

    args = parser.parse_args(argv)
    if args.port is None:
        args.port = DEFAULT_TCP_PORT if args.proto == "tcp" else DEFAULT_UDP_PORT
    if args.user is None:
        role_label = "ping" if args.mode == "ping" else args.role
        args.user = f"bench_{args.proto}_{role_label}"
    return args


def main(argv: list[str] | None = None) -> int:
    """Entry point utama untuk menjalankan benchmark dari CLI."""
    args = parse_args(argv)
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        run_benchmark(
            proto=args.proto,
            host=args.host,
            port=args.port,
            user=args.user,
            mode=args.mode,
            role=args.role,
            count=args.count,
            size=args.size,
            interval=args.interval,
            scenario=args.scenario,
            run_id=args.run,
            out_dir=args.out_dir,
        )
    except Exception as exc:
        print(f"[ERROR BENCHMARK] {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
