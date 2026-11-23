"""
web_ui.py - Full Web UI/UX Dashboard & Backend Bridge untuk aplikasi DualChat.

Mengintegrasikan seluruh modul backend yang sudah dirancang:
- `chat_core.py`  (encode/decode, StreamFramer, CsvLogger, ChatServerEngine)
- `transport.py`  (TcpTransport SOCK_STREAM & UdpTransport SOCK_DGRAM asli)
- `server.py`     (ProtocolServerWorker untuk TCP dan UDP)
- `benchmark.py`  (run_benchmark untuk mode PING dan MSG)

Mengapa arsitektur Bridge ini penting untuk tugas Jaringan Komputer?
Browser modern hanya dapat berkomunikasi melalui HTTP/WebSocket. Agar paket yang
lewat di jaringan (loopback 127.0.0.1 maupun LAN) TETAP berupa paket raw TCP
(dengan delimiter '\\n') dan raw UDP datagram murni yang bisa dianalisis di Wireshark,
modul ini menjalankan HTTP server lokal (default port 8080) yang mengendalikan objek
`TcpTransport` dan `UdpTransport` asli di sisi Python.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from benchmark import run_benchmark
from chat_core import (
    DEFAULT_HOST,
    DEFAULT_TCP_PORT,
    DEFAULT_UDP_PORT,
    StreamFramer,
    decode_message,
    encode_message,
    make_message,
)
from server import ProtocolServerWorker
from transport import TcpTransport, UdpTransport, create_transport


# =====================================================================
# MANAJER SESI CLIENT VIRTUAL UNTUK UI INTERAKTIF
# =====================================================================

class ManagedChatClient:
    """
    Representasi satu client chat aktif (TCP atau UDP) yang dikendalikan dari Web UI.
    Menggunakan `TcpTransport` atau `UdpTransport` asli sehingga semua paket
    benar-benar dikirim melalui socket OS (terlihat di Wireshark).
    """

    def __init__(self, client_id: str, proto: str, host: str, port: int, user: str) -> None:
        self.client_id = client_id
        self.proto = proto.strip().lower()
        self.host = host
        self.port = port
        self.user = user

        self.transport: TcpTransport | UdpTransport = create_transport(self.proto)
        self.connected = False
        self.seq = 0
        self.last_rtt_ms: float | None = None
        self.messages: list[dict[str, Any]] = []
        self.warnings: list[str] = []
        self._expected_seq_by_sender: dict[str, int] = {}

        self.packets_sent: int = 0
        self.packets_recv: int = 0
        self.bytes_sent: int = 0
        self.bytes_recv: int = 0
        self.out_of_order_count: int = 0

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._recv_thread: threading.Thread | None = None
        self._pending_pings: dict[int, float] = {}

    def _next_seq(self) -> int:
        with self._lock:
            s = self.seq
            self.seq += 1
            return s

    def start_and_join(self, timeout: float = 2.0) -> tuple[bool, str]:
        """Menghubungkan socket ke server dan mengirim pesan JOIN."""
        try:
            self.transport.client_connect(self.host, self.port)
        except OSError as exc:
            return False, f"Gagal connect ke {self.host}:{self.port}: {exc}"

        join_seq = self._next_seq()
        join_msg = make_message("JOIN", join_seq, self.user, "")
        try:
            self.transport.client_send(join_msg)
        except OSError as exc:
            self.transport.close()
            return False, f"Gagal mengirim JOIN: {exc}"

        deadline = time.monotonic() + timeout
        while True:
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0.0:
                self.transport.close()
                return False, "JOIN gagal/hilang (timeout menunggu ACK_JOIN)"

            reply = self.transport.client_recv(timeout=remaining)
            if reply is None:
                self.transport.close()
                return False, "JOIN gagal/hilang"

            mtype = reply.get("type")
            if mtype == "ACK_JOIN":
                self.connected = True
                self._record_event(
                    direction="IN",
                    msg_type="ACK_JOIN",
                    sender="server",
                    seq=int(reply.get("seq", 0)),
                    data=str(reply.get("data", "Bergabung")),
                    raw_msg=reply,
                )
                self._stop_event.clear()
                self._recv_thread = threading.Thread(
                    target=self._recv_loop,
                    name=f"UIClientRecv-{self.client_id}",
                    daemon=True,
                )
                self._recv_thread.start()
                return True, str(reply.get("data", "JOIN berhasil"))

            if mtype == "ERR":
                self.transport.close()
                return False, str(reply.get("data", "JOIN ditolak server"))

    def _record_event(
        self,
        direction: str,
        msg_type: str,
        sender: str,
        seq: int,
        data: str,
        raw_msg: dict[str, Any],
        rtt_ms: float | None = None,
        warning: str | None = None,
    ) -> None:
        raw_bytes = encode_message(raw_msg)
        wire_preview = (
            raw_bytes.decode("utf-8", errors="replace") + "\\n"
            if self.proto == "tcp"
            else raw_bytes.decode("utf-8", errors="replace")
        )
        wire_size = len(raw_bytes) + (1 if self.proto == "tcp" else 0)

        entry = {
            "ts": time.time(),
            "direction": direction,
            "proto": self.proto,
            "type": msg_type,
            "sender": sender,
            "seq": seq,
            "data": data,
            "rtt_ms": round(rtt_ms, 3) if rtt_ms is not None else None,
            "warning": warning,
            "wire_size": wire_size,
            "wire_preview": wire_preview,
        }
        with self._lock:
            if direction == "OUT":
                self.packets_sent += 1
                self.bytes_sent += wire_size
            elif direction == "IN":
                self.packets_recv += 1
                self.bytes_recv += wire_size

            if warning:
                self.out_of_order_count += 1

            self.messages.append(entry)
            if len(self.messages) > 200:
                self.messages = self.messages[-200:]

    def _recv_loop(self) -> None:
        while not self._stop_event.is_set() and self.connected:
            msg = self.transport.client_recv(timeout=0.25)
            if msg is None:
                continue

            mtype = str(msg.get("type", ""))
            sender = str(msg.get("user", "unknown"))
            seq = int(msg.get("seq", 0))
            data = str(msg.get("data", ""))

            if mtype == "MSG":
                warn_text: str | None = None
                if sender != "server":
                    expected = self._expected_seq_by_sender.get(sender)
                    if expected is not None and seq != expected:
                        warn_text = (
                            f"pesan hilang/out-of-order dari [{sender}]: "
                            f"diharapkan seq={expected}, diterima seq={seq}"
                        )
                        with self._lock:
                            self.warnings.append(warn_text)
                    self._expected_seq_by_sender[sender] = seq + 1

                self._record_event(
                    direction="IN",
                    msg_type="MSG",
                    sender=sender,
                    seq=seq,
                    data=data,
                    raw_msg=msg,
                    warning=warn_text,
                )

            elif mtype == "PONG":
                rtt_ms = (time.time() - float(msg["ts"])) * 1000.0
                with self._lock:
                    self.last_rtt_ms = rtt_ms
                self._record_event(
                    direction="IN",
                    msg_type="PONG",
                    sender="server",
                    seq=seq,
                    data=f"PONG (RTT: {rtt_ms:.3f} ms)",
                    raw_msg=msg,
                    rtt_ms=rtt_ms,
                )

            elif mtype == "ERR":
                self._record_event(
                    direction="IN",
                    msg_type="ERR",
                    sender="server",
                    seq=seq,
                    data=data,
                    raw_msg=msg,
                )

    def send_chat(self, text: str) -> dict[str, Any]:
        """Mengirim pesan MSG dari client ini ke server."""
        if not self.connected:
            raise RuntimeError("Client sudah tidak terhubung")
        seq = self._next_seq()
        msg = make_message("MSG", seq, self.user, text)
        self.transport.client_send(msg)
        self._record_event(
            direction="OUT",
            msg_type="MSG",
            sender=self.user,
            seq=seq,
            data=text,
            raw_msg=msg,
        )
        return msg

    def send_ping(self) -> dict[str, Any]:
        """Mengirim pesan PING ke server untuk mengukur RTT."""
        if not self.connected:
            raise RuntimeError("Client sudah tidak terhubung")
        seq = self._next_seq()
        msg = make_message("PING", seq, self.user, "")
        self.transport.client_send(msg)
        self._record_event(
            direction="OUT",
            msg_type="PING",
            sender=self.user,
            seq=seq,
            data="PING request",
            raw_msg=msg,
        )
        return msg

    def leave_and_close(self) -> None:
        """Mengirim LEAVE dan menutup socket client."""
        if self.connected:
            try:
                seq = self._next_seq()
                leave_msg = make_message("LEAVE", seq, self.user, "")
                self.transport.client_send(leave_msg)
            except Exception:
                pass
        self.connected = False
        self._stop_event.set()
        self.transport.close()
        if self._recv_thread is not None and self._recv_thread.is_alive():
            if threading.current_thread() is not self._recv_thread:
                self._recv_thread.join(timeout=0.8)

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "client_id": self.client_id,
                "proto": self.proto,
                "host": self.host,
                "port": self.port,
                "user": self.user,
                "connected": self.connected,
                "seq": self.seq,
                "last_rtt_ms": round(self.last_rtt_ms, 3) if self.last_rtt_ms is not None else None,
                "packets_sent": self.packets_sent,
                "packets_recv": self.packets_recv,
                "bytes_sent": self.bytes_sent,
                "bytes_recv": self.bytes_recv,
                "out_of_order_count": self.out_of_order_count,
                "messages": list(self.messages[-50:]),
                "warnings": list(self.warnings[-10:]),
            }


# =====================================================================
# CONTROLLER BACKEND DASHBOARD (SERVER + CLIENTS + BENCHMARK)
# =====================================================================

class DualChatDashboardController:
    """
    Orkestrator utama yang menghubungkan Web UI dengan:
    1. Server TCP & UDP (`ProtocolServerWorker`)
    2. Daftar Client Interaktif (`ManagedChatClient`)
    3. Benchmark Runner (`run_benchmark`) & Pembaca CSV (`results/`)
    """

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        tcp_port: int = DEFAULT_TCP_PORT,
        udp_port: int = DEFAULT_UDP_PORT,
        log_dir: str | Path = "results",
        auto_start_servers: bool = True,
    ) -> None:
        self.host = host
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.log_dir = Path(log_dir)
        os.makedirs(self.log_dir, exist_ok=True)

        self._lock = threading.Lock()
        self.tcp_server: ProtocolServerWorker | None = None
        self.udp_server: ProtocolServerWorker | None = None
        self.clients: dict[str, ManagedChatClient] = {}
        self.benchmark_history: list[dict[str, Any]] = []
        self.benchmark_running = False

        if auto_start_servers:
            self.start_servers()

    def start_servers(self) -> dict[str, Any]:
        """Menjalankan server TCP dan UDP jika belum aktif."""
        with self._lock:
            if self.tcp_server is None:
                try:
                    srv = ProtocolServerWorker(
                        proto="tcp",
                        host=self.host,
                        port=self.tcp_port,
                        log_dir=self.log_dir,
                    )
                    srv.start()
                    self.tcp_server = srv
                    self.tcp_port = srv.bound_port
                except OSError as err:
                    logging.warning("Port TCP %s sudah dipakai (server eksternal berjalan): %s", self.tcp_port, err)
                    self.tcp_server = None

            if self.udp_server is None:
                try:
                    srv = ProtocolServerWorker(
                        proto="udp",
                        host=self.host,
                        port=self.udp_port,
                        log_dir=self.log_dir,
                    )
                    srv.start()
                    self.udp_server = srv
                    self.udp_port = srv.bound_port
                except OSError as err:
                    logging.warning("Port UDP %s sudah dipakai (server eksternal berjalan): %s", self.udp_port, err)
                    self.udp_server = None

        return self.get_status()

    def stop_all(self) -> None:
        """Menghentikan semua client aktif serta server TCP dan UDP."""
        with self._lock:
            client_list = list(self.clients.values())
            self.clients.clear()
            tcp_srv = self.tcp_server
            udp_srv = self.udp_server
            self.tcp_server = None
            self.udp_server = None

        for c in client_list:
            c.leave_and_close()
        if tcp_srv is not None:
            tcp_srv.stop()
        if udp_srv is not None:
            udp_srv.stop()

    def get_status(self) -> dict[str, Any]:
        """Mengambil snapshot status server, daftar client, dan benchmark."""
        with self._lock:
            tcp_active = self.tcp_server is not None
            udp_active = self.udp_server is not None
            tcp_users = self.tcp_server.engine.active_users() if self.tcp_server else []
            udp_users = self.udp_server.engine.active_users() if self.udp_server else []
            clients_data = [c.to_dict() for c in self.clients.values()]
            bench_hist = list(self.benchmark_history[-20:])

        return {
            "host": self.host,
            "tcp_server": {
                "running": tcp_active,
                "port": self.tcp_port,
                "active_users": tcp_users,
            },
            "udp_server": {
                "running": udp_active,
                "port": self.udp_port,
                "active_users": udp_users,
            },
            "clients": clients_data,
            "benchmark_running": self.benchmark_running,
            "benchmark_history": bench_hist,
            "csv_files": self.list_csv_files(),
        }

    def create_client(self, proto: str, user: str, host: str | None = None, port: int | None = None) -> tuple[bool, Any]:
        """Membuat client baru dan melakukan JOIN ke server."""
        proto = proto.strip().lower()
        user = user.strip()
        if not user:
            return False, "Username tidak boleh kosong"

        target_host = host or self.host
        target_port = port or (self.tcp_port if proto == "tcp" else self.udp_port)
        client_id = f"{proto}_{user}"

        with self._lock:
            if client_id in self.clients and self.clients[client_id].connected:
                return False, f"Client '{user}' pada {proto.upper()} sudah aktif"

        client = ManagedChatClient(
            client_id=client_id,
            proto=proto,
            host=target_host,
            port=int(target_port),
            user=user,
        )
        ok, msg = client.start_and_join(timeout=2.0)
        if not ok:
            return False, msg

        with self._lock:
            self.clients[client_id] = client

        return True, client.to_dict()

    def remove_client(self, client_id: str) -> bool:
        """Mengeluarkan client (LEAVE) dan menghapusnya dari daftar UI."""
        with self._lock:
            client = self.clients.pop(client_id, None)
        if client is None:
            return False
        client.leave_and_close()
        return True

    def client_action(self, client_id: str, action: str, text: str = "") -> tuple[bool, Any]:
        """Menjalankan aksi pada client tertentu: 'msg', 'ping', atau 'leave'."""
        with self._lock:
            client = self.clients.get(client_id)
        if client is None:
            return False, f"Client {client_id} tidak ditemukan"

        try:
            if action == "msg":
                client.send_chat(text)
                return True, client.to_dict()
            if action == "ping":
                client.send_ping()
                # Tunggu secara dinamis hingga PONG diterima (maks 200ms)
                for _ in range(20):
                    time.sleep(0.01)
                    if client.last_rtt_ms is not None:
                        break
                return True, client.to_dict()
            if action == "leave":
                self.remove_client(client_id)
                return True, {"removed": client_id}
            return False, f"Aksi tidak dikenal: {action}"
        except Exception as exc:
            return False, str(exc)

    def run_benchmark_suite(
        self,
        proto_mode: str,
        mode: str,
        count: int,
        size: int,
        interval: float,
        scenario: str,
        run_id: int,
    ) -> dict[str, Any]:
        """
        Menjalankan benchmark otomatis dari UI:
        - `proto_mode`: 'tcp' | 'udp' | 'both' (head-to-head comparison)
        - `mode`: 'ping' | 'msg'
        Pada mode 'msg', fungsi ini secara otomatis menyiapkan thread `receiver`
        lalu menjalankan `sender` sehingga pengguna cukup menekan 1 tombol di UI.
        """
        with self._lock:
            if self.benchmark_running:
                raise RuntimeError("Benchmark lain sedang berjalan, harap tunggu.")
            self.benchmark_running = True

        protos = ["tcp", "udp"] if proto_mode == "both" else [proto_mode.lower()]
        results: list[dict[str, Any]] = []

        try:
            for p in protos:
                port = self.tcp_port if p == "tcp" else self.udp_port
                if mode == "ping":
                    summary = run_benchmark(
                        proto=p,
                        host=self.host,
                        port=port,
                        user=f"ui_ping_{p}_{int(time.time() * 1000) % 10000}",
                        mode="ping",
                        count=count,
                        size=size,
                        interval=interval,
                        scenario=scenario,
                        run_id=run_id,
                        out_dir=self.log_dir,
                    )
                    summary["series"] = self._extract_csv_series(summary["csv_path"])
                    results.append(summary)
                else:
                    # Mode MSG: jalankan receiver di background thread, lalu sender
                    recv_box: dict[str, Any] = {}
                    recv_started = threading.Event()
                    ts_tag = int(time.time() * 1000) % 10000

                    def _recv_job(proto_name: str, target_port: int) -> None:
                        recv_started.set()
                        try:
                            res = run_benchmark(
                                proto=proto_name,
                                host=self.host,
                                port=target_port,
                                user=f"ui_recv_{proto_name}_{ts_tag}",
                                mode="msg",
                                role="receiver",
                                count=count,
                                size=size,
                                scenario=scenario,
                                run_id=run_id,
                                out_dir=self.log_dir,
                                receiver_idle_timeout=0.8,
                            )
                            recv_box["summary"] = res
                        except Exception as exc:
                            recv_box["error"] = str(exc)

                    r_thread = threading.Thread(target=_recv_job, args=(p, port), daemon=True)
                    r_thread.start()
                    recv_started.wait(timeout=1.0)
                    time.sleep(0.12)

                    send_summary = run_benchmark(
                        proto=p,
                        host=self.host,
                        port=port,
                        user=f"ui_send_{p}_{ts_tag}",
                        mode="msg",
                        role="sender",
                        count=count,
                        size=size,
                        interval=interval,
                        scenario=scenario,
                        run_id=run_id,
                        out_dir=self.log_dir,
                    )
                    r_thread.join(timeout=8.0)

                    recv_summary = recv_box.get("summary", {})
                    combined = {
                        "proto": p,
                        "mode": "msg",
                        "role": "sender+receiver",
                        "scenario": scenario,
                        "run": run_id,
                        "sent": send_summary.get("sent", count),
                        "received": recv_summary.get("received", 0),
                        "lost": recv_summary.get("lost", 0),
                        "out_of_order": recv_summary.get("out_of_order", 0),
                        "duplicate": recv_summary.get("duplicate", 0),
                        "rtt_avg_ms": 0.0,
                        "rtt_min_ms": 0.0,
                        "rtt_max_ms": 0.0,
                        "rtt_stddev_ms": 0.0,
                        "duration_sec": recv_summary.get("duration_sec", send_summary["duration_sec"]),
                        "total_bytes": recv_summary.get("total_bytes", send_summary["total_bytes"]),
                        "throughput_bps": recv_summary.get("throughput_bps", send_summary["throughput_bps"]),
                        "csv_path": recv_summary.get("csv_path", send_summary["csv_path"]),
                        "sender_csv_path": send_summary["csv_path"],
                        "series": self._extract_csv_series(
                            recv_summary.get("csv_path", send_summary["csv_path"])
                        ),
                    }
                    results.append(combined)

            with self._lock:
                self.benchmark_history.extend(results)

            return {"results": results}
        finally:
            with self._lock:
                self.benchmark_running = False

    def _extract_csv_series(self, csv_path: str | Path, max_points: int = 200) -> list[dict[str, Any]]:
        """Membaca data baris dari file CSV benchmark untuk digambar pada grafik UI."""
        path = Path(csv_path)
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        try:
            with open(path, mode="r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    rows.append(
                        {
                            "seq": int(row.get("seq", 0) or 0),
                            "event": row.get("event", ""),
                            "size_bytes": int(row.get("size_bytes", 0) or 0),
                            "rtt_ms": float(row["rtt_ms"]) if row.get("rtt_ms") else None,
                        }
                    )
                    if len(rows) >= max_points:
                        break
        except Exception:
            pass
        return rows

    def list_csv_files(self) -> list[dict[str, Any]]:
        """Mengembalikan daftar file CSV yang ada di direktori results/."""
        if not self.log_dir.exists():
            return []
        files_info: list[dict[str, Any]] = []
        for p in sorted(self.log_dir.glob("*.csv"), key=lambda x: x.stat().st_mtime, reverse=True):
            stat = p.stat()
            files_info.append(
                {
                    "name": p.name,
                    "size_bytes": stat.st_size,
                    "modified": stat.st_mtime,
                }
            )
        return files_info

    def read_csv_content(self, filename: str, limit: int = 100) -> dict[str, Any]:
        """Membaca isi file CSV tertentu di folder results/ untuk ditampilkan di tabel UI."""
        safe_name = Path(filename).name
        target = self.log_dir / safe_name
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"File CSV tidak ditemukan: {safe_name}")

        with open(target, mode="r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            headers = next(reader, [])
            rows: list[list[str]] = []
            total_rows = 0
            for r in reader:
                total_rows += 1
                if len(rows) < limit:
                    rows.append(r)

        return {
            "filename": safe_name,
            "headers": headers,
            "rows": rows,
            "total_rows": total_rows,
        }

    @staticmethod
    def simulate_framing(text: str, user: str = "alice", seq: int = 1, split_at: int = 25) -> dict[str, Any]:
        """
        Menyimulasikan bagaimana pesan di-encode dan di-frame pada TCP vs UDP
        (fitur edukasi interaktif untuk laporan Jaringan Komputer).
        """
        msg = make_message("MSG", seq, user, text)
        raw_bytes = encode_message(msg)
        tcp_wire = raw_bytes + b"\n"
        udp_wire = raw_bytes

        # Simulasikan pemotongan chunk di TCP menggunakan StreamFramer asli
        split_idx = max(1, min(len(tcp_wire) - 1, int(split_at)))
        chunk_1 = tcp_wire[:split_idx]
        chunk_2 = tcp_wire[split_idx:]

        framer = StreamFramer()
        out_after_chunk1 = framer.feed(chunk_1)
        out_after_chunk2 = framer.feed(chunk_2)

        decoded_final, _ = decode_message(out_after_chunk2[0]) if out_after_chunk2 else (None, None)

        return {
            "msg_dict": msg,
            "json_utf8_len": len(raw_bytes),
            "tcp": {
                "wire_bytes_len": len(tcp_wire),
                "wire_repr": tcp_wire.decode("utf-8", errors="replace").replace("\n", "\\n"),
                "header_overhead_bytes": 20,  # Header TCP standar minimum 20 byte
                "chunk_1_repr": chunk_1.decode("utf-8", errors="replace").replace("\n", "\\n"),
                "chunk_2_repr": chunk_2.decode("utf-8", errors="replace").replace("\n", "\\n"),
                "frames_after_chunk_1": len(out_after_chunk1),
                "frames_after_chunk_2": len(out_after_chunk2),
                "reassembled_ok": decoded_final == msg,
            },
            "udp": {
                "wire_bytes_len": len(udp_wire),
                "wire_repr": udp_wire.decode("utf-8", errors="replace"),
                "header_overhead_bytes": 8,  # Header UDP tetap 8 byte
                "single_datagram": True,
            },
        }


# =====================================================================
# HTML / CSS / JS FRONTEND (SINGLE-PAGE TELEMETRY & CHAT APPLICATION)
# =====================================================================

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="id">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DualChat Telemetry & Protocol Studio</title>
  <style>
    :root {
      --bg-main: #0B0F19;
      --bg-sidebar: #0F172A;
      --bg-card: #1E293B;
      --bg-subtle: #131B2E;
      --border-color: rgba(255, 255, 255, 0.08);
      --tcp-color: #10B981;
      --tcp-glow: rgba(16, 185, 129, 0.18);
      --udp-color: #F59E0B;
      --udp-glow: rgba(245, 158, 11, 0.18);
      --text-main: #FFFFFF;
      --text-muted: #94A3B8;
      --text-dim: #64748B;
      --cyan-accent: #38BDF8;
      --err-color: #EF4444;
      --err-bg: #2E1313;
      --pong-bg: #0F291E;
      --pong-border: rgba(16, 185, 129, 0.35);
      --pong-text: #34D399;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg-main);
      color: var(--text-main);
      font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
      font-size: 13px;
      line-height: 1.4;
      height: 100vh;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }

    /* TOPBAR (FLUTTER APPBAR REPLICA) */
    .appbar {
      background: var(--bg-sidebar);
      height: 52px;
      padding: 0 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--border-color);
      flex-shrink: 0;
    }
    .appbar-left {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .proto-badge {
      padding: 3px 10px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 800;
      letter-spacing: 1.2px;
      transition: all 0.2s;
    }
    .proto-badge.tcp {
      background: var(--tcp-glow);
      color: var(--tcp-color);
      border: 1px solid rgba(16, 185, 129, 0.4);
    }
    .proto-badge.udp {
      background: var(--udp-glow);
      color: var(--udp-color);
      border: 1px solid rgba(245, 158, 11, 0.4);
    }
    .appbar-title {
      font-size: 17px;
      font-weight: 600;
      color: #fff;
    }
    .status-tag {
      font-size: 11px;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 4px;
      background: rgba(255, 255, 255, 0.06);
      font-family: Consolas, monospace;
    }
    .status-tag.connected { color: #10B981; }
    .status-tag.connecting { color: #F59E0B; }
    .status-tag.disconnected { color: #64748B; }

    .appbar-right {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .icon-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      width: 34px;
      height: 34px;
      border-radius: 6px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 15px;
      transition: all 0.15s;
    }
    .icon-btn:hover {
      background: rgba(255, 255, 255, 0.08);
      color: #fff;
    }
    .icon-btn.ping-btn { color: var(--cyan-accent); }
    .icon-btn.ping-btn:hover { background: rgba(56, 189, 248, 0.15); }

    /* VIEW SWITCHER TABS */
    .view-tabs {
      background: var(--bg-sidebar);
      display: flex;
      gap: 2px;
      padding: 0 16px;
      border-bottom: 1px solid var(--border-color);
      flex-shrink: 0;
      overflow-x: auto;
    }
    .vtab-btn {
      background: transparent;
      border: none;
      border-bottom: 2px solid transparent;
      color: var(--text-muted);
      padding: 8px 14px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s;
      white-space: nowrap;
    }
    .vtab-btn:hover { color: #fff; }
    .vtab-btn.active {
      color: #fff;
      border-bottom-color: var(--tcp-color);
      background: rgba(255, 255, 255, 0.03);
    }
    .vtab-btn.active.udp-mode { border-bottom-color: var(--udp-color); }

    /* WORKSPACE BODY */
    .workspace-body {
      flex: 1;
      display: flex;
      overflow: hidden;
      position: relative;
    }
    .view-panel {
      display: none;
      width: 100%;
      height: 100%;
    }
    .view-panel.active { display: flex; }

    /* ======================================================== */
    /* FLUTTER CLIENT LAYOUT                                    */
    /* ======================================================== */
    .flutter-layout {
      width: 100%;
      height: 100%;
      display: flex;
    }

    /* SIDEBAR */
    .flutter-sidebar {
      width: 320px;
      background: var(--bg-sidebar);
      border-right: 1px solid var(--border-color);
      display: flex;
      flex-direction: column;
      padding: 16px;
      overflow-y: auto;
      flex-shrink: 0;
      gap: 12px;
    }
    .sidebar-section {
      font-size: 11px;
      font-weight: 700;
      color: var(--text-dim);
      letter-spacing: 1.1px;
      text-transform: uppercase;
    }

    /* Proto Segmented Button */
    .proto-toggle {
      background: var(--bg-card);
      border-radius: 8px;
      padding: 4px;
      display: flex;
      gap: 4px;
    }
    .proto-opt {
      flex: 1;
      border: none;
      background: transparent;
      color: var(--text-muted);
      font-size: 12px;
      font-weight: 500;
      padding: 8px 0;
      border-radius: 6px;
      cursor: pointer;
      transition: all 0.15s;
      text-align: center;
    }
    .proto-opt.active.tcp {
      background: var(--tcp-glow);
      color: var(--tcp-color);
      font-weight: 700;
      border: 1px solid rgba(16, 185, 129, 0.4);
    }
    .proto-opt.active.udp {
      background: var(--udp-glow);
      color: var(--udp-color);
      font-weight: 700;
      border: 1px solid rgba(245, 158, 11, 0.4);
    }

    .form-group {
      display: flex;
      flex-direction: column;
      gap: 5px;
    }
    .form-group label {
      font-size: 12px;
      color: var(--text-muted);
    }
    .flutter-input {
      background: var(--bg-card);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 6px;
      color: #fff;
      padding: 8px 12px;
      font-size: 13px;
      outline: none;
      transition: border-color 0.15s;
    }
    .flutter-input:focus { border-color: var(--cyan-accent); }

    .btn-connect {
      width: 100%;
      height: 40px;
      border: none;
      border-radius: 8px;
      font-size: 13px;
      font-weight: 700;
      color: #fff;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      transition: all 0.15s;
      margin-top: 4px;
    }
    .btn-connect.tcp { background: var(--tcp-color); }
    .btn-connect.tcp:hover { background: #059669; }
    .btn-connect.udp { background: var(--udp-color); color: #000; font-weight: 800; }
    .btn-connect.udp:hover { background: #d97706; color: #fff; }
    .btn-connect.disconnect { background: var(--err-color); color: #fff; }
    .btn-connect.disconnect:hover { background: #dc2626; }

    /* HUD METRIC GRID */
    .hud-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
    }
    .hud-card {
      background: var(--bg-card);
      border: 1px solid rgba(255, 255, 255, 0.06);
      border-radius: 6px;
      padding: 8px 10px;
      display: flex;
      flex-direction: column;
    }
    .hud-label {
      font-size: 10px;
      font-weight: 600;
      color: var(--text-dim);
    }
    .hud-val {
      font-size: 14px;
      font-weight: 700;
      margin-top: 3px;
      font-family: Consolas, monospace;
    }

    .framing-note {
      background: var(--bg-subtle);
      border: 1px solid rgba(255, 255, 255, 0.06);
      border-radius: 6px;
      padding: 10px;
      font-size: 11px;
      margin-top: auto;
    }
    .framing-note-header {
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 6px;
      margin-bottom: 4px;
      color: #fff;
    }
    .framing-note-body {
      color: var(--text-muted);
      font-size: 10.5px;
      line-height: 1.35;
    }

    /* MAIN CHAT CANVAS */
    .flutter-chat {
      flex: 1;
      display: flex;
      flex-direction: column;
      background: var(--bg-main);
      overflow: hidden;
    }
    .chat-scroll {
      flex: 1;
      overflow-y: auto;
      padding: 16px 20px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .empty-chat {
      margin: auto;
      text-align: center;
      color: var(--text-dim);
    }
    .empty-chat .empty-icon {
      font-size: 42px;
      opacity: 0.25;
      margin-bottom: 8px;
    }

    /* Chat message row */
    .msg-row {
      display: flex;
      flex-direction: column;
      max-width: 580px;
    }
    .msg-row.self {
      align-self: flex-end;
      align-items: flex-end;
    }
    .msg-row.peer {
      align-self: flex-start;
      align-items: flex-start;
    }
    .msg-meta-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 3px;
      font-size: 11px;
    }
    .msg-user { font-weight: 700; }
    .msg-user.self.tcp { color: var(--tcp-color); }
    .msg-user.self.udp { color: var(--udp-color); }
    .msg-user.peer { color: var(--cyan-accent); }
    .seq-chip {
      background: rgba(255, 255, 255, 0.08);
      padding: 1px 5px;
      border-radius: 3px;
      font-size: 10px;
      font-family: Consolas, monospace;
      color: var(--text-dim);
    }

    .msg-bubble {
      padding: 10px 14px;
      border-radius: 10px;
      font-size: 13.5px;
      line-height: 1.45;
      word-break: break-word;
    }
    .msg-bubble.self.tcp {
      background: rgba(16, 185, 129, 0.16);
      border: 1px solid rgba(16, 185, 129, 0.4);
      border-bottom-right-radius: 2px;
      color: #fff;
    }
    .msg-bubble.self.udp {
      background: rgba(245, 158, 11, 0.16);
      border: 1px solid rgba(245, 158, 11, 0.4);
      border-bottom-right-radius: 2px;
      color: #fff;
    }
    .msg-bubble.peer {
      background: var(--bg-card);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-bottom-left-radius: 2px;
      color: #fff;
    }

    .pong-card {
      align-self: flex-start;
      background: var(--pong-bg);
      border: 1px solid var(--pong-border);
      color: var(--pong-text);
      padding: 7px 12px;
      border-radius: 8px;
      font-family: Consolas, monospace;
      font-size: 12px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .warn-chip {
      background: rgba(239, 68, 68, 0.16);
      border: 1px solid var(--err-color);
      color: #FCA5A5;
      padding: 4px 8px;
      border-radius: 4px;
      font-family: Consolas, monospace;
      font-size: 11px;
      margin-bottom: 4px;
    }
    .sys-card {
      align-self: center;
      background: var(--bg-subtle);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 6px;
      padding: 6px 12px;
      font-family: Consolas, monospace;
      font-size: 11.5px;
      color: var(--text-muted);
      max-width: 90%;
      text-align: center;
    }
    .sys-card.err {
      background: var(--err-bg);
      border-color: rgba(239, 68, 68, 0.4);
      color: #FCA5A5;
    }

    /* CHAT INPUT BAR */
    .flutter-input-bar {
      background: var(--bg-sidebar);
      padding: 10px 16px;
      display: flex;
      align-items: center;
      gap: 8px;
      border-top: 1px solid var(--border-color);
      flex-shrink: 0;
    }
    .ping-icon-btn {
      background: transparent;
      border: none;
      color: var(--udp-color);
      width: 38px;
      height: 38px;
      border-radius: 6px;
      cursor: pointer;
      font-size: 18px;
      display: flex;
      align-items: center;
      justify-content: center;
      transition: background 0.15s;
    }
    .ping-icon-btn:hover { background: rgba(245, 158, 11, 0.15); }
    .chat-text-input {
      flex: 1;
      background: var(--bg-card);
      border: none;
      border-radius: 8px;
      padding: 10px 14px;
      color: #fff;
      font-size: 13.5px;
      outline: none;
    }
    .send-icon-btn {
      width: 38px;
      height: 38px;
      border-radius: 8px;
      border: none;
      background: var(--tcp-color);
      color: #fff;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 16px;
      transition: all 0.15s;
    }
    .send-icon-btn.udp { background: var(--udp-color); color: #000; }
    .send-icon-btn:disabled { background: #334155; color: #64748B; cursor: not-allowed; }

    /* ======================================================== */
    /* OTHER TABS (BENCHMARK, FRAMING, CSV)                      */
    /* ======================================================== */
    .standard-panel {
      padding: 20px 24px;
      width: 100%;
      height: 100%;
      overflow-y: auto;
      max-width: 1400px;
      margin: 0 auto;
    }
    .card {
      background: var(--bg-sidebar);
      border: 1px solid var(--border-color);
      border-radius: 10px;
      padding: 16px;
      margin-bottom: 16px;
    }
    .card-title {
      font-size: 14px;
      font-weight: 700;
      color: #fff;
      margin-bottom: 12px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    .split-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }
    @media (max-width: 900px) {
      .split-2 { grid-template-columns: 1fr; }
      .flutter-layout { flex-direction: column; }
      .flutter-sidebar { width: 100%; height: auto; }
    }
    .btn-action {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      color: #fff;
      padding: 7px 14px;
      border-radius: 6px;
      font-size: 12px;
      font-weight: 600;
      cursor: pointer;
    }
    .btn-action:hover { background: #26334d; }
    .btn-action.primary { background: var(--tcp-color); border-color: transparent; }
    .table-wrap {
      overflow-x: auto;
      border: 1px solid var(--border-color);
      border-radius: 8px;
    }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    th, td { padding: 8px 12px; border-bottom: 1px solid var(--border-color); font-family: Consolas, monospace; }
    th { background: var(--bg-card); color: var(--text-muted); font-weight: 600; text-align: left; }
    .code-box {
      background: var(--bg-card);
      padding: 12px;
      border-radius: 6px;
      font-family: Consolas, monospace;
      font-size: 12px;
      color: #E2E8F0;
      white-space: pre-wrap;
      word-break: break-all;
    }

    /* MODAL */
    .modal-overlay {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.75);
      display: none;
      align-items: center;
      justify-content: center;
      z-index: 1000;
    }
    .modal-box {
      background: var(--bg-subtle);
      border: 1px solid var(--border-color);
      border-radius: 12px;
      max-width: 550px;
      width: 90%;
      padding: 20px;
    }
    .modal-header {
      font-size: 16px;
      font-weight: 700;
      margin-bottom: 12px;
      color: #fff;
    }
    .modal-body {
      font-size: 12.5px;
      line-height: 1.5;
      color: #CBD5E1;
      max-height: 400px;
      overflow-y: auto;
    }
    .modal-footer {
      display: flex;
      justify-content: flex-end;
      margin-top: 16px;
    }
  </style>
</head>
<body>

  <!-- APPBAR (FLUTTER STYLE) -->
  <header class="appbar">
    <div class="appbar-left">
      <span id="hdr-proto-pill" class="proto-badge tcp">TCP</span>
      <span class="appbar-title">DualChat Telemetry & Protocol Studio</span>
      <span id="hdr-status-tag" class="status-tag disconnected">DISCONNECTED</span>
    </div>
    <div class="appbar-right">
      <button class="icon-btn ping-btn" title="Send PING (/ping)" onclick="handleSendPing()">⚡</button>
      <button class="icon-btn" title="Clear Chat Log" onclick="clearChatLog()">🗑</button>
      <button class="icon-btn" title="Wire Protocol Info" onclick="openModal()">ℹ️</button>
    </div>
  </header>

  <!-- NAVIGATION TABS -->
  <nav class="view-tabs">
    <button class="vtab-btn active" onclick="switchView('tab-flutter', this)">💬 Client Studio (Flutter UI)</button>
    <button class="vtab-btn" onclick="switchView('tab-bench', this)">⚡ Benchmark Lab & Grafik RTT</button>
    <button class="vtab-btn" onclick="switchView('tab-framing', this)">🔍 Wire Framing Visualizer</button>
    <button class="vtab-btn" onclick="switchView('tab-csv', this)">📁 CSV Explorer</button>
  </nav>

  <!-- WORKSPACE BODY -->
  <div class="workspace-body">

    <!-- VIEW 1: FLUTTER CLIENT INTERFACE -->
    <div id="tab-flutter" class="view-panel active">
      <div class="flutter-layout">
        
        <!-- SIDEBAR -->
        <aside class="flutter-sidebar">
          <div class="sidebar-section">NETWORK CONFIGURATION</div>

          <!-- Protocol Tabs -->
          <div class="proto-toggle">
            <button id="opt-tcp" class="proto-opt active tcp" onclick="selectProto('tcp')">TCP (Stream)</button>
            <button id="opt-udp" class="proto-opt" onclick="selectProto('udp')">UDP (Datagram)</button>
          </div>

          <!-- Inputs -->
          <div class="form-group">
            <label>Server Host / IP</label>
            <input type="text" id="cfg-host" class="flutter-input" value="127.0.0.1" />
          </div>

          <div class="form-group">
            <label>Port</label>
            <input type="number" id="cfg-port" class="flutter-input" value="5000" />
          </div>

          <div class="form-group">
            <label>Username</label>
            <input type="text" id="cfg-user" class="flutter-input" value="alice" />
          </div>

          <button id="btn-toggle-connect" class="btn-connect tcp" onclick="toggleConnect()">
            <span id="btn-connect-icon">🔗</span>
            <span id="btn-connect-label">CONNECT & JOIN</span>
          </button>

          <div class="sidebar-section" style="margin-top: 14px;">LIVE TELEMETRY HUD</div>
          <div class="hud-grid">
            <div class="hud-card">
              <span class="hud-label">Packets TX</span>
              <span id="hud-tx-pkts" class="hud-val" style="color:#38BDF8;">0</span>
            </div>
            <div class="hud-card">
              <span class="hud-label">Packets RX</span>
              <span id="hud-rx-pkts" class="hud-val" style="color:#818CF8;">0</span>
            </div>
            <div class="hud-card">
              <span class="hud-label">RTT Terakhir</span>
              <span id="hud-rtt" class="hud-val" style="color:#10B981;">—</span>
            </div>
            <div class="hud-card">
              <span class="hud-label">Out-of-Order / Loss</span>
              <span id="hud-ooo" class="hud-val" style="color:#64748B;">0</span>
            </div>
            <div class="hud-card">
              <span class="hud-label">Bytes TX</span>
              <span id="hud-tx-bytes" class="hud-val" style="color:#94A3B8;">0 B</span>
            </div>
            <div class="hud-card">
              <span class="hud-label">Bytes RX</span>
              <span id="hud-rx-bytes" class="hud-val" style="color:#94A3B8;">0 B</span>
            </div>
          </div>

          <div class="framing-note">
            <div class="framing-note-header">
              <span id="fn-icon">🌐</span>
              <span id="fn-title">TCP Framing (Stream)</span>
            </div>
            <div id="fn-desc" class="framing-note-body">
              Byte-stream kontinu dengan delimiter \n (0x0A). Menggunakan framing buffer StreamFramer.
            </div>
          </div>
        </aside>

        <!-- MAIN CHAT CANVAS -->
        <main class="flutter-chat">
          <div id="chat-feed" class="chat-scroll">
            <div id="empty-hint" class="empty-chat">
              <div class="empty-icon">💬</div>
              <div style="font-size:14px; color:var(--text-muted);">Belum ada pesan chat.</div>
              <div style="font-size:12px; margin-top:4px;">Hubungkan client lalu kirim pesan atau /ping untuk menguji.</div>
            </div>
          </div>

          <div class="flutter-input-bar">
            <button class="ping-icon-btn" title="Quick /ping" onclick="handleSendPing()">⚡</button>
            <input type="text" id="chat-input" class="chat-text-input" placeholder="Ketik pesan chat atau /ping..." onkeydown="if(event.key==='Enter') handleSendChat()" disabled />
            <button id="btn-send-chat" class="send-icon-btn" onclick="handleSendChat()" disabled>→</button>
          </div>
        </main>

      </div>
    </div>

    <!-- VIEW 2: BENCHMARK LAB -->
    <div id="tab-bench" class="view-panel">
      <div class="standard-panel">
        <div class="card">
          <div class="card-title">⚡ Workload Benchmark Deterministik</div>
          <div style="display:flex; gap:12px; flex-wrap:wrap; align-items:center;">
            <div>
              <label style="font-size:11px; color:var(--text-dim); display:block;">Protokol</label>
              <select id="bm-proto" class="flutter-input" style="padding:6px 10px;">
                <option value="both" selected>Head-to-Head (TCP & UDP)</option>
                <option value="tcp">TCP Saja</option>
                <option value="udp">UDP Saja</option>
              </select>
            </div>
            <div>
              <label style="font-size:11px; color:var(--text-dim); display:block;">Mode</label>
              <select id="bm-mode" class="flutter-input" style="padding:6px 10px;">
                <option value="ping" selected>Mode PING (RTT)</option>
                <option value="msg">Mode MSG (Throughput)</option>
              </select>
            </div>
            <div>
              <label style="font-size:11px; color:var(--text-dim); display:block;">Pesan (--count)</label>
              <input type="number" id="bm-count" class="flutter-input" value="50" style="width:90px;" />
            </div>
            <div>
              <label style="font-size:11px; color:var(--text-dim); display:block;">Ukuran Byte (--size)</label>
              <input type="number" id="bm-size" class="flutter-input" value="128" style="width:90px;" />
            </div>
            <div>
              <label style="font-size:11px; color:var(--text-dim); display:block;">Interval s</label>
              <input type="number" id="bm-interval" class="flutter-input" value="0.002" step="0.001" style="width:90px;" />
            </div>
            <div>
              <label style="font-size:11px; color:var(--text-dim); display:block;">Skenario</label>
              <input type="text" id="bm-scenario" class="flutter-input" value="S1" style="width:80px;" />
            </div>
            <button id="btn-run-bm" class="btn-action primary" onclick="triggerBenchmark()" style="margin-top:16px;">▶ Jalankan Benchmark</button>
          </div>
        </div>

        <div class="card">
          <div class="card-title">📊 Hasil Pengujian Terakhir</div>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Proto</th>
                  <th>Mode</th>
                  <th>Sent</th>
                  <th>Recv</th>
                  <th>Lost</th>
                  <th>OOO</th>
                  <th>RTT Avg (ms)</th>
                  <th>Throughput (B/s)</th>
                </tr>
              </thead>
              <tbody id="bm-tbody">
                <tr><td colspan="8" style="text-align:center; color:var(--text-dim);">Belum ada benchmark dijalankan.</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- VIEW 3: FRAMING SIMULATOR -->
    <div id="tab-framing" class="view-panel">
      <div class="standard-panel">
        <div class="split-2">
          <div class="card">
            <div class="card-title">🔬 Simulator Framing TCP vs UDP</div>
            <p style="font-size:12px; color:var(--text-muted); margin-bottom:12px;">
              Lihat bagaimana satu pesan JSON di-frame di kawat. TCP memotong stream dan memakai delimiter \n, sedangkan UDP utuh dalam 1 datagram.
            </p>
            <div style="display:flex; flex-direction:column; gap:10px;">
              <input type="text" id="sim-text" class="flutter-input" value="Halo semua! Ini uji framing TCP vs UDP." />
              <div style="display:flex; gap:10px; align-items:center;">
                <label style="font-size:12px; color:var(--text-dim);">Potong chunk TCP di byte:</label>
                <input type="number" id="sim-split" class="flutter-input" value="25" style="width:70px;" />
                <button class="btn-action primary" onclick="runFramingSim()">Simulasikan</button>
              </div>
              <div id="sim-output" class="code-box">Klik "Simulasikan" untuk melihat representasi byte.</div>
            </div>
          </div>

          <div class="card">
            <div class="card-title">
              <span>📡 Live Server Event Log</span>
              <button class="btn-action" onclick="refreshServerLogs()">↻ Refresh</button>
            </div>
            <div class="table-wrap" style="max-height: 380px;">
              <table>
                <thead>
                  <tr><th>Proto</th><th>Event</th><th>Type</th><th>Seq</th><th>User</th><th>Bytes</th></tr>
                </thead>
                <tbody id="server-log-tbody"></tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- VIEW 4: CSV EXPLORER -->
    <div id="tab-csv" class="view-panel">
      <div class="standard-panel">
        <div class="split-2">
          <div class="card" style="max-width:380px;">
            <div class="card-title">
              <span>📁 File CSV di results/</span>
              <button class="btn-action" onclick="pollStatus()">↻</button>
            </div>
            <div id="csv-file-list" style="display:flex; flex-direction:column; gap:6px;"></div>
          </div>

          <div class="card">
            <div class="card-title">
              <span id="csv-preview-title">Pilih file CSV</span>
              <a id="csv-download-link" class="btn-action" href="#" download style="display:none; text-decoration:none;">⬇ Download</a>
            </div>
            <div class="table-wrap" style="max-height:450px;">
              <table id="csv-preview-table">
                <thead><tr><th>Pilih file CSV di sisi kiri.</th></tr></thead>
                <tbody></tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>

  </div>

  <!-- PROTOCOL INFO MODAL -->
  <div id="info-modal" class="modal-overlay" onclick="closeModal(event)">
    <div class="modal-box" onclick="event.stopPropagation()">
      <div class="modal-header">Spesifikasi Protokol DualChat</div>
      <div class="modal-body">
        <p><strong>1. TCP (SOCK_STREAM):</strong><br>
        - Berorientasi byte-stream (tidak ada batasan pesan bawaan).<br>
        - Menggunakan delimiter <code>\n</code> (0x0A) dan buffer framing <code>StreamFramer</code>.<br>
        - Handshake 3-way (SYN &rarr; SYN-ACK &rarr; ACK) saat inisialisasi socket.<br>
        - Pengurutan & retransmisi otomatis di level kernel OS.<br><br>
        <strong>2. UDP (SOCK_DGRAM):</strong><br>
        - Berbasis datagram utuh (1 sendto = 1 recvfrom).<br>
        - Connectionless (tanpa handshake).<br>
        - Tidak ada retransmisi atau pengurutan ulang di aplikasi, sehingga paket loss / out-of-order terlihat apa adanya.<br><br>
        <strong>3. Format Pesan (JSON UTF-8):</strong><br>
        <code>{"type": "...", "seq": int, "ts": float, "user": "...", "data": "..."}</code></p>
      </div>
      <div class="modal-footer">
        <button class="btn-action primary" onclick="closeModal()">Tutup</button>
      </div>
    </div>
  </div>

  <script>
    const appState = {
      proto: 'tcp',
      connected: false,
      clientId: null,
      username: 'alice',
      pollTimer: null,
      displayedMsgCount: 0
    };

    function selectProto(proto) {
      if (appState.connected) return;
      appState.proto = proto;
      const isTcp = proto === 'tcp';

      document.getElementById('opt-tcp').className = 'proto-opt' + (isTcp ? ' active tcp' : '');
      document.getElementById('opt-udp').className = 'proto-opt' + (!isTcp ? ' active udp' : '');

      const pill = document.getElementById('hdr-proto-pill');
      pill.textContent = proto.toUpperCase();
      pill.className = 'proto-badge ' + proto;

      const btn = document.getElementById('btn-toggle-connect');
      btn.className = 'btn-connect ' + proto;

      const sendBtn = document.getElementById('btn-send-chat');
      sendBtn.className = 'send-icon-btn ' + proto;

      document.getElementById('cfg-port').value = isTcp ? '5000' : '5001';

      document.getElementById('fn-icon').textContent = isTcp ? '🌐' : '📡';
      document.getElementById('fn-title').textContent = isTcp ? 'TCP Framing (Stream)' : 'UDP Framing (Datagram)';
      document.getElementById('fn-desc').textContent = isTcp
        ? 'Byte-stream kontinu dengan delimiter \\n (0x0A). Menggunakan framing buffer StreamFramer.'
        : '1 Datagram = 1 Pesan utuh via sendto(). Connectionless tanpa retransmisi.';
    }

    async function api(path, method = 'GET', body = null) {
      const opts = { method, headers: { 'Content-Type': 'application/json' } };
      if (body) opts.body = JSON.stringify(body);
      const res = await fetch(path, opts);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Request gagal');
      return data;
    }

    async function toggleConnect() {
      if (appState.connected) {
        // Disconnect
        try {
          await api('/api/clients/action', 'POST', { client_id: appState.clientId, action: 'leave' });
        } catch (_) {}
        setDisconnected();
      } else {
        // Connect & Join
        const host = document.getElementById('cfg-host').value.trim() || '127.0.0.1';
        const port = parseInt(document.getElementById('cfg-port').value.trim(), 10) || 5000;
        const user = document.getElementById('cfg-user').value.trim();
        if (!user) {
          alert('Username tidak boleh kosong!');
          return;
        }

        appState.username = user;
        setConnecting();

        try {
          const res = await api('/api/clients/join', 'POST', {
            proto: appState.proto,
            user: user,
            host: host,
            port: port
          });

          appState.clientId = res.client.client_id;
          setConnected();
          renderMessages(res.client.messages || []);
          updateHud(res.client);
        } catch (err) {
          setDisconnected();
          appendSystemMessage('Gagal terhubung: ' + err.message, true);
        }
      }
    }

    function setConnected() {
      appState.connected = true;
      document.getElementById('hdr-status-tag').textContent = 'CONNECTED';
      document.getElementById('hdr-status-tag').className = 'status-tag connected';

      const btn = document.getElementById('btn-toggle-connect');
      btn.className = 'btn-connect disconnect';
      document.getElementById('btn-connect-icon').textContent = '❌';
      document.getElementById('btn-connect-label').textContent = 'DISCONNECT';

      document.getElementById('chat-input').disabled = false;
      document.getElementById('btn-send-chat').disabled = false;
      document.getElementById('cfg-host').disabled = true;
      document.getElementById('cfg-port').disabled = true;
      document.getElementById('cfg-user').disabled = true;

      startPolling();
    }

    function setConnecting() {
      document.getElementById('hdr-status-tag').textContent = 'CONNECTING...';
      document.getElementById('hdr-status-tag').className = 'status-tag connecting';
      document.getElementById('btn-connect-label').textContent = 'CONNECTING...';
    }

    function setDisconnected() {
      appState.connected = false;
      appState.clientId = null;
      document.getElementById('hdr-status-tag').textContent = 'DISCONNECTED';
      document.getElementById('hdr-status-tag').className = 'status-tag disconnected';

      const btn = document.getElementById('btn-toggle-connect');
      btn.className = 'btn-connect ' + appState.proto;
      document.getElementById('btn-connect-icon').textContent = '🔗';
      document.getElementById('btn-connect-label').textContent = 'CONNECT & JOIN';

      document.getElementById('chat-input').disabled = true;
      document.getElementById('btn-send-chat').disabled = true;
      document.getElementById('cfg-host').disabled = false;
      document.getElementById('cfg-port').disabled = false;
      document.getElementById('cfg-user').disabled = false;

      stopPolling();
    }

    function startPolling() {
      stopPolling();
      appState.pollTimer = setInterval(pollClientUpdates, 350);
    }

    function stopPolling() {
      if (appState.pollTimer) {
        clearInterval(appState.pollTimer);
        appState.pollTimer = null;
      }
    }

    async function pollClientUpdates() {
      if (!appState.connected || !appState.clientId) return;
      try {
        const data = await api('/api/status');
        const clients = data.clients || [];
        const client = clients.find(c => c.client_id === appState.clientId);
        if (client) {
          if (!client.connected) {
            setDisconnected();
            appendSystemMessage('Koneksi terputus dari server', true);
            return;
          }
          renderMessages(client.messages || []);
          updateHud(client);
        }
      } catch (_) {}
    }

    function updateHud(client) {
      document.getElementById('hud-tx-pkts').textContent = client.packets_sent || 0;
      document.getElementById('hud-rx-pkts').textContent = client.packets_recv || 0;
      document.getElementById('hud-rtt').textContent = client.last_rtt_ms !== null ? client.last_rtt_ms + ' ms' : '—';
      const ooo = client.out_of_order_count || 0;
      const oooEl = document.getElementById('hud-ooo');
      oooEl.textContent = ooo;
      oooEl.style.color = ooo > 0 ? '#EF4444' : '#64748B';
      document.getElementById('hud-tx-bytes').textContent = formatBytes(client.bytes_sent || 0);
      document.getElementById('hud-rx-bytes').textContent = formatBytes(client.bytes_recv || 0);
    }

    function formatBytes(b) {
      if (b < 1024) return b + ' B';
      return (b / 1024).toFixed(1) + ' KB';
    }

    function renderMessages(messages) {
      const feed = document.getElementById('chat-feed');
      const empty = document.getElementById('empty-hint');
      if (!messages || messages.length === 0) {
        if (empty) empty.style.display = 'block';
        return;
      }
      if (empty) empty.style.display = 'none';

      feed.innerHTML = messages.map(m => {
        if (m.type === 'PONG') {
          return `
            <div class="pong-card">
              <span>⚡</span>
              <span>[PONG] seq=${m.seq} RTT = ${m.rtt_ms !== null ? m.rtt_ms : '—'} ms</span>
            </div>
          `;
        }
        if (m.type === 'ACK_JOIN' || m.sender === 'server' || m.type === 'ERR') {
          const isErr = m.type === 'ERR';
          return `
            <div class="sys-card ${isErr ? 'err' : ''}">
              <span>${isErr ? '❌' : 'ℹ️'}</span> ${escapeHtml(m.data || m.type)}
            </div>
          `;
        }

        const isSelf = m.sender === appState.username || m.direction === 'OUT';
        const warnHtml = m.warning ? `<div class="warn-chip">⚠️ ${escapeHtml(m.warning)}</div>` : '';

        return `
          <div class="msg-row ${isSelf ? 'self' : 'peer'}">
            ${warnHtml}
            <div class="msg-meta-row">
              <span class="msg-user ${isSelf ? 'self ' + appState.proto : 'peer'}">
                ${isSelf ? 'You (' + escapeHtml(m.sender) + ')' : escapeHtml(m.sender)}
              </span>
              <span class="seq-chip">seq: ${m.seq}</span>
            </div>
            <div class="msg-bubble ${isSelf ? 'self ' + appState.proto : 'peer'}">
              ${escapeHtml(m.data)}
            </div>
          </div>
        `;
      }).join('');

      feed.scrollTop = feed.scrollHeight;
    }

    function appendSystemMessage(text, isErr = false) {
      const feed = document.getElementById('chat-feed');
      const empty = document.getElementById('empty-hint');
      if (empty) empty.style.display = 'none';

      const div = document.createElement('div');
      div.className = 'sys-card' + (isErr ? ' err' : '');
      div.innerHTML = `<span>${isErr ? '❌' : 'ℹ️'}</span> ${escapeHtml(text)}`;
      feed.appendChild(div);
      feed.scrollTop = feed.scrollHeight;
    }

    async function handleSendChat() {
      if (!appState.connected || !appState.clientId) return;
      const input = document.getElementById('chat-input');
      const text = input.value.trim();
      if (!text) return;

      if (text === '/ping') {
        handleSendPing();
        input.value = '';
        return;
      }

      try {
        await api('/api/clients/action', 'POST', {
          client_id: appState.clientId,
          action: 'msg',
          text: text
        });
        input.value = '';
        pollClientUpdates();
      } catch (err) {
        appendSystemMessage(err.message, true);
      }
    }

    async function handleSendPing() {
      if (!appState.connected || !appState.clientId) {
        alert('Hubungkan client terlebih dahulu!');
        return;
      }
      try {
        await api('/api/clients/action', 'POST', {
          client_id: appState.clientId,
          action: 'ping'
        });
        pollClientUpdates();
      } catch (err) {
        appendSystemMessage(err.message, true);
      }
    }

    function clearChatLog() {
      const feed = document.getElementById('chat-feed');
      feed.innerHTML = `
        <div id="empty-hint" class="empty-chat">
          <div class="empty-icon">💬</div>
          <div style="font-size:14px; color:var(--text-muted);">Chat log dibersihkan.</div>
        </div>
      `;
    }

    function switchView(viewId, btn) {
      document.querySelectorAll('.view-panel').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.vtab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById(viewId).classList.add('active');
      btn.classList.add('active');

      if (viewId === 'tab-framing') {
        runFramingSim();
        refreshServerLogs();
      } else if (viewId === 'tab-csv') {
        pollStatus();
      }
    }

    async function triggerBenchmark() {
      const btn = document.getElementById('btn-run-bm');
      btn.disabled = true;
      btn.textContent = '⏳ Menjalankan...';
      try {
        const payload = {
          proto: document.getElementById('bm-proto').value,
          mode: document.getElementById('bm-mode').value,
          count: parseInt(document.getElementById('bm-count').value, 10),
          size: parseInt(document.getElementById('bm-size').value, 10),
          interval: parseFloat(document.getElementById('bm-interval').value),
          scenario: document.getElementById('bm-scenario').value,
          run: 1
        };
        const res = await api('/api/benchmark/run', 'POST', payload);
        const rows = res.results.map(r => `
          <tr>
            <td><strong>${r.proto.toUpperCase()}</strong></td>
            <td>${r.mode}</td>
            <td>${r.sent}</td>
            <td>${r.received}</td>
            <td>${r.lost}</td>
            <td>${r.out_of_order}</td>
            <td>${r.rtt_avg_ms ? r.rtt_avg_ms.toFixed(3) : '—'}</td>
            <td>${r.throughput_bps ? r.throughput_bps.toFixed(0) : '—'}</td>
          </tr>
        `).join('');
        document.getElementById('bm-tbody').innerHTML = rows;
      } catch (e) {
        alert('Benchmark gagal: ' + e.message);
      } finally {
        btn.disabled = false;
        btn.textContent = '▶ Jalankan Benchmark';
      }
    }

    async function runFramingSim() {
      const text = document.getElementById('sim-text').value;
      const splitAt = parseInt(document.getElementById('sim-split').value, 10);
      try {
        const res = await api('/api/framing/simulate', 'POST', { text, split_at: splitAt });
        document.getElementById('sim-output').textContent = JSON.stringify(res, null, 2);
      } catch (e) {
        document.getElementById('sim-output').textContent = 'Error: ' + e.message;
      }
    }

    async function refreshServerLogs() {
      try {
        const dTcp = await api('/api/csv/read?file=server_tcp.csv&limit=20');
        const dUdp = await api('/api/csv/read?file=server_udp.csv&limit=20');
        const all = [];
        (dTcp.rows || []).forEach(r => all.push({ proto: 'tcp', row: r }));
        (dUdp.rows || []).forEach(r => all.push({ proto: 'udp', row: r }));
        all.sort((a, b) => parseFloat(b.row[0] || 0) - parseFloat(a.row[0] || 0));

        const tbody = document.getElementById('server-log-tbody');
        tbody.innerHTML = all.slice(0, 30).map(x => `
          <tr>
            <td><strong style="color:${x.proto==='tcp'?'#10B981':'#F59E0B'}">${x.proto.toUpperCase()}</strong></td>
            <td>${escapeHtml(x.row[2]||'')}</td>
            <td>${escapeHtml(x.row[3]||'')}</td>
            <td>${escapeHtml(x.row[4]||'')}</td>
            <td>${escapeHtml(x.row[5]||'')}</td>
            <td>${escapeHtml(x.row[6]||'')}</td>
          </tr>
        `).join('');
      } catch (_) {}
    }

    async function pollStatus() {
      try {
        const data = await api('/api/status');
        renderCsvFileList(data.csv_files || []);
      } catch (_) {}
    }

    function renderCsvFileList(files) {
      const el = document.getElementById('csv-file-list');
      if (!files.length) {
        el.innerHTML = '<span style="color:var(--text-dim); font-size:12px;">Belum ada CSV di results/.</span>';
        return;
      }
      el.innerHTML = files.map(f => `
        <div style="background:var(--bg-card); padding:8px 12px; border-radius:6px; cursor:pointer; font-size:12px; display:flex; justify-content:space-between;" onclick="previewCsv('${f.name}')">
          <span style="color:var(--cyan-accent);">${f.name}</span>
          <span style="color:var(--text-dim);">${f.size_bytes} B</span>
        </div>
      `).join('');
    }

    async function previewCsv(name) {
      try {
        const d = await api(`/api/csv/read?file=${encodeURIComponent(name)}&limit=80`);
        document.getElementById('csv-preview-title').textContent = d.filename;
        const dl = document.getElementById('csv-download-link');
        dl.href = `/api/csv/download?file=${encodeURIComponent(name)}`;
        dl.style.display = 'inline-block';

        const table = document.getElementById('csv-preview-table');
        table.querySelector('thead').innerHTML = `<tr>${d.headers.map(h => `<th>${escapeHtml(h)}</th>`).join('')}</tr>`;
        table.querySelector('tbody').innerHTML = d.rows.map(r => `
          <tr>${r.map(c => `<td>${escapeHtml(c)}</td>`).join('')}</tr>
        `).join('');
      } catch (e) {
        alert(e.message);
      }
    }

    function openModal() { document.getElementById('info-modal').style.display = 'flex'; }
    function closeModal() { document.getElementById('info-modal').style.display = 'none'; }

    function escapeHtml(str) {
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }
  </script>
</body>
</html>
"""


class DualChatHttpHandler(BaseHTTPRequestHandler):
    """Handler HTTP untuk menyajikan halaman Web UI dan endpoint JSON API."""

    controller: DualChatDashboardController

    def log_message(self, format: str, *args: Any) -> None:
        # Redam log HTTP rutin agar konsol tetap bersih
        pass

    def _send_json(self, data: Any, status: int = HTTPStatus.OK) -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            body = DASHBOARD_HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/status":
            self._send_json(self.controller.get_status())
            return

        if path == "/api/csv/read":
            filename = query.get("file", [""])[0]
            limit = int(query.get("limit", ["100"])[0])
            try:
                content = self.controller.read_csv_content(filename, limit=limit)
                self._send_json(content)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/csv/download":
            filename = Path(query.get("file", [""])[0]).name
            target = self.controller.log_dir / filename
            if not target.exists():
                self._send_json({"error": "File tidak ditemukan"}, status=HTTPStatus.NOT_FOUND)
                return
            raw = target.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return

        self._send_json({"error": "Endpoint tidak ditemukan"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        try:
            payload = self._read_json_body()
        except Exception as exc:
            self._send_json({"error": f"JSON body tidak valid: {exc}"}, status=HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/clients/join":
            proto = str(payload.get("proto", "tcp"))
            user = str(payload.get("user", ""))
            host = payload.get("host")
            port = payload.get("port")
            if port is not None:
                try:
                    port = int(port)
                except ValueError:
                    port = None
            ok, result = self.controller.create_client(proto=proto, user=user, host=host, port=port)
            if not ok:
                self._send_json({"error": result}, status=HTTPStatus.BAD_REQUEST)
            else:
                self._send_json({"client": result})
            return

        if path == "/api/clients/action":
            client_id = str(payload.get("client_id", ""))
            action = str(payload.get("action", ""))
            text = str(payload.get("text", ""))
            ok, result = self.controller.client_action(client_id=client_id, action=action, text=text)
            if not ok:
                self._send_json({"error": result}, status=HTTPStatus.BAD_REQUEST)
            else:
                self._send_json({"client": result})
            return

        if path == "/api/benchmark/run":
            try:
                res = self.controller.run_benchmark_suite(
                    proto_mode=str(payload.get("proto", "both")),
                    mode=str(payload.get("mode", "ping")),
                    count=int(payload.get("count", 50)),
                    size=int(payload.get("size", 64)),
                    interval=float(payload.get("interval", 0.0)),
                    scenario=str(payload.get("scenario", "S1")),
                    run_id=int(payload.get("run", 1)),
                )
                self._send_json(res)
            except Exception as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/framing/simulate":
            sim = self.controller.simulate_framing(
                text=str(payload.get("text", "halo semua")),
                user=str(payload.get("user", "alice")),
                seq=int(payload.get("seq", 1)),
                split_at=int(payload.get("split_at", 25)),
            )
            self._send_json(sim)
            return

        self._send_json({"error": "Endpoint POST tidak ditemukan"}, status=HTTPStatus.NOT_FOUND)


def create_http_server(
    ui_host: str = "127.0.0.1",
    ui_port: int = 8080,
    chat_host: str = DEFAULT_HOST,
    tcp_port: int = DEFAULT_TCP_PORT,
    udp_port: int = DEFAULT_UDP_PORT,
    log_dir: str | Path = "results",
) -> tuple[ThreadingHTTPServer, DualChatDashboardController]:
    """Membuat instance HTTP server dan controller DualChat."""
    controller = DualChatDashboardController(
        host=chat_host,
        tcp_port=tcp_port,
        udp_port=udp_port,
        log_dir=log_dir,
        auto_start_servers=True,
    )

    class BoundHandler(DualChatHttpHandler):
        pass

    BoundHandler.controller = controller
    httpd = ThreadingHTTPServer((ui_host, ui_port), BoundHandler)
    return httpd, controller


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Web UI/UX Dashboard untuk DualChat (TCP vs UDP)")
    parser.add_argument("--ui-host", default="127.0.0.1", help="Host untuk Web UI HTTP server (default: 127.0.0.1)")
    parser.add_argument("--ui-port", type=int, default=8080, help="Port untuk Web UI HTTP server (default: 8080)")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host untuk Server Socket TCP/UDP (default: 127.0.0.1)")
    parser.add_argument("--tcp-port", type=int, default=DEFAULT_TCP_PORT, help="Port TCP Server (default: 5000)")
    parser.add_argument("--udp-port", type=int, default=DEFAULT_UDP_PORT, help="Port UDP Server (default: 5001)")
    parser.add_argument("--log-dir", default="results/", help="Direktori penyimpanan CSV (default: results/)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    httpd, controller = create_http_server(
        ui_host=args.ui_host,
        ui_port=args.ui_port,
        chat_host=args.host,
        tcp_port=args.tcp_port,
        udp_port=args.udp_port,
        log_dir=args.log_dir,
    )
    actual_ui_port = httpd.server_address[1]
    url = f"http://{args.ui_host}:{actual_ui_port}"

    print("================================================================")
    print(" DualChat Full Web UI/UX Studio Aktif!")
    print(f" [WEB] Buka di Browser  : {url}")
    print(f" [TCP] Socket Server    : {args.host}:{controller.tcp_port}")
    print(f" [UDP] Socket Server    : {args.host}:{controller.udp_port}")
    print(" Tekan Ctrl+C untuk menutup server dengan rapi.")
    print("================================================================")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Menutup Web UI dan Server TCP/UDP...")
    finally:
        httpd.server_close()
        controller.stop_all()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
