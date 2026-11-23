"""
server.py - Server aplikasi DualChat yang mendukung mode TCP, UDP, atau keduanya (both)
secara bersamaan.

Catatan Penting untuk Laporan Jaringan Komputer:
- Logika server di kelas `ProtocolServerWorker` bekerja secara IDENTIK tanpa
  percabangan `if proto == "udp"`.
- Saat dijalankan dengan `--proto both`, dua worker dijalankan pada thread terpisah
  dengan daftar client (room state) dan file CSV log yang terpisah:
  `results/server_tcp.csv` dan `results/server_udp.csv`.
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from chat_core import (
    DEFAULT_HOST,
    DEFAULT_INACTIVITY_TIMEOUT,
    DEFAULT_TCP_PORT,
    DEFAULT_UDP_PORT,
    SERVER_CSV_COLUMNS,
    ChatServerEngine,
    CsvLogger,
)
from transport import Peer, create_transport


class ProtocolServerWorker:
    """
    Worker server generik untuk satu protokol transport (TCP atau UDP).
    Seluruh logika di dalam kelas ini bebas dari detail spesifik protokol.
    """

    def __init__(
        self,
        proto: str,
        host: str,
        port: int,
        log_dir: str | Path = "results",
        inactivity_timeout: float = DEFAULT_INACTIVITY_TIMEOUT,
    ) -> None:
        self.proto = proto.strip().lower()
        self.host = host
        self.port = port
        self.log_dir = Path(log_dir)
        self.inactivity_timeout = inactivity_timeout

        self.logger = logging.getLogger(f"DualChat.Server.{self.proto.upper()}")
        csv_path = self.log_dir / f"server_{self.proto}.csv"
        self.csv_logger = CsvLogger(filepath=csv_path, columns=SERVER_CSV_COLUMNS)

        self.engine = ChatServerEngine(
            proto_label=self.proto,
            csv_logger=self.csv_logger,
            logger=self.logger,
        )

        self.transport = create_transport(
            proto=self.proto,
            on_error=self._on_transport_error,
            on_disconnect=self._on_transport_disconnect,
            logger=self.logger,
        )

        self._stop_event = threading.Event()
        self._main_thread: threading.Thread | None = None
        self._reaper_thread: threading.Thread | None = None

    @property
    def bound_port(self) -> int:
        """Mengembalikan port aktual tempat server mendengarkan (berguna jika port awal = 0)."""
        return self.transport.bound_port

    def _on_transport_error(self, reason: str, size_bytes: int, peer: Peer) -> None:
        """Callback saat transport menerima paket/frame yang rusak atau tidak valid."""
        try:
            self.engine.record_error(reason=reason, size_bytes=size_bytes, peer=peer)
        except Exception as exc:
            self.logger.error("Gagal mencatat error dari %s: %s", peer, exc)

    def _on_transport_disconnect(self, peer: Peer) -> None:
        """Callback saat koneksi peer terputus."""
        try:
            outgoing = self.engine.handle_disconnect(peer)
            self._dispatch_outgoing(outgoing)
        except Exception as exc:
            self.logger.error("Error saat menangani disconnect %s: %s", peer, exc)

    def _dispatch_outgoing(self, outgoing: list[tuple[Any, dict[str, Any]]]) -> None:
        """Mengirimkan daftar pesan balasan/broadcast melalui transport dan mencatat ke CSV."""
        for target_peer, msg_dict in outgoing:
            try:
                sent_bytes = self.transport.server_send(msg_dict, target_peer)
                if sent_bytes > 0:
                    self.engine.log_event(
                        event="SEND",
                        msg_type=msg_dict["type"],
                        seq=msg_dict["seq"],
                        user=msg_dict["user"],
                        size_bytes=sent_bytes,
                        peer=target_peer,
                    )
            except Exception as exc:
                self.logger.error("Gagal mengirim pesan ke %s: %s", target_peer, exc)

    def start(self) -> None:
        """Memulai socket server, thread penerima pesan, dan thread pembersih client pasif."""
        self._stop_event.clear()
        self.transport.server_listen(self.host, self.port)

        self._main_thread = threading.Thread(
            target=self._serve_loop,
            name=f"ServerLoop-{self.proto.upper()}",
            daemon=True,
        )
        self._reaper_thread = threading.Thread(
            target=self._reaper_loop,
            name=f"ReaperLoop-{self.proto.upper()}",
            daemon=True,
        )
        self._main_thread.start()
        self._reaper_thread.start()

    def _serve_loop(self) -> None:
        """Loop utama penerima dan pemroses pesan server."""
        while not self._stop_event.is_set():
            try:
                msg_dict, peer = self.transport.server_recv(timeout=0.2)
                if msg_dict is None or peer is None:
                    continue

                outgoing = self.engine.handle_message(
                    msg=msg_dict,
                    raw_size_bytes=peer.last_recv_size,
                    peer=peer,
                )
                self._dispatch_outgoing(outgoing)
            except Exception as exc:
                # Tangkap exception per pesan agar server tidak pernah crash karena 1 client bermasalah
                self.logger.error("Exception tidak terduga pada loop server: %s", exc)

    def _reaper_loop(self) -> None:
        """
        Thread berkala untuk memeriksa dan menghapus client yang tidak aktif
        melebihi batas waktu (`inactivity_timeout`, default 60 detik).
        """
        check_interval = min(1.0, max(0.05, self.inactivity_timeout / 4.0))
        while not self._stop_event.wait(timeout=check_interval):
            try:
                outgoing = self.engine.reap_inactive_clients(timeout_sec=self.inactivity_timeout)
                if outgoing:
                    self._dispatch_outgoing(outgoing)
            except Exception as exc:
                self.logger.error("Exception pada reaper thread: %s", exc)

    def stop(self) -> None:
        """Menghentikan server dengan rapi: menutup socket dan melakukan flush file CSV."""
        self._stop_event.set()
        self.transport.close()

        if self._main_thread is not None and self._main_thread.is_alive():
            if threading.current_thread() is not self._main_thread:
                self._main_thread.join(timeout=1.5)

        if self._reaper_thread is not None and self._reaper_thread.is_alive():
            if threading.current_thread() is not self._reaper_thread:
                self._reaper_thread.join(timeout=1.5)

        self.csv_logger.close()
        self.logger.info("Server %s dihentikan dengan rapi.", self.proto.upper())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Mem-parsing argumen baris perintah (CLI) untuk server."""
    parser = argparse.ArgumentParser(
        description="Server DualChat (Perbandingan TCP vs UDP pada Application Layer)"
    )
    parser.add_argument(
        "--proto",
        choices=["tcp", "udp", "both"],
        default="both",
        help="Protokol transport yang dijalankan: tcp, udp, atau both (default: both)",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Alamat IP bind server (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--tcp-port",
        type=int,
        default=DEFAULT_TCP_PORT,
        help=f"Port untuk server TCP (default: {DEFAULT_TCP_PORT})",
    )
    parser.add_argument(
        "--udp-port",
        type=int,
        default=DEFAULT_UDP_PORT,
        help=f"Port untuk server UDP (default: {DEFAULT_UDP_PORT})",
    )
    parser.add_argument(
        "--log-dir",
        default="results/",
        help="Direktori penyimpanan log CSV (default: results/)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Tampilkan log debug lebih detail di konsol",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Fungsi utama untuk menjalankan server DualChat dari CLI."""
    args = parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    os.makedirs(args.log_dir, exist_ok=True)

    # Tentukan daftar protokol dan port yang akan dijalankan
    targets: list[tuple[str, int]] = []
    if args.proto in ("tcp", "both"):
        targets.append(("tcp", args.tcp_port))
    if args.proto in ("udp", "both"):
        targets.append(("udp", args.udp_port))

    workers: list[ProtocolServerWorker] = []
    try:
        for proto_name, port_num in targets:
            worker = ProtocolServerWorker(
                proto=proto_name,
                host=args.host,
                port=port_num,
                log_dir=args.log_dir,
            )
            worker.start()
            workers.append(worker)

        print("========================================================")
        print(" DualChat Server Aktif")
        for w in workers:
            print(f" - Mode {w.proto.upper():3s} : {w.host}:{w.bound_port} (Log: {w.csv_logger.filepath})")

        # Deteksi dan tampilkan IP LAN untuk kemudahan koneksi dari HP lain
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
            print(f" [LAN IP] Untuk koneksi dari HP lain di Wi-Fi:")
            print(f"          Gunakan Host: {lan_ip}")
        except Exception:
            pass

        print(" Tekan Ctrl+C untuk menghentikan server dengan rapi.")
        print("========================================================")

        # Tahan thread utama sampai dihentikan dengan Ctrl+C
        while True:
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n[INFO] Menerima sinyal Ctrl+C, menutup server dan menyimpan log CSV...")
    finally:
        for w in workers:
            w.stop()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
