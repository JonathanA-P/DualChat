"""
client.py - Client interaktif berbasis CLI untuk aplikasi DualChat (TCP & UDP).

Fitur Utama:
- Mendukung mode `--proto tcp` maupun `--proto udp`.
- Melakukan registrasi aplikasi (`JOIN` -> tunggu `ACK_JOIN`).
  Pada UDP, jika `ACK_JOIN` tidak datang dalam 2 detik, tampilkan "JOIN gagal/hilang"
  dan langsung keluar TANPA retry.
- Satu thread khusus menerima dan menampilkan pesan masuk (`[user] isi pesan`).
- Melacak nomor urut (`seq`) per pengirim dan menampilkan peringatan
  "pesan hilang/out-of-order" apabila ada nomor urut yang terlewat atau acak.
- Mendukung perintah `/ping` (mengukur RTT dalam milidetik) dan `/quit` (mengirim `LEAVE`).
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from chat_core import (
    DEFAULT_HOST,
    DEFAULT_TCP_PORT,
    DEFAULT_UDP_PORT,
    make_message,
)
from transport import TcpTransport, UdpTransport, create_transport


class InteractiveChatClient:
    """
    Kelas client interaktif yang menjalankan loop input pengguna dan thread penerima pesan.
    """

    def __init__(
        self,
        proto: str,
        host: str,
        port: int,
        user: str,
        logger: logging.Logger | None = None,
    ) -> None:
        self.proto = proto.strip().lower()
        self.host = host
        self.port = port
        self.user = user
        self.logger = logger or logging.getLogger(f"DualChat.Client.{self.proto.upper()}")

        self.transport: TcpTransport | UdpTransport = create_transport(
            proto=self.proto,
            logger=self.logger,
        )
        self._seq: int = 0
        self._seq_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._recv_thread: threading.Thread | None = None

        # Melacak nomor urut (seq) terakhir yang diharapkan dari setiap pengirim
        self._expected_seq_by_user: dict[str, int] = {}

    def _next_seq(self) -> int:
        """Mengambil nomor urut pengiriman berikutnya (mulai dari 0, naik 1 per pesan)."""
        with self._seq_lock:
            current = self._seq
            self._seq += 1
            return current

    def connect_and_join(self, udp_join_timeout: float = 2.0) -> bool:
        """
        Menghubungkan transport dan melakukan proses JOIN.
        - TCP: menunggu sampai menerima ACK_JOIN, ERR, atau koneksi terputus.
        - UDP: menunggu maksimal `udp_join_timeout` (2 detik). Jika tidak ada balasan,
          tampilkan "JOIN gagal/hilang" dan keluar tanpa retry.
        """
        try:
            self.transport.client_connect(self.host, self.port)
        except OSError as exc:
            print(f"[ERROR] Gagal menghubungkan socket ke {self.host}:{self.port}: {exc}")
            return False

        join_msg = make_message(
            msg_type="JOIN",
            seq=self._next_seq(),
            user=self.user,
            data="",
        )

        try:
            self.transport.client_send(join_msg)
        except OSError as exc:
            print(f"[ERROR] Gagal mengirim pesan JOIN: {exc}")
            return False

        # Tentukan batas waktu tunggu ACK_JOIN
        wait_timeout = None if self.proto == "tcp" else udp_join_timeout
        deadline = None if wait_timeout is None else (time.monotonic() + wait_timeout)

        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if remaining is not None and remaining <= 0.0:
                print("JOIN gagal/hilang")
                return False

            reply = self.transport.client_recv(timeout=remaining)
            if reply is None:
                if self.proto == "udp":
                    print("JOIN gagal/hilang")
                else:
                    print("[ERROR] Koneksi terputus sebelum menerima ACK_JOIN.")
                return False

            msg_type = reply.get("type")
            if msg_type == "ACK_JOIN":
                print(f"[SERVER] {reply.get('data', 'JOIN berhasil')}")
                return True
            if msg_type == "ERR":
                print(f"[ERROR] JOIN ditolak server: {reply.get('data', '')}")
                return False

    def _check_sequence(self, sender: str, seq: int) -> None:
        """
        Memeriksa apakah nomor urut (`seq`) dari seorang pengirim berurutan.
        Jika terlewat atau tidak berurutan, tampilkan peringatan "pesan hilang/out-of-order".
        """
        if sender == "server":
            return

        expected = self._expected_seq_by_user.get(sender)
        if expected is not None and seq != expected:
            print(
                f"[PERINGATAN] pesan hilang/out-of-order dari [{sender}]: "
                f"diharapkan seq={expected}, diterima seq={seq}"
            )
        self._expected_seq_by_user[sender] = seq + 1

    def _receiver_loop(self) -> None:
        """Thread khusus untuk menerima dan menampilkan pesan yang masuk dari server."""
        while not self._stop_event.is_set():
            msg = self.transport.client_recv(timeout=0.3)
            if msg is None:
                continue

            msg_type = msg.get("type")
            sender = msg.get("user", "unknown")
            seq = int(msg.get("seq", 0))
            data = msg.get("data", "")

            if msg_type == "MSG":
                self._check_sequence(sender, seq)
                print(f"[{sender}] {data}")
            elif msg_type == "PONG":
                rtt_ms = (time.time() - float(msg["ts"])) * 1000.0
                print(f"[PONG] seq={seq} RTT = {rtt_ms:.3f} ms")
            elif msg_type == "ERR":
                print(f"[ERROR SERVER] {data}")

    def run_interactive(self) -> int:
        """Menjalankan banner, proses JOIN, thread penerima, dan loop baca input keyboard."""
        print("========================================================")
        print(f" DualChat Interactive Client | Protokol Aktif: {self.proto.upper()}")
        print(f" Server Target : {self.host}:{self.port}")
        print(f" Username      : {self.user}")
        print(" Perintah      : ketik pesan biasa, /ping, atau /quit")
        print("========================================================")

        try:
            if not self.connect_and_join(udp_join_timeout=2.0):
                return 1

            self._stop_event.clear()
            self._recv_thread = threading.Thread(
                target=self._receiver_loop,
                name="ClientReceiverThread",
                daemon=True,
            )
            self._recv_thread.start()

            while not self._stop_event.is_set():
                try:
                    line = input()
                except EOFError:
                    # Input stream ditutup, keluar dengan mengirim LEAVE
                    self._send_leave()
                    break

                text = line.strip()
                if not text:
                    continue

                if text == "/quit":
                    self._send_leave()
                    break
                elif text == "/ping":
                    ping_msg = make_message(
                        msg_type="PING",
                        seq=self._next_seq(),
                        user=self.user,
                        data="",
                    )
                    self.transport.client_send(ping_msg)
                else:
                    chat_msg = make_message(
                        msg_type="MSG",
                        seq=self._next_seq(),
                        user=self.user,
                        data=line,
                    )
                    self.transport.client_send(chat_msg)

        except KeyboardInterrupt:
            print("\n[INFO] Keluar dari chat (Ctrl+C)...")
            self._send_leave()
        except OSError as exc:
            print(f"\n[ERROR] Koneksi bermasalah: {exc}")
            return 1
        finally:
            self.close()

        return 0

    def _send_leave(self) -> None:
        """Mengirim pesan LEAVE ke server sebelum menutup client."""
        try:
            leave_msg = make_message(
                msg_type="LEAVE",
                seq=self._next_seq(),
                user=self.user,
                data="",
            )
            self.transport.client_send(leave_msg)
        except Exception:
            pass

    def close(self) -> None:
        """Menghentikan thread penerima dan menutup socket transport."""
        self._stop_event.set()
        self.transport.close()
        if self._recv_thread is not None and self._recv_thread.is_alive():
            if threading.current_thread() is not self._recv_thread:
                self._recv_thread.join(timeout=1.0)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Mem-parsing argumen CLI untuk client interaktif."""
    parser = argparse.ArgumentParser(
        description="Client Interaktif DualChat (TCP vs UDP)"
    )
    parser.add_argument(
        "--proto",
        required=True,
        choices=["tcp", "udp"],
        help="Protokol transport yang digunakan: tcp atau udp (wajib)",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Alamat IP server tujuan (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"Port server tujuan (default: {DEFAULT_TCP_PORT} untuk TCP, {DEFAULT_UDP_PORT} untuk UDP)",
    )
    parser.add_argument(
        "--user",
        required=True,
        help="Username unik untuk bergabung ke ruang chat (wajib)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Tampilkan log debug di konsol",
    )
    args = parser.parse_args(argv)
    if args.port is None:
        args.port = DEFAULT_TCP_PORT if args.proto == "tcp" else DEFAULT_UDP_PORT
    return args


def main(argv: list[str] | None = None) -> int:
    """Entry point utama untuk menjalankan client interaktif."""
    args = parse_args(argv)
    log_level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    client = InteractiveChatClient(
        proto=args.proto,
        host=args.host,
        port=args.port,
        user=args.user,
    )
    return client.run_interactive()


if __name__ == "__main__":
    sys.exit(main())
