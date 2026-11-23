"""
chat_core.py - Logika inti protokol Application Layer, framing buffer, CSV logger,
dan mesin pemroses pesan server untuk aplikasi DualChat.

Catatan Penting untuk Laporan Jaringan Komputer:
- Modul ini berada murni di Application Layer.
- DILARANG ada kode socket atau percabangan `if proto == "udp"` di dalam file ini.
- Logika di sini dijalankan secara IDENTIK baik di atas TCP maupun UDP.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

# =====================================================================
# KONSTANTA PROTOKOL APLIKASI
# =====================================================================

VALID_TYPES: set[str] = {
    "JOIN",
    "ACK_JOIN",
    "MSG",
    "PING",
    "PONG",
    "LEAVE",
    "ERR",
}

MAX_DATA_BYTES: int = 65535
UDP_BUFFER_SIZE: int = 65535

DEFAULT_HOST: str = "127.0.0.1"
DEFAULT_TCP_PORT: int = 5000
DEFAULT_UDP_PORT: int = 5001
DEFAULT_INACTIVITY_TIMEOUT: float = 60.0

SERVER_CSV_COLUMNS: list[str] = [
    "timestamp",
    "proto",
    "event",
    "type",
    "seq",
    "user",
    "size_bytes",
    "peer",
]

BENCH_CSV_COLUMNS: list[str] = [
    "proto",
    "scenario",
    "run",
    "event",
    "type",
    "seq",
    "size_bytes",
    "t_send",
    "t_recv",
    "rtt_ms",
]


# =====================================================================
# PEMBUATAN, ENCODE, DAN DECODE PESAN JSON
# =====================================================================

def make_message(
    msg_type: str,
    seq: int,
    user: str,
    data: str = "",
    ts: float | None = None,
) -> dict[str, Any]:
    """
    Membuat dictionary pesan sesuai spesifikasi protokol aplikasi DualChat.
    """
    if msg_type not in VALID_TYPES:
        raise ValueError(f"Tipe pesan tidak dikenal: {msg_type}")
    return {
        "type": msg_type,
        "seq": int(seq),
        "ts": float(time.time() if ts is None else ts),
        "user": str(user),
        "data": str(data),
    }


def validate_message_dict(msg: Any) -> tuple[bool, str | None]:
    """
    Memvalidasi apakah sebuah objek adalah dictionary pesan protokol yang sah.
    Mengembalikan (True, None) jika valid, atau (False, alasan_error) jika tidak valid.
    """
    if not isinstance(msg, dict):
        return False, "Pesan bukan objek JSON (dict)"

    required_fields = ("type", "seq", "ts", "user", "data")
    for field in required_fields:
        if field not in msg:
            return False, f"Field wajib '{field}' tidak ditemukan"

    msg_type = msg["type"]
    seq = msg["seq"]
    ts = msg["ts"]
    user = msg["user"]
    data = msg["data"]

    if not isinstance(msg_type, str) or msg_type not in VALID_TYPES:
        return False, f"Field 'type' tidak dikenal: {msg_type!r}"

    # Di Python, bool adalah subclass dari int, jadi harus dicek eksplisit
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        return False, f"Field 'seq' harus integer >= 0, diterima: {seq!r}"

    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return False, f"Field 'ts' harus float/int, diterima: {ts!r}"

    if not isinstance(user, str) or not user.strip():
        return False, "Field 'user' harus string tidak kosong"

    if not isinstance(data, str):
        return False, "Field 'data' harus berupa string"

    data_bytes_len = len(data.encode("utf-8"))
    if data_bytes_len > MAX_DATA_BYTES:
        return (
            False,
            f"Ukuran 'data' ({data_bytes_len} byte) melebihi batas maksimum {MAX_DATA_BYTES} byte",
        )

    return True, None


def encode_message(msg_dict: dict[str, Any]) -> bytes:
    """
    Mengubah dictionary pesan menjadi byte JSON UTF-8 (tanpa delimiter newline).
    json.dumps secara otomatis melakukan escape terhadap karakter newline ('\\n')
    di dalam isi string 'data', sehingga aman untuk framing berbasis baris di TCP.
    """
    is_valid, err_reason = validate_message_dict(msg_dict)
    if not is_valid:
        raise ValueError(f"Pesan tidak valid untuk di-encode: {err_reason}")

    # separators=(",", ":") menjaga ukuran byte tetap ringkas dan deterministik
    json_str = json.dumps(msg_dict, ensure_ascii=False, separators=(",", ":"))
    return json_str.encode("utf-8")


def decode_message(raw_bytes: bytes) -> tuple[dict[str, Any] | None, str | None]:
    """
    Mengubah byte JSON UTF-8 menjadi dictionary pesan dan memvalidasi isinya.
    Jika rusak/tidak valid, TIDAK melempar exception melainkan mengembalikan
    (None, alasan_error) agar server/client tidak crash.
    """
    if not isinstance(raw_bytes, (bytes, bytearray)):
        return None, "Input bukan bytes"

    if len(raw_bytes) == 0:
        return None, "Pesan kosong (0 byte)"

    try:
        text = bytes(raw_bytes).decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, f"Gagal decode UTF-8: {exc}"

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"JSON rusak: {exc}"

    is_valid, err_reason = validate_message_dict(parsed)
    if not is_valid:
        return None, err_reason

    # Normalisasi tipe ts menjadi float
    parsed["ts"] = float(parsed["ts"])
    return parsed, None


# =====================================================================
# FRAMING BUFFER UNTUK STREAM TCP
# =====================================================================

class StreamFramer:
    """
    Buffer penerima untuk protokol berbasis byte stream (TCP).

    Mengapa ini diperlukan di TCP?
    TCP adalah protokol byte-stream: tidak ada batas pesan bawaan.
    - Satu panggilan send() bisa terpecah menjadi beberapa recv().
    - Beberapa panggilan send() bisa bergabung dalam satu recv() (Nagle/buffering OS).

    Oleh karena itu, setiap pesan diakhiri dengan delimiter '\\n' (0x0A).
    StreamFramer mengakumulasi byte hasil recv(), memisahkan berdasarkan '\\n',
    dan menyimpan sisa byte yang belum lengkap untuk recv() berikutnya.
    """

    def __init__(self) -> None:
        self._buffer = bytearray()

    def append(self, chunk: bytes) -> None:
        """Menambahkan potongan byte baru dari recv() ke dalam buffer internal."""
        if chunk:
            self._buffer.extend(chunk)

    def pop_message(self) -> bytes | None:
        """
        Mengambil satu pesan utuh dari buffer jika sudah ada delimiter '\\n'.
        Mengembalikan None jika belum ada pesan utuh di dalam buffer.
        """
        while True:
            newline_idx = self._buffer.find(b"\n")
            if newline_idx == -1:
                return None

            frame = bytes(self._buffer[:newline_idx])
            del self._buffer[: newline_idx + 1]
            if frame.endswith(b"\r"):
                frame = frame[:-1]
            if len(frame) > 0:
                return frame

    def feed(self, chunk: bytes) -> list[bytes]:
        """
        Memasukkan potongan byte baru dari recv() dan mengembalikan daftar
        pesan lengkap (masing-masing tanpa karakter delimiter '\\n' di ujungnya).
        """
        self.append(chunk)
        messages: list[bytes] = []
        while True:
            frame = self.pop_message()
            if frame is None:
                break
            messages.append(frame)
        return messages

    def has_complete_message(self) -> bool:
        """Memeriksa apakah ada minimal satu pesan lengkap di buffer."""
        return b"\n" in self._buffer

    def clear(self) -> None:
        """Mengosongkan isi buffer."""
        self._buffer.clear()


# =====================================================================
# CSV LOGGER (THREAD-SAFE)
# =====================================================================

class CsvLogger:
    """
    Pencatat log berformat CSV yang thread-safe dan melakukan flush berkala.
    Digunakan baik oleh server maupun benchmark client agar format konsisten.
    """

    def __init__(
        self,
        filepath: str | Path,
        columns: list[str],
        flush_every: int = 1,
    ) -> None:
        self.filepath = Path(filepath)
        self.columns = list(columns)
        self.flush_every = max(1, flush_every)
        self._lock = threading.Lock()
        self._write_count = 0
        self._closed = False

        # Pastikan direktori tujuan (mis. results/) sudah ada
        if self.filepath.parent:
            os.makedirs(self.filepath.parent, exist_ok=True)

        self._file = open(self.filepath, mode="w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=self.columns)
        self._writer.writeheader()
        self._file.flush()

    def log_row(self, row_dict: dict[str, Any]) -> None:
        """Menulis satu baris ke file CSV secara thread-safe."""
        with self._lock:
            if self._closed:
                return
            filtered = {col: row_dict.get(col, "") for col in self.columns}
            self._writer.writerow(filtered)
            self._write_count += 1
            if self._write_count % self.flush_every == 0:
                self._file.flush()

    def flush(self) -> None:
        """Memaksa flush buffer file ke disk."""
        with self._lock:
            if not self._closed:
                self._file.flush()

    def close(self) -> None:
        """Menutup file CSV dengan rapi."""
        with self._lock:
            if not self._closed:
                self._file.flush()
                self._file.close()
                self._closed = True

    def __enter__(self) -> CsvLogger:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


# =====================================================================
# LOGIKA INTI SERVER (CHAT ROOM ENGINE)
# =====================================================================

class ChatServerEngine:
    """
    Mesin pemroses logika pesan chat di sisi server.
    Sepenuhnya independen dari jenis socket (TCP/UDP):
    - Mengelola daftar client aktif (username -> peer, peer -> username).
    - Mencatat waktu aktif terakhir tiap peer untuk pembersihan client pasif.
    - Menghasilkan daftar pesan balasan/broadcast untuk dikirim oleh Transport.
    """

    def __init__(
        self,
        proto_label: str,
        csv_logger: CsvLogger | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.proto_label = proto_label
        self.csv_logger = csv_logger
        self.logger = logger or logging.getLogger(f"DualChat.Server.{proto_label.upper()}")

        self._lock = threading.Lock()
        self._user_to_peer: dict[str, Any] = {}
        self._peer_to_user: dict[Any, str] = {}
        self._peer_last_seen: dict[Any, float] = {}
        self._server_seq: int = 0

    def _next_server_seq(self) -> int:
        """Mengambil nomor urut berikutnya untuk pesan yang dibuat oleh server."""
        seq = self._server_seq
        self._server_seq += 1
        return seq

    def log_event(
        self,
        event: str,
        msg_type: str,
        seq: int | str,
        user: str,
        size_bytes: int,
        peer: Any,
    ) -> None:
        """Mencatat event RECV / SEND / ERROR ke dalam file CSV server."""
        if self.csv_logger is None:
            return
        self.csv_logger.log_row(
            {
                "timestamp": f"{time.time():.6f}",
                "proto": self.proto_label,
                "event": event,
                "type": msg_type,
                "seq": seq,
                "user": user,
                "size_bytes": size_bytes,
                "peer": str(peer),
            }
        )

    def record_error(self, reason: str, size_bytes: int, peer: Any) -> None:
        """Mencatat pesan rusak/error protokol ke log konsol dan CSV."""
        self.logger.warning("[%s] Pesan tidak valid dari %s: %s", self.proto_label.upper(), peer, reason)
        self.log_event(
            event="ERROR",
            msg_type="INVALID",
            seq=-1,
            user="UNKNOWN",
            size_bytes=size_bytes,
            peer=peer,
        )

    def handle_message(
        self,
        msg: dict[str, Any],
        raw_size_bytes: int,
        peer: Any,
    ) -> list[tuple[Any, dict[str, Any]]]:
        """
        Memproses satu pesan valid yang diterima dari `peer`.
        Mengembalikan daftar tuple `(target_peer, msg_dict)` yang harus dikirim
        melalui layer transport.
        """
        now = time.time()
        msg_type: str = msg["type"]
        seq: int = msg["seq"]
        ts: float = msg["ts"]
        user: str = msg["user"]
        data: str = msg["data"]

        # Catat penerimaan pesan ke CSV
        self.log_event(
            event="RECV",
            msg_type=msg_type,
            seq=seq,
            user=user,
            size_bytes=raw_size_bytes,
            peer=peer,
        )

        outgoing: list[tuple[Any, dict[str, Any]]] = []

        with self._lock:
            # Perbarui timestamp aktivitas jika peer sudah terdaftar
            if peer in self._peer_to_user:
                self._peer_last_seen[peer] = now

            if msg_type == "JOIN":
                existing_peer = self._user_to_peer.get(user)
                if existing_peer is not None and existing_peer != peer:
                    # Username sudah dipakai oleh peer lain
                    err_msg = make_message(
                        msg_type="ERR",
                        seq=self._next_server_seq(),
                        user="server",
                        data="username sudah dipakai",
                    )
                    outgoing.append((peer, err_msg))
                else:
                    # Jika peer ini sebelumnya pakai username lain, bersihkan dulu
                    old_user = self._peer_to_user.get(peer)
                    if old_user and old_user != user:
                        self._user_to_peer.pop(old_user, None)

                    self._user_to_peer[user] = peer
                    self._peer_to_user[peer] = user
                    self._peer_last_seen[peer] = now

                    # Balas ACK_JOIN ke pengirim
                    ack_msg = make_message(
                        msg_type="ACK_JOIN",
                        seq=seq,
                        user="server",
                        data=f"Selamat datang {user}",
                    )
                    outgoing.append((peer, ack_msg))

                    # Broadcast notifikasi sistem ke client aktif lainnya
                    join_notif = make_message(
                        msg_type="MSG",
                        seq=self._next_server_seq(),
                        user="server",
                        data=f"{user} bergabung",
                    )
                    for other_user, other_peer in self._user_to_peer.items():
                        if other_peer != peer:
                            outgoing.append((other_peer, join_notif))

                    self.logger.info("[%s] User '%s' bergabung dari %s", self.proto_label.upper(), user, peer)

            elif msg_type == "MSG":
                # Pastikan pengirim sudah JOIN dan cocok dengan peer terdaftar
                registered_user = self._peer_to_user.get(peer)
                if registered_user is None or registered_user != user:
                    err_msg = make_message(
                        msg_type="ERR",
                        seq=self._next_server_seq(),
                        user="server",
                        data="Anda belum JOIN",
                    )
                    outgoing.append((peer, err_msg))
                else:
                    # Broadcast salinan pesan ke SEMUA client aktif kecuali pengirim
                    # Termasuk pesan [DM:...], [GROUP:...], [FEED], [FEED_COMMENT], Image, dan Voice
                    forward_msg = make_message(
                        msg_type="MSG",
                        seq=seq,
                        user=user,
                        data=data,
                        ts=ts,
                    )
                    for other_user, other_peer in self._user_to_peer.items():
                        if other_peer != peer:
                            outgoing.append((other_peer, forward_msg))

            elif msg_type == "PING":
                # Balas PONG secepat mungkin dengan seq dan ts yang SAMA persis dengan PING
                pong_msg = make_message(
                    msg_type="PONG",
                    seq=seq,
                    user=user,
                    data="",
                    ts=ts,
                )
                outgoing.append((peer, pong_msg))

            elif msg_type == "LEAVE":
                registered_user = self._peer_to_user.pop(peer, None)
                self._peer_last_seen.pop(peer, None)
                target_user = registered_user or user
                if registered_user is not None:
                    self._user_to_peer.pop(registered_user, None)
                    leave_notif = make_message(
                        msg_type="MSG",
                        seq=self._next_server_seq(),
                        user="server",
                        data=f"{target_user} keluar",
                    )
                    for other_peer in self._user_to_peer.values():
                        outgoing.append((other_peer, leave_notif))
                    self.logger.info("[%s] User '%s' keluar (%s)", self.proto_label.upper(), target_user, peer)

            else:
                # Pesan tipe lain (misal ACK_JOIN / PONG / ERR yang dikirim client ke server)
                self.logger.debug(
                    "[%s] Mengabaikan pesan tipe %s dari %s",
                    self.proto_label.upper(),
                    msg_type,
                    peer,
                )

        return outgoing

    def handle_disconnect(self, peer: Any) -> list[tuple[Any, dict[str, Any]]]:
        """
        Menghapus client ketika koneksi terputus (atau gagal kirim) dan
        menyiapkan broadcast notifikasi keluar ke client lain yang masih aktif.
        """
        outgoing: list[tuple[Any, dict[str, Any]]] = []
        with self._lock:
            username = self._peer_to_user.pop(peer, None)
            self._peer_last_seen.pop(peer, None)
            if username is not None:
                self._user_to_peer.pop(username, None)
                leave_notif = make_message(
                    msg_type="MSG",
                    seq=self._next_server_seq(),
                    user="server",
                    data=f"{username} keluar",
                )
                for other_peer in self._user_to_peer.values():
                    outgoing.append((other_peer, leave_notif))
                self.logger.info(
                    "[%s] Koneksi '%s' (%s) terputus, dihapus dari daftar client",
                    self.proto_label.upper(),
                    username,
                    peer,
                )
        return outgoing

    def reap_inactive_clients(self, timeout_sec: float = DEFAULT_INACTIVITY_TIMEOUT) -> list[tuple[Any, dict[str, Any]]]:
        """
        Memeriksa dan menghapus client yang tidak aktif selama > `timeout_sec` detik.
        Mengembalikan daftar pesan notifikasi untuk dikirim ke client lain yang masih aktif.
        """
        now = time.time()
        outgoing: list[tuple[Any, dict[str, Any]]] = []
        expired_peers: list[tuple[Any, str]] = []

        with self._lock:
            for peer, last_seen in list(self._peer_last_seen.items()):
                if now - last_seen > timeout_sec:
                    username = self._peer_to_user.pop(peer, None)
                    self._peer_last_seen.pop(peer, None)
                    if username is not None:
                        self._user_to_peer.pop(username, None)
                        expired_peers.append((peer, username))

            for peer, username in expired_peers:
                leave_notif = make_message(
                    msg_type="MSG",
                    seq=self._next_server_seq(),
                    user="server",
                    data=f"{username} keluar (timeout tidak aktif)",
                )
                for other_peer in self._user_to_peer.values():
                    outgoing.append((other_peer, leave_notif))
                self.logger.info(
                    "[%s] Client '%s' (%s) dihapus karena tidak aktif > %.1fs",
                    self.proto_label.upper(),
                    username,
                    peer,
                    timeout_sec,
                )

        return outgoing

    def active_users(self) -> list[str]:
        """Mengembalikan daftar username yang sedang aktif (untuk keperluan inspeksi/testing)."""
        with self._lock:
            return list(self._user_to_peer.keys())
