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
                self.tcp_server = ProtocolServerWorker(
                    proto="tcp",
                    host=self.host,
                    port=self.tcp_port,
                    log_dir=self.log_dir,
                )
                self.tcp_server.start()
                self.tcp_port = self.tcp_server.bound_port

            if self.udp_server is None:
                self.udp_server = ProtocolServerWorker(
                    proto="udp",
                    host=self.host,
                    port=self.udp_port,
                    log_dir=self.log_dir,
                )
                self.udp_server.start()
                self.udp_port = self.udp_server.bound_port

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
                # Beri waktu sangat singkat agar PONG di localhost langsung tertangkap
                time.sleep(0.04)
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
<html lang="id" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>DualChat Network Telemetry & Chat Studio (TCP vs UDP)</title>
  <style>
    :root {
      --bg-main: #0b0f17;
      --bg-panel: #111827;
      --bg-card: #1b2436;
      --bg-input: #0f172a;
      --border: #2a364f;
      --text-main: #f1f5f9;
      --text-muted: #94a3b8;
      --tcp-color: #3b82f6;
      --tcp-glow: rgba(59, 130, 246, 0.18);
      --udp-color: #10b981;
      --udp-glow: rgba(16, 185, 129, 0.18);
      --warn-color: #f59e0b;
      --err-color: #ef4444;
      --accent-purple: #8b5cf6;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg-main);
      color: var(--text-main);
      font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
      font-size: 14px;
      line-height: 1.5;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    /* Top Navbar */
    .topbar {
      background: var(--bg-panel);
      border-bottom: 1px solid var(--border);
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 12px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
    }
    .brand-logo {
      width: 38px;
      height: 38px;
      border-radius: 10px;
      background: linear-gradient(135deg, var(--tcp-color), var(--udp-color));
      display: flex;
      align-items: center;
      justify-content: center;
      font-weight: 800;
      font-size: 16px;
      color: #fff;
      box-shadow: 0 4px 14px rgba(59, 130, 246, 0.35);
    }
    .brand-title h1 {
      font-size: 17px;
      font-weight: 700;
      letter-spacing: -0.01em;
    }
    .brand-title p {
      font-size: 12px;
      color: var(--text-muted);
    }
    .status-pills {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 6px 12px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      border: 1px solid var(--border);
      background: var(--bg-card);
    }
    .pill.tcp { border-color: rgba(59,130,246,0.45); background: var(--tcp-glow); color: #93c5fd; }
    .pill.udp { border-color: rgba(16,185,129,0.45); background: var(--udp-glow); color: #6ee7b7; }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: #22c55e;
      box-shadow: 0 0 8px #22c55e;
    }
    /* Navigation Tabs */
    .nav-tabs {
      display: flex;
      gap: 6px;
      background: var(--bg-panel);
      padding: 0 24px;
      border-bottom: 1px solid var(--border);
      overflow-x: auto;
    }
    .tab-btn {
      background: transparent;
      color: var(--text-muted);
      border: none;
      border-bottom: 2px solid transparent;
      padding: 12px 18px;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s;
      white-space: nowrap;
    }
    .tab-btn:hover { color: var(--text-main); }
    .tab-btn.active {
      color: #fff;
      border-bottom-color: var(--tcp-color);
      background: rgba(59, 130, 246, 0.08);
    }
    /* Main Workspace Container */
    .workspace {
      flex: 1;
      padding: 20px 24px;
      max-width: 1500px;
      width: 100%;
      margin: 0 auto;
    }
    .tab-pane { display: none; }
    .tab-pane.active { display: block; }
    /* Grid helpers */
    .split-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
    }
    @media (max-width: 1024px) {
      .split-2 { grid-template-columns: 1fr; }
    }
    .card {
      background: var(--bg-panel);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .card.tcp-card { border-top: 3px solid var(--tcp-color); }
    .card.udp-card { border-top: 3px solid var(--udp-color); }
    .card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--border);
    }
    .card-title {
      font-size: 15px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .badge {
      font-size: 11px;
      padding: 2px 8px;
      border-radius: 6px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .badge-tcp { background: var(--tcp-glow); color: #60a5fa; border: 1px solid rgba(59,130,246,0.4); }
    .badge-udp { background: var(--udp-glow); color: #34d399; border: 1px solid rgba(16,185,129,0.4); }
    .proto-specs {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 8px;
      background: var(--bg-input);
      padding: 10px;
      border-radius: 8px;
      border: 1px solid var(--border);
      font-size: 12px;
    }
    .spec-item span { display: block; color: var(--text-muted); font-size: 11px; }
    .spec-item strong { color: var(--text-main); font-family: Consolas, monospace; }
    /* Forms & Buttons */
    .form-row {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    input[type="text"], input[type="number"], select {
      background: var(--bg-input);
      border: 1px solid var(--border);
      color: var(--text-main);
      padding: 8px 12px;
      border-radius: 8px;
      font-size: 13px;
      outline: none;
    }
    input:focus, select:focus { border-color: var(--tcp-color); }
    .btn {
      padding: 8px 14px;
      border-radius: 8px;
      border: 1px solid transparent;
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .btn-tcp { background: var(--tcp-color); color: #fff; }
    .btn-tcp:hover { background: #2563eb; }
    .btn-udp { background: var(--udp-color); color: #052e16; font-weight: 700; }
    .btn-udp:hover { background: #059669; color: #fff; }
    .btn-secondary {
      background: var(--bg-card);
      color: var(--text-main);
      border-color: var(--border);
    }
    .btn-secondary:hover { background: #26334d; }
    .btn-danger {
      background: rgba(239, 68, 68, 0.15);
      color: #fca5a5;
      border-color: rgba(239, 68, 68, 0.35);
    }
    .btn-danger:hover { background: #ef4444; color: #fff; }
    /* Client pills selector */
    .client-list {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      min-height: 34px;
      align-items: center;
    }
    .client-chip {
      padding: 5px 11px;
      border-radius: 8px;
      background: var(--bg-card);
      border: 1px solid var(--border);
      font-size: 12px;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
    }
    .client-chip.active-tcp {
      border-color: var(--tcp-color);
      background: var(--tcp-glow);
      color: #fff;
      font-weight: 600;
    }
    .client-chip.active-udp {
      border-color: var(--udp-color);
      background: var(--udp-glow);
      color: #fff;
      font-weight: 600;
    }
    /* Chat Feed */
    .chat-box {
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: 10px;
      height: 310px;
      overflow-y: auto;
      padding: 12px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      font-size: 13px;
    }
    .chat-msg {
      padding: 8px 11px;
      border-radius: 8px;
      background: var(--bg-card);
      border-left: 3px solid var(--border);
    }
    .chat-msg.out { border-left-color: var(--tcp-color); background: rgba(59, 130, 246, 0.08); }
    .chat-msg.in { border-left-color: var(--udp-color); }
    .chat-msg.pong { border-left-color: var(--accent-purple); background: rgba(139, 92, 246, 0.1); }
    .chat-msg.warn { border-left-color: var(--warn-color); background: rgba(245, 158, 11, 0.12); }
    .msg-meta {
      display: flex;
      justify-content: space-between;
      font-size: 11px;
      color: var(--text-muted);
      margin-bottom: 3px;
      font-family: Consolas, monospace;
    }
    .wire-tag {
      margin-top: 4px;
      padding: 4px 7px;
      background: rgba(0,0,0,0.35);
      border-radius: 4px;
      font-family: Consolas, monospace;
      font-size: 11px;
      color: #94a3b8;
      word-break: break-all;
    }
    /* Benchmark metrics & charts */
    .metric-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
    }
    @media (max-width: 900px) {
      .metric-grid { grid-template-columns: repeat(2, 1fr); }
    }
    .metric-box {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
    }
    .metric-box .label { font-size: 12px; color: var(--text-muted); }
    .metric-box .val { font-size: 20px; font-weight: 700; margin-top: 4px; font-family: Consolas, monospace; }
    canvas.chart-canvas {
      width: 100%;
      height: 250px;
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: 10px;
    }
    /* Tables */
    .table-wrap {
      overflow-x: auto;
      border: 1px solid var(--border);
      border-radius: 10px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 12px;
      text-align: left;
    }
    th, td {
      padding: 9px 12px;
      border-bottom: 1px solid var(--border);
      font-family: Consolas, monospace;
    }
    th { background: var(--bg-card); color: var(--text-muted); font-weight: 600; }
    tr:hover td { background: rgba(255,255,255,0.02); }
    .code-block {
      background: var(--bg-input);
      border: 1px solid var(--border);
      padding: 12px;
      border-radius: 8px;
      font-family: Consolas, monospace;
      font-size: 12px;
      overflow-x: auto;
      color: #e2e8f0;
    }
    .toast {
      position: fixed;
      bottom: 20px;
      right: 20px;
      background: #1e293b;
      border: 1px solid var(--tcp-color);
      color: #fff;
      padding: 10px 16px;
      border-radius: 10px;
      box-shadow: 0 8px 24px rgba(0,0,0,0.45);
      font-size: 13px;
      display: none;
      z-index: 999;
    }
  </style>
</head>
<body>

  <!-- TOP TELEMETRY NAVBAR -->
  <header class="topbar">
    <div class="brand">
      <div class="brand-logo">DC</div>
      <div class="brand-title">
        <h1>DualChat Telemetry & Protocol Studio</h1>
        <p>Perbandingan Apple-to-Apple TCP (SOCK_STREAM) vs UDP (SOCK_DGRAM) pada Application Layer</p>
      </div>
    </div>
    <div class="status-pills">
      <div class="pill tcp">
        <span class="dot"></span>
        <span>TCP Server: <strong id="nav-tcp-port">127.0.0.1:5000</strong> (<span id="nav-tcp-users">0</span> client)</span>
      </div>
      <div class="pill udp">
        <span class="dot"></span>
        <span>UDP Server: <strong id="nav-udp-port">127.0.0.1:5001</strong> (<span id="nav-udp-users">0</span> client)</span>
      </div>
    </div>
  </header>

  <!-- WORKSPACE TABS -->
  <nav class="nav-tabs">
    <button class="tab-btn active" onclick="switchTab('tab-chat', this)">💬 1. Dual-Protocol Chat Arena (TCP vs UDP)</button>
    <button class="tab-btn" onclick="switchTab('tab-bench', this)">⚡ 2. Benchmark Lab & Grafik RTT/Throughput</button>
    <button class="tab-btn" onclick="switchTab('tab-framing', this)">🔍 3. Wire Framing Visualizer & Server Log</button>
    <button class="tab-btn" onclick="switchTab('tab-csv', this)">📁 4. CSV Explorer (Bahan Laporan)</button>
  </nav>

  <!-- MAIN CONTENT -->
  <main class="workspace">

    <!-- ========================================================= -->
    <!-- TAB 1: DUAL-PROTOCOL INTERACTIVE CHAT ARENA               -->
    <!-- ========================================================= -->
    <section id="tab-chat" class="tab-pane active">
      <div class="split-2">

        <!-- TCP ROOM PANEL -->
        <div class="card tcp-card">
          <div class="card-header">
            <div class="card-title">
              <span class="badge badge-tcp">TCP · SOCK_STREAM</span>
              <span>Connection-Oriented Chat Room</span>
            </div>
            <button class="btn btn-secondary" onclick="quickSeedClients('tcp')" title="Buat 2 client contoh otomatis">⚡ Quick Demo (alice_tcp & bob_tcp)</button>
          </div>

          <div class="proto-specs">
            <div class="spec-item"><span>Handshake</span><strong>3-Way (SYN/SYN-ACK/ACK)</strong></div>
            <div class="spec-item"><span>Framing</span><strong>Byte Stream + '\n' Buffer</strong></div>
            <div class="spec-item"><span>Header Overhead</span><strong>20+ Byte per Segmen</strong></div>
          </div>

          <div class="form-row">
            <input type="text" id="tcp-new-user" placeholder="Username baru (mis. alice)" style="flex:1" />
            <button class="btn btn-tcp" onclick="joinClient('tcp')">+ JOIN TCP</button>
          </div>

          <div>
            <div style="font-size:12px; color:var(--text-muted); margin-bottom:6px;">Pilih Client TCP Aktif (Pengirim/Penampil):</div>
            <div id="tcp-client-list" class="client-list">
              <span style="color:var(--text-muted); font-size:12px;">Belum ada client TCP. Klik "+ JOIN TCP" atau "Quick Demo".</span>
            </div>
          </div>

          <div id="tcp-chat-box" class="chat-box"></div>

          <div class="form-row">
            <input type="text" id="tcp-msg-input" placeholder="Ketik pesan MSG untuk dikirim lewat stream TCP..." style="flex:1" onkeydown="if(event.key==='Enter') sendChatMsg('tcp')" />
            <button class="btn btn-tcp" onclick="sendChatMsg('tcp')">Kirim MSG</button>
            <button class="btn btn-secondary" onclick="sendPing('tcp')">📡 /ping (RTT)</button>
            <button class="btn btn-danger" onclick="leaveCurrentClient('tcp')">/quit</button>
          </div>
        </div>

        <!-- UDP ROOM PANEL -->
        <div class="card udp-card">
          <div class="card-header">
            <div class="card-title">
              <span class="badge badge-udp">UDP · SOCK_DGRAM</span>
              <span>Connectionless Datagram Room</span>
            </div>
            <button class="btn btn-secondary" onclick="quickSeedClients('udp')" title="Buat 2 client contoh otomatis">⚡ Quick Demo (alice_udp & bob_udp)</button>
          </div>

          <div class="proto-specs">
            <div class="spec-item"><span>Handshake</span><strong>Tanpa Handshake (0 RTT)</strong></div>
            <div class="spec-item"><span>Framing</span><strong>1 Datagram = 1 Pesan</strong></div>
            <div class="spec-item"><span>Reliability</span><strong>No Retry / Seq Gap Check</strong></div>
          </div>

          <div class="form-row">
            <input type="text" id="udp-new-user" placeholder="Username baru (mis. charlie)" style="flex:1" />
            <button class="btn btn-udp" onclick="joinClient('udp')">+ JOIN UDP</button>
          </div>

          <div>
            <div style="font-size:12px; color:var(--text-muted); margin-bottom:6px;">Pilih Client UDP Aktif (Pengirim/Penampil):</div>
            <div id="udp-client-list" class="client-list">
              <span style="color:var(--text-muted); font-size:12px;">Belum ada client UDP. Klik "+ JOIN UDP" atau "Quick Demo".</span>
            </div>
          </div>

          <div id="udp-chat-box" class="chat-box"></div>

          <div class="form-row">
            <input type="text" id="udp-msg-input" placeholder="Ketik pesan MSG untuk dikirim sebagai datagram UDP..." style="flex:1" onkeydown="if(event.key==='Enter') sendChatMsg('udp')" />
            <button class="btn btn-udp" onclick="sendChatMsg('udp')">Kirim MSG</button>
            <button class="btn btn-secondary" onclick="sendPing('udp')">📡 /ping (RTT)</button>
            <button class="btn btn-danger" onclick="leaveCurrentClient('udp')">/quit</button>
          </div>
        </div>

      </div>
    </section>

    <!-- ========================================================= -->
    <!-- TAB 2: BENCHMARK LAB & GRAFIK PERBANDINGAN                -->
    <!-- ========================================================= -->
    <section id="tab-bench" class="tab-pane">
      <div class="card" style="margin-bottom: 20px;">
        <div class="card-header">
          <div class="card-title">⚡ Konfigurasi Workload Benchmark Deterministik (`benchmark.py`)</div>
          <span style="font-size:12px; color:var(--text-muted)">Payload: <code>'x' * size</code> | Menghasilkan CSV otomatis di <code>results/</code></span>
        </div>

        <div class="form-row" style="gap: 14px;">
          <div>
            <label style="display:block; font-size:11px; color:var(--text-muted)">Protokol</label>
            <select id="bm-proto">
              <option value="both" selected>Head-to-Head (TCP & UDP Sekaligus)</option>
              <option value="tcp">Hanya TCP</option>
              <option value="udp">Hanya UDP</option>
            </select>
          </div>
          <div>
            <label style="display:block; font-size:11px; color:var(--text-muted)">Mode Uji</label>
            <select id="bm-mode">
              <option value="ping" selected>Mode PING (Ukur RTT & Jitter)</option>
              <option value="msg">Mode MSG (Sender -> Receiver Throughput)</option>
            </select>
          </div>
          <div>
            <label style="display:block; font-size:11px; color:var(--text-muted)">Jumlah Pesan (--count)</label>
            <input type="number" id="bm-count" value="50" min="5" max="2000" style="width:110px" />
          </div>
          <div>
            <label style="display:block; font-size:11px; color:var(--text-muted)">Ukuran Data (--size B)</label>
            <input type="number" id="bm-size" value="128" min="1" max="8192" style="width:110px" />
          </div>
          <div>
            <label style="display:block; font-size:11px; color:var(--text-muted)">Interval (--interval s)</label>
            <input type="number" id="bm-interval" value="0.002" step="0.001" min="0" style="width:110px" />
          </div>
          <div>
            <label style="display:block; font-size:11px; color:var(--text-muted)">Label Skenario</label>
            <input type="text" id="bm-scenario" value="S1" style="width:80px" />
          </div>
          <div>
            <label style="display:block; font-size:11px; color:var(--text-muted)">Run #</label>
            <input type="number" id="bm-run" value="1" min="1" style="width:70px" />
          </div>
          <div style="margin-top: 16px;">
            <button id="btn-run-bm" class="btn btn-tcp" onclick="triggerBenchmark()">▶ Jalankan Benchmark</button>
          </div>
        </div>
      </div>

      <div class="split-2" style="margin-bottom: 20px;">
        <div class="card">
          <div class="card-header">
            <div class="card-title">📈 Grafik Perbandingan Latensi / RTT per Sequence (`seq`)</div>
            <span style="font-size:12px; color:var(--text-muted)">Biru = TCP | Hijau = UDP</span>
          </div>
          <canvas id="rttChart" class="chart-canvas" width="650" height="250"></canvas>
        </div>

        <div class="card">
          <div class="card-header">
            <div class="card-title">📊 Perbandingan Throughput & Keandalan Pengiriman</div>
            <span style="font-size:12px; color:var(--text-muted)">Hasil Run Terakhir</span>
          </div>
          <div id="bm-summary-cards" class="metric-grid">
            <div class="metric-box"><div class="label">TCP Avg RTT</div><div class="val" id="m-tcp-rtt">-</div></div>
            <div class="metric-box"><div class="label">UDP Avg RTT</div><div class="val" id="m-udp-rtt">-</div></div>
            <div class="metric-box"><div class="label">TCP Throughput</div><div class="val" id="m-tcp-tp">-</div></div>
            <div class="metric-box"><div class="label">UDP Throughput</div><div class="val" id="m-udp-tp">-</div></div>
          </div>
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
                  <th>StdDev</th>
                  <th>Throughput (B/s)</th>
                </tr>
              </thead>
              <tbody id="bm-results-tbody">
                <tr><td colspan="9" style="text-align:center; color:var(--text-muted)">Klik "Jalankan Benchmark" untuk melihat perbandingan statistik.</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </section>

    <!-- ========================================================= -->
    <!-- TAB 3: WIRE FRAMING VISUALIZER & LIVE SERVER LOG          -->
    <!-- ========================================================= -->
    <section id="tab-framing" class="tab-pane">
      <div class="split-2">
        <div class="card">
          <div class="card-header">
            <div class="card-title">🔬 Simulator Framing: TCP Byte-Stream (`\n`) vs UDP Datagram</div>
          </div>
          <p style="font-size:12px; color:var(--text-muted)">
            Uji bagaimana satu pesan JSON diperlakukan berbeda di Transport Layer: TCP menambahkan delimiter <code>\n</code> dan merakit potongan chunk menggunakan <code>StreamFramer</code>, sedangkan UDP mengirimkannya sebagai 1 datagram utuh.
          </p>
          <div class="form-row">
            <input type="text" id="sim-text" value="Halo semua! Ini uji framing TCP vs UDP." style="flex:1" />
            <label style="font-size:12px; color:var(--text-muted)">Potong Chunk TCP di byte ke-:</label>
            <input type="number" id="sim-split" value="28" min="5" max="120" style="width:75px" />
            <button class="btn btn-tcp" onclick="runFramingSim()">Simulasikan</button>
          </div>
          <div id="sim-output" class="code-block">Klik "Simulasikan" untuk melihat representasi byte di kawat (wire format).</div>
        </div>

        <div class="card">
          <div class="card-header">
            <div class="card-title">📡 Live Server CSV Telemetry (`server_tcp.csv` & `server_udp.csv`)</div>
            <button class="btn btn-secondary" onclick="refreshServerLogs()">↻ Refresh Log</button>
          </div>
          <div class="table-wrap" style="max-height: 360px; overflow-y: auto;">
            <table>
              <thead>
                <tr>
                  <th>Proto</th>
                  <th>Event</th>
                  <th>Type</th>
                  <th>Seq</th>
                  <th>User</th>
                  <th>Bytes</th>
                  <th>Peer</th>
                </tr>
              </thead>
              <tbody id="server-log-tbody"></tbody>
            </table>
          </div>
        </div>
      </div>
    </section>

    <!-- ========================================================= -->
    <!-- TAB 4: CSV EXPLORER (HASIL EKSPERIMEN)                    -->
    <!-- ========================================================= -->
    <section id="tab-csv" class="tab-pane">
      <div class="split-2">
        <div class="card" style="max-width: 420px;">
          <div class="card-header">
            <div class="card-title">📁 Daftar File CSV di <code>results/</code></div>
            <button class="btn btn-secondary" onclick="pollStatus()">↻ Refresh</button>
          </div>
          <div id="csv-file-list" style="display:flex; flex-direction:column; gap:8px;"></div>
        </div>

        <div class="card" style="flex:1;">
          <div class="card-header">
            <div class="card-title">📄 Pratinjau Isi CSV: <span id="csv-preview-title" style="color:var(--tcp-color)">Pilih file</span></div>
            <a id="csv-download-link" class="btn btn-secondary" href="#" download style="display:none; text-decoration:none;">⬇ Download CSV</a>
          </div>
          <div class="table-wrap" style="max-height: 440px; overflow-y: auto;">
            <table id="csv-preview-table">
              <thead><tr><th>Pilih salah satu file CSV di panel kiri untuk melihat datanya.</th></tr></thead>
              <tbody></tbody>
            </table>
          </div>
        </div>
      </div>
    </section>

  </main>

  <div id="toast" class="toast"></div>

  <script>
    let state = {
      clients: [],
      selectedClient: { tcp: null, udp: null },
      lastBenchResults: []
    };

    function showToast(msg) {
      const el = document.getElementById('toast');
      el.textContent = msg;
      el.style.display = 'block';
      clearTimeout(window._toastTimer);
      window._toastTimer = setTimeout(() => { el.style.display = 'none'; }, 3000);
    }

    function switchTab(tabId, btn) {
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.getElementById(tabId).classList.add('active');
      btn.classList.add('active');
      if (tabId === 'tab-framing') {
        runFramingSim();
        refreshServerLogs();
      }
    }

    async function api(path, method = 'GET', body = null) {
      const opts = { method, headers: { 'Content-Type': 'application/json' } };
      if (body) opts.body = JSON.stringify(body);
      const res = await fetch(path, opts);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Request gagal');
      return data;
    }

    async function pollStatus() {
      try {
        const data = await api('/api/status');
        document.getElementById('nav-tcp-port').textContent = `${data.host}:${data.tcp_server.port}`;
        document.getElementById('nav-tcp-users').textContent = data.tcp_server.active_users.length;
        document.getElementById('nav-udp-port').textContent = `${data.host}:${data.udp_server.port}`;
        document.getElementById('nav-udp-users').textContent = data.udp_server.active_users.length;

        state.clients = data.clients || [];
        renderClients('tcp');
        renderClients('udp');
        renderCsvFileList(data.csv_files || []);
      } catch (e) {
        console.error(e);
      }
    }

    function renderClients(proto) {
      const listEl = document.getElementById(`${proto}-client-list`);
      const boxEl = document.getElementById(`${proto}-chat-box`);
      const protoClients = state.clients.filter(c => c.proto === proto && c.connected);

      if (protoClients.length === 0) {
        state.selectedClient[proto] = null;
        listEl.innerHTML = `<span style="color:var(--text-muted); font-size:12px;">Belum ada client ${proto.toUpperCase()} aktif.</span>`;
        boxEl.innerHTML = `<div style="color:var(--text-muted); text-align:center; margin-top:110px;">Tambahkan client ${proto.toUpperCase()} (JOIN) untuk mulai mengirim pesan dan menguji /ping.</div>`;
        return;
      }

      if (!state.selectedClient[proto] || !protoClients.some(c => c.client_id === state.selectedClient[proto])) {
        state.selectedClient[proto] = protoClients[0].client_id;
      }

      listEl.innerHTML = protoClients.map(c => {
        const isSel = c.client_id === state.selectedClient[proto];
        const cls = isSel ? `client-chip active-${proto}` : 'client-chip';
        const rttBadge = c.last_rtt_ms !== null ? `<span style="opacity:0.85">(${c.last_rtt_ms} ms)</span>` : '';
        return `<div class="${cls}" onclick="selectClient('${proto}', '${c.client_id}')">
          <span>👤 ${c.user} [seq=${c.seq}]</span> ${rttBadge}
        </div>`;
      }).join('');

      const activeObj = protoClients.find(c => c.client_id === state.selectedClient[proto]);
      if (!activeObj) return;

      boxEl.innerHTML = activeObj.messages.map(m => {
        let cls = 'chat-msg ' + (m.direction === 'OUT' ? 'out' : 'in');
        if (m.type === 'PONG') cls = 'chat-msg pong';
        if (m.warning) cls = 'chat-msg warn';
        const warnHtml = m.warning ? `<div style="color:#fbbf24; font-weight:700; font-size:12px; margin-top:4px;">⚠️ [PERINGATAN] ${m.warning}</div>` : '';
        return `<div class="${cls}">
          <div class="msg-meta">
            <span><strong>[${m.sender}]</strong> · ${m.type} · seq=${m.seq}</span>
            <span>${m.wire_size} B (${m.proto.toUpperCase()})</span>
          </div>
          <div>${escapeHtml(m.data)}</div>
          ${warnHtml}
          <div class="wire-tag">Wire: ${escapeHtml(m.wire_preview)}</div>
        </div>`;
      }).join('');
      boxEl.scrollTop = boxEl.scrollHeight;
    }

    function selectClient(proto, clientId) {
      state.selectedClient[proto] = clientId;
      renderClients(proto);
    }

    async function joinClient(proto, customUser = null) {
      const input = document.getElementById(`${proto}-new-user`);
      const user = (customUser || input.value).trim();
      if (!user) return showToast('Masukkan username terlebih dahulu!');
      try {
        const res = await api('/api/clients/join', 'POST', { proto, user });
        if (!customUser) input.value = '';
        state.selectedClient[proto] = res.client.client_id;
        showToast(`JOIN ${proto.toUpperCase()} berhasil sebagai '${user}'`);
        await pollStatus();
      } catch (e) {
        showToast(`Gagal JOIN: ${e.message}`);
      }
    }

    async function quickSeedClients(proto) {
      const u1 = `alice_${proto}`;
      const u2 = `bob_${proto}`;
      await joinClient(proto, u1);
      await joinClient(proto, u2);
      state.selectedClient[proto] = `${proto}_${u1}`;
      await pollStatus();
    }

    async function sendChatMsg(proto) {
      const cid = state.selectedClient[proto];
      if (!cid) return showToast(`Pilih atau JOIN client ${proto.toUpperCase()} terlebih dahulu`);
      const input = document.getElementById(`${proto}-msg-input`);
      const text = input.value.trim();
      if (!text) return;
      try {
        await api('/api/clients/action', 'POST', { client_id: cid, action: 'msg', text });
        input.value = '';
        await pollStatus();
      } catch (e) {
        showToast(e.message);
      }
    }

    async function sendPing(proto) {
      const cid = state.selectedClient[proto];
      if (!cid) return showToast(`Pilih atau JOIN client ${proto.toUpperCase()} terlebih dahulu`);
      try {
        const res = await api('/api/clients/action', 'POST', { client_id: cid, action: 'ping' });
        const rtt = res.client.last_rtt_ms;
        showToast(rtt !== null ? `PONG diterima (${proto.toUpperCase()}): RTT = ${rtt} ms` : `PING dikirim (${proto.toUpperCase()})`);
        await pollStatus();
      } catch (e) {
        showToast(e.message);
      }
    }

    async function leaveCurrentClient(proto) {
      const cid = state.selectedClient[proto];
      if (!cid) return;
      try {
        await api('/api/clients/action', 'POST', { client_id: cid, action: 'leave' });
        showToast(`Client ${cid} keluar (LEAVE)`);
        await pollStatus();
      } catch (e) {
        showToast(e.message);
      }
    }

    async function triggerBenchmark() {
      const btn = document.getElementById('btn-run-bm');
      btn.disabled = true;
      btn.textContent = '⏳ Menjalankan Benchmark...';
      try {
        const payload = {
          proto: document.getElementById('bm-proto').value,
          mode: document.getElementById('bm-mode').value,
          count: parseInt(document.getElementById('bm-count').value, 10),
          size: parseInt(document.getElementById('bm-size').value, 10),
          interval: parseFloat(document.getElementById('bm-interval').value),
          scenario: document.getElementById('bm-scenario').value,
          run: parseInt(document.getElementById('bm-run').value, 10)
        };
        const res = await api('/api/benchmark/run', 'POST', payload);
        state.lastBenchResults = res.results || [];
        renderBenchmarkResults(state.lastBenchResults);
        showToast('Benchmark selesai! CSV telah disimpan di results/');
        await pollStatus();
      } catch (e) {
        showToast(`Benchmark gagal: ${e.message}`);
      } finally {
        btn.disabled = false;
        btn.textContent = '▶ Jalankan Benchmark';
      }
    }

    function renderBenchmarkResults(results) {
      const tbody = document.getElementById('bm-results-tbody');
      if (!results || results.length === 0) return;

      const tcpRes = results.find(r => r.proto === 'tcp');
      const udpRes = results.find(r => r.proto === 'udp');

      if (tcpRes) {
        document.getElementById('m-tcp-rtt').textContent = tcpRes.mode === 'ping' ? `${tcpRes.rtt_avg_ms.toFixed(3)} ms` : 'N/A (MSG)';
        document.getElementById('m-tcp-tp').textContent = `${(tcpRes.throughput_bps / 1024).toFixed(1)} KB/s`;
      }
      if (udpRes) {
        document.getElementById('m-udp-rtt').textContent = udpRes.mode === 'ping' ? `${udpRes.rtt_avg_ms.toFixed(3)} ms` : 'N/A (MSG)';
        document.getElementById('m-udp-tp').textContent = `${(udpRes.throughput_bps / 1024).toFixed(1)} KB/s`;
      }

      tbody.innerHTML = results.map(r => `
        <tr>
          <td><span class="badge badge-${r.proto}">${r.proto.toUpperCase()}</span></td>
          <td>${r.mode.toUpperCase()}</td>
          <td>${r.sent}</td>
          <td>${r.received}</td>
          <td>${r.lost}</td>
          <td>${r.out_of_order}</td>
          <td>${r.rtt_avg_ms.toFixed(3)}</td>
          <td>${r.rtt_stddev_ms.toFixed(3)}</td>
          <td>${r.throughput_bps.toFixed(1)}</td>
        </tr>
      `).join('');

      drawChart(results);
    }

    function drawChart(results) {
      const canvas = document.getElementById('rttChart');
      const ctx = canvas.getContext('2d');
      const W = canvas.width;
      const H = canvas.height;
      ctx.clearRect(0, 0, W, H);

      const padL = 50, padR = 20, padT = 20, padB = 30;
      const plotW = W - padL - padR;
      const plotH = H - padT - padB;

      let allValues = [];
      results.forEach(r => {
        (r.series || []).forEach(pt => {
          if (pt.rtt_ms !== null) allValues.push(pt.rtt_ms);
        });
      });
      const maxY = allValues.length > 0 ? Math.max(...allValues) * 1.15 : 1.0;

      // Grid lines
      ctx.strokeStyle = '#1e293b';
      ctx.fillStyle = '#94a3b8';
      ctx.font = '11px Consolas, monospace';
      for (let i = 0; i <= 4; i++) {
        const y = padT + (plotH * i) / 4;
        ctx.beginPath();
        ctx.moveTo(padL, y);
        ctx.lineTo(W - padR, y);
        ctx.stroke();
        const val = (maxY * (4 - i) / 4).toFixed(2);
        ctx.fillText(`${val}ms`, 4, y + 4);
      }

      results.forEach(r => {
        const pts = (r.series || []).filter(p => p.rtt_ms !== null);
        if (pts.length === 0) return;
        ctx.strokeStyle = r.proto === 'tcp' ? '#3b82f6' : '#10b981';
        ctx.lineWidth = 2;
        ctx.beginPath();
        pts.forEach((pt, idx) => {
          const x = padL + (idx / Math.max(1, pts.length - 1)) * plotW;
          const y = padT + plotH - (pt.rtt_ms / maxY) * plotH;
          if (idx === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.stroke();
      });
    }

    async function runFramingSim() {
      const text = document.getElementById('sim-text').value;
      const splitAt = parseInt(document.getElementById('sim-split').value, 10);
      try {
        const d = await api('/api/framing/simulate', 'POST', { text, split_at: splitAt });
        document.getElementById('sim-output').innerHTML = `
<div><strong>1. Payload JSON Application Layer (${d.json_utf8_len} byte):</strong></div>
<div style="color:#93c5fd; margin-bottom:10px;">${escapeHtml(d.udp.wire_repr)}</div>

<div><strong>2. TCP (SOCK_STREAM) Wire Format (${d.tcp.wire_bytes_len} byte, diakhiri '\\n'):</strong></div>
<div style="color:#60a5fa;">Chunk #1 (recv pertama): "${escapeHtml(d.tcp.chunk_1_repr)}" -> Pesan utuh keluar dari StreamFramer: <strong>${d.tcp.frames_after_chunk_1}</strong> (disimpan di buffer)</div>
<div style="color:#60a5fa; margin-bottom:10px;">Chunk #2 (recv kedua)  : "${escapeHtml(d.tcp.chunk_2_repr)}" -> Pesan utuh keluar dari StreamFramer: <strong>${d.tcp.frames_after_chunk_2}</strong> (Reassembled OK: <strong>${d.tcp.reassembled_ok}</strong>)</div>

<div><strong>3. UDP (SOCK_DGRAM) Wire Format (${d.udp.wire_bytes_len} byte, tanpa '\\n'):</strong></div>
<div style="color:#34d399;">Datagram Tunggal (recvfrom 65535): "${escapeHtml(d.udp.wire_repr)}" (Header UDP = 8 byte vs TCP = 20 byte)</div>
        `.trim();
      } catch (e) {
        console.error(e);
      }
    }

    async function refreshServerLogs() {
      try {
        const tbody = document.getElementById('server-log-tbody');
        let combined = [];
        for (const f of ['server_tcp.csv', 'server_udp.csv']) {
          try {
            const d = await api(`/api/csv/read?file=${encodeURIComponent(f)}&limit=30`);
            d.rows.forEach(r => combined.push(r));
          } catch (_) {}
        }
        combined.sort((a, b) => parseFloat(b[0] || 0) - parseFloat(a[0] || 0));
        tbody.innerHTML = combined.slice(0, 40).map(r => `
          <tr>
            <td><span class="badge badge-${r[1]}">${(r[1] || '').toUpperCase()}</span></td>
            <td>${r[2] || ''}</td>
            <td>${r[3] || ''}</td>
            <td>${r[4] || ''}</td>
            <td>${escapeHtml(r[5] || '')}</td>
            <td>${r[6] || ''}</td>
            <td>${escapeHtml(r[7] || '')}</td>
          </tr>
        `).join('');
      } catch (e) {
        console.error(e);
      }
    }

    function renderCsvFileList(files) {
      const container = document.getElementById('csv-file-list');
      if (files.length === 0) {
        container.innerHTML = `<div style="color:var(--text-muted); font-size:12px;">Belum ada file CSV.</div>`;
        return;
      }
      container.innerHTML = files.map(f => `
        <div class="client-chip" style="justify-content:space-between;" onclick="previewCsv('${f.name}')">
          <span>📄 ${f.name}</span>
          <span style="color:var(--text-muted)">${f.size_bytes} B</span>
        </div>
      `).join('');
    }

    async function previewCsv(filename) {
      try {
        const d = await api(`/api/csv/read?file=${encodeURIComponent(filename)}&limit=100`);
        document.getElementById('csv-preview-title').textContent = `${d.filename} (${d.total_rows} baris)`;
        const dl = document.getElementById('csv-download-link');
        dl.href = `/api/csv/download?file=${encodeURIComponent(filename)}`;
        dl.style.display = 'inline-flex';

        const table = document.getElementById('csv-preview-table');
        table.querySelector('thead').innerHTML = `<tr>${d.headers.map(h => `<th>${escapeHtml(h)}</th>`).join('')}</tr>`;
        table.querySelector('tbody').innerHTML = d.rows.map(r => `
          <tr>${r.map(c => `<td>${escapeHtml(c)}</td>`).join('')}</tr>
        `).join('');
      } catch (e) {
        showToast(e.message);
      }
    }

    function escapeHtml(str) {
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    setInterval(pollStatus, 1200);
    pollStatus();
  </script>
</body>
</html>
"""


# =====================================================================
# HTTP REQUEST HANDLER UNTUK WEB UI & REST API
# =====================================================================

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
            ok, result = self.controller.create_client(proto=proto, user=user)
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
