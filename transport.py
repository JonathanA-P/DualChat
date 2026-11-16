"""
transport.py - Implementasi Transport Layer (TcpTransport & UdpTransport).

Catatan Penting untuk Laporan Jaringan Komputer:
- File ini adalah SATU-SATUNYA tempat seluruh kode `socket` berada.
- TcpTransport dan UdpTransport menyediakan antarmuka (interface) method yang SAMA
  sehingga server.py, client.py, dan benchmark.py dapat bekerja dengan logika
  Application Layer yang 100% identik.
- Perbedaan mendasar yang ditunjukkan di file ini:
  1. Handshake & Koneksi:
     * TCP (SOCK_STREAM) melakukan 3-way handshake (SYN, SYN-ACK, ACK) saat
       `connect()` di sisi client dan `accept()` di sisi server.
     * UDP (SOCK_DGRAM) bersifat connectionless: tidak ada handshake, tidak ada
       `listen()` maupun `accept()`.
  2. Framing Pesan:
     * TCP adalah byte-stream: pesan dibatasi dengan karakter newline ('\\n') dan
       dirakit menggunakan `StreamFramer` karena satu `recv()` tidak dijamin
       berisi tepat satu pesan.
     * UDP berbasis datagram: satu panggilan `sendto()` / `recvfrom(65535)` adalah
       satu pesan utuh tanpa delimiter '\\n'.
  3. Keandalan (Reliability):
     * TCP menangani retransmisi, pengurutan (ordering), dan deteksi putus koneksi
       secara otomatis di level kernel/OS.
     * UDP tidak memiliki retransmisi, ACK buatan, atau pengurutan ulang di aplikasi
       agar efek packet loss dan out-of-order terlihat apa adanya.
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
from typing import Any, Callable

from chat_core import (
    UDP_BUFFER_SIZE,
    StreamFramer,
    decode_message,
    encode_message,
)


class Peer:
    """
    Pembungkus (wrapper) identitas client agar logika server di Application Layer
    tidak perlu mengetahui perbedaan antara objek socket koneksi (TCP) dan
    tuple alamat `(ip, port)` (UDP).
    """

    def __init__(
        self,
        raw_handle: socket.socket | tuple[str, int],
        address: tuple[str, int],
    ) -> None:
        self.raw_handle = raw_handle
        self.address = address
        self.last_recv_size: int = 0
        # Untuk TCP: identitas unik adalah objek socket koneksi.
        # Untuk UDP: identitas unik adalah tuple (ip, port).
        if isinstance(raw_handle, tuple):
            self._key: Any = raw_handle
        else:
            self._key = id(raw_handle)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Peer):
            return False
        return self._key == other._key

    def __hash__(self) -> int:
        return hash(self._key)

    def __str__(self) -> str:
        return f"{self.address[0]}:{self.address[1]}"

    def __repr__(self) -> str:
        return f"Peer({self.address[0]}:{self.address[1]})"


# Tipe callback opsional untuk pelaporan error protokol dan putus koneksi ke server
ErrorCallback = Callable[[str, int, Peer], None]
DisconnectCallback = Callable[[Peer], None]


# =====================================================================
# IMPLEMENTASI TCP TRANSPORT (SOCK_STREAM)
# =====================================================================

class TcpTransport:
    """
    Transport berbasis TCP (Transmission Control Protocol - SOCK_STREAM).
    Berorientasi koneksi (connection-oriented), andal, dan berbasis byte-stream.
    """

    proto_name: str = "tcp"

    def __init__(
        self,
        on_error: ErrorCallback | None = None,
        on_disconnect: DisconnectCallback | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.on_error = on_error
        self.on_disconnect = on_disconnect
        self.logger = logger or logging.getLogger("DualChat.Transport.TCP")

        self._server_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._client_framer = StreamFramer()
        self._client_send_lock = threading.Lock()

        # Antrian pesan masuk di sisi server: berisi tuple (msg_dict, peer)
        self._recv_queue: queue.Queue[tuple[dict[str, Any], Peer]] = queue.Queue()
        self._active_peers: dict[Peer, socket.socket] = {}
        self._peers_lock = threading.Lock()
        self._running = False
        self._accept_thread: threading.Thread | None = None

        self.bound_host: str = ""
        self.bound_port: int = 0

    # -----------------------------------------------------------------
    # SISI SERVER (TCP)
    # -----------------------------------------------------------------

    def server_listen(self, host: str, port: int) -> None:
        """
        Membuka socket server TCP:
        1. Membuat socket AF_INET dengan tipe SOCK_STREAM (TCP).
        2. Mengaktifkan opsi SO_REUSEADDR agar port bisa segera dipakai ulang saat restart.
        3. Melakukan bind(host, port) dan listen() untuk antrian koneksi masuk.
        4. Menjalankan thread acceptor untuk menerima koneksi client.
        """
        # [LAPORAN: INISIALISASI SOCKET SERVER TCP]
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(64)
        sock.settimeout(0.5)

        actual_addr = sock.getsockname()
        self.bound_host = str(actual_addr[0])
        self.bound_port = int(actual_addr[1])

        self._server_sock = sock
        self._running = True

        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="TcpAcceptThread",
            daemon=True,
        )
        self._accept_thread.start()
        self.logger.info("Server TCP mendengarkan di %s:%d", self.bound_host, self.bound_port)

    def _accept_loop(self) -> None:
        """
        Thread khusus untuk menerima koneksi TCP yang masuk melalui accept().
        CATATAN LAPORAN:
        Pada panggilan `accept()`, proses 3-way handshake TCP (SYN -> SYN-ACK -> ACK)
        antara client dan server telah selesai di level kernel, dan menghasilkan
        objek socket baru khusus untuk client tersebut.
        """
        while self._running and self._server_sock is not None:
            try:
                conn_sock, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                # Socket server ditutup saat shutdown
                break

            conn_sock.settimeout(0.5)
            peer = Peer(raw_handle=conn_sock, address=(str(addr[0]), int(addr[1])))
            with self._peers_lock:
                self._active_peers[peer] = conn_sock

            worker = threading.Thread(
                target=self._client_reader_loop,
                args=(conn_sock, peer),
                name=f"TcpClientReader-{peer}",
                daemon=True,
            )
            worker.start()

    def _client_reader_loop(self, conn_sock: socket.socket, peer: Peer) -> None:
        """
        Worker thread per koneksi TCP yang membaca byte stream secara kontinu.
        Menggunakan StreamFramer untuk memisahkan pesan berdasarkan delimiter '\\n'.
        Jika recv() mengembalikan b"" (EOF/FIN) atau terjadi error socket, koneksi
        dibersihkan dan server diberi tahu bahwa client telah terputus.
        """
        framer = StreamFramer()
        try:
            while self._running:
                try:
                    chunk = conn_sock.recv(4096)
                except socket.timeout:
                    continue
                except OSError as exc:
                    self.logger.debug("Koneksi TCP %s error saat recv: %s", peer, exc)
                    break

                # Di TCP, recv() mengembalikan b"" (kosong) ketika client menutup koneksi (FIN)
                if not chunk:
                    self.logger.debug("Koneksi TCP %s ditutup oleh client (EOF)", peer)
                    break

                # Akumulasi chunk ke buffer dan pisahkan berdasarkan '\n'
                frames = framer.feed(chunk)
                for raw_frame in frames:
                    msg_dict, err_reason = decode_message(raw_frame)
                    if msg_dict is None:
                        if self.on_error is not None:
                            self.on_error(err_reason or "Pesan tidak valid", len(raw_frame), peer)
                        continue

                    peer.last_recv_size = len(raw_frame)
                    self._recv_queue.put((msg_dict, peer))
        finally:
            self._cleanup_peer(peer)

    def _cleanup_peer(self, peer: Peer) -> None:
        """Menutup socket koneksi client TCP dan memicu callback disconnect."""
        conn_sock: socket.socket | None = None
        with self._peers_lock:
            conn_sock = self._active_peers.pop(peer, None)

        if conn_sock is not None:
            try:
                conn_sock.close()
            except OSError:
                pass
            if self._running and self.on_disconnect is not None:
                try:
                    self.on_disconnect(peer)
                except Exception as exc:
                    self.logger.error("Error pada callback on_disconnect untuk %s: %s", peer, exc)

    def server_recv(self, timeout: float | None = 0.5) -> tuple[dict[str, Any] | None, Peer | None]:
        """
        Mengambil satu pesan yang sudah di-decode dari antrian koneksi client TCP.
        Mengembalikan `(msg_dict, peer)` atau `(None, None)` jika timeout habis.
        """
        try:
            return self._recv_queue.get(timeout=timeout)
        except queue.Empty:
            return None, None

    def server_send(self, msg_dict: dict[str, Any], peer: Peer) -> int:
        """
        Mengirimkan pesan ke satu client TCP tertentu.
        Pesan di-encode ke JSON UTF-8 dan ditambahkan delimiter '\\n' untuk framing TCP.
        Mengembalikan ukuran byte payload JSON (tanpa newline) atau 0 jika gagal.
        """
        raw_json = encode_message(msg_dict)
        wire_bytes = raw_json + b"\n"

        sock_obj = peer.raw_handle
        if not isinstance(sock_obj, socket.socket):
            return 0

        try:
            sock_obj.sendall(wire_bytes)
            return len(raw_json)
        except OSError as exc:
            self.logger.debug("Gagal mengirim ke client TCP %s: %s", peer, exc)
            self._cleanup_peer(peer)
            return 0

    # -----------------------------------------------------------------
    # SISI CLIENT (TCP)
    # -----------------------------------------------------------------

    def client_connect(self, host: str, port: int) -> None:
        """
        Membuka koneksi client TCP ke server:
        CATATAN LAPORAN:
        Pemanggilan `connect((host, port))` di bawah ini memicu pengiriman paket SYN
        dan melakukan 3-way handshake TCP (SYN -> SYN-ACK -> ACK) dengan server.
        Jika server tidak aktif, `connect()` akan langsung melempar ConnectionRefusedError.
        """
        # [LAPORAN: INISIALISASI & HANDSHAKE SOCKET CLIENT TCP]
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))
        self._client_sock = sock
        self._client_framer.clear()
        self._running = True

    def client_send(self, msg_dict: dict[str, Any]) -> int:
        """
        Mengirim pesan dari client ke server melalui stream TCP (diakhiri '\\n').
        Mengembalikan panjang byte pesan JSON hasil encode.
        """
        if self._client_sock is None:
            raise RuntimeError("Client TCP belum terhubung (client_connect belum dipanggil)")

        raw_json = encode_message(msg_dict)
        wire_bytes = raw_json + b"\n"
        with self._client_send_lock:
            self._client_sock.sendall(wire_bytes)
        return len(raw_json)

    def client_recv(self, timeout: float | None = None) -> dict[str, Any] | None:
        """
        Menerima satu pesan utuh dari server TCP.
        WAJIB memakai StreamFramer:
        - Jika di dalam buffer sudah ada pesan lengkap (karena recv() sebelumnya
          menerima lebih dari satu pesan sekaligus), langsung ambil dari buffer.
        - Jika belum, baca chunk baru dari socket dengan recv() sampai menemukan '\\n'
          atau sampai timeout/EOF.
        """
        if self._client_sock is None:
            return None

        deadline = (
            None
            if timeout is None
            else (threading.Event(), __import__("time").monotonic() + timeout)[1]
        )

        while True:
            # 1. Cek apakah buffer sudah menyimpan pesan lengkap
            buffered_frame = self._client_framer.pop_message()
            if buffered_frame is not None:
                if len(buffered_frame) == 0:
                    continue
                msg_dict, err_reason = decode_message(buffered_frame)
                if msg_dict is None:
                    self.logger.warning("Client TCP menerima pesan tidak valid: %s", err_reason)
                    continue
                return msg_dict

            # 2. Hitung sisa timeout jika diberlakukan
            if deadline is not None:
                remaining = deadline - __import__("time").monotonic()
                if remaining <= 0:
                    return None
                self._client_sock.settimeout(remaining)
            else:
                self._client_sock.settimeout(None)

            # 3. Baca byte berikutnya dari stream TCP
            try:
                chunk = self._client_sock.recv(4096)
            except socket.timeout:
                return None
            except OSError:
                return None

            if not chunk:
                # Koneksi ditutup oleh server (EOF)
                return None

            self._client_framer.append(chunk)

    def close(self) -> None:
        """Menutup seluruh resource socket TCP (baik client maupun server)."""
        self._running = False

        if self._client_sock is not None:
            try:
                self._client_sock.close()
            except OSError:
                pass
            self._client_sock = None

        with self._peers_lock:
            peers_copy = list(self._active_peers.values())
            self._active_peers.clear()

        for conn_sock in peers_copy:
            try:
                conn_sock.close()
            except OSError:
                pass

        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None

        if self._accept_thread is not None and self._accept_thread.is_alive():
            if threading.current_thread() is not self._accept_thread:
                self._accept_thread.join(timeout=1.0)


# =====================================================================
# IMPLEMENTASI UDP TRANSPORT (SOCK_DGRAM)
# =====================================================================

class UdpTransport:
    """
    Transport berbasis UDP (User Datagram Protocol - SOCK_DGRAM).
    Bersifat connectionless (tanpa 3-way handshake), berbasis datagram (1 datagram = 1 pesan),
    dan tanpa jaminan pengiriman (tanpa retransmisi, tanpa ACK buatan, tanpa pengurutan ulang).
    """

    proto_name: str = "udp"

    def __init__(
        self,
        on_error: ErrorCallback | None = None,
        on_disconnect: DisconnectCallback | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.on_error = on_error
        self.on_disconnect = on_disconnect
        self.logger = logger or logging.getLogger("DualChat.Transport.UDP")

        self._sock: socket.socket | None = None
        self._server_addr: tuple[str, int] | None = None
        self._running = False

        self.bound_host: str = ""
        self.bound_port: int = 0

    # -----------------------------------------------------------------
    # SISI SERVER (UDP)
    # -----------------------------------------------------------------

    def server_listen(self, host: str, port: int) -> None:
        """
        Membuka socket server UDP:
        CATATAN LAPORAN:
        1. Membuat socket AF_INET dengan tipe SOCK_DGRAM (UDP).
        2. Hanya memanggil `bind((host, port))`. TIDAK ADA panggilan `listen()`
           atau `accept()` karena UDP bersifat connectionless (tidak ada state koneksi).
        """
        # [LAPORAN: INISIALISASI SOCKET SERVER UDP]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.settimeout(0.5)

        actual_addr = sock.getsockname()
        self.bound_host = str(actual_addr[0])
        self.bound_port = int(actual_addr[1])

        self._sock = sock
        self._running = True
        self.logger.info("Server UDP mendengarkan di %s:%d", self.bound_host, self.bound_port)

    def server_recv(self, timeout: float | None = 0.5) -> tuple[dict[str, Any] | None, Peer | None]:
        """
        Menerima satu datagram UDP dengan `recvfrom(65535)`.
        Satu datagram UDP adalah satu pesan utuh (tanpa delimiter '\\n').
        Identitas pengirim `(ip, port)` dibungkus menjadi objek `Peer`.
        """
        if self._sock is None or not self._running:
            return None, None

        self._sock.settimeout(timeout)
        try:
            raw_bytes, addr = self._sock.recvfrom(UDP_BUFFER_SIZE)
        except socket.timeout:
            return None, None
        except ConnectionResetError:
            # Di Windows, recvfrom() UDP dapat melempar WSAECONNRESET jika pengiriman
            # sebelumnya mengenai port tujuan yang sudah tutup (ICMP Port Unreachable).
            # Abaikan agar server UDP tidak crash.
            return None, None
        except OSError:
            return None, None

        client_addr = (str(addr[0]), int(addr[1]))
        peer = Peer(raw_handle=client_addr, address=client_addr)
        peer.last_recv_size = len(raw_bytes)

        msg_dict, err_reason = decode_message(raw_bytes)
        if msg_dict is None:
            if self.on_error is not None:
                self.on_error(err_reason or "Datagram tidak valid", len(raw_bytes), peer)
            return None, None

        return msg_dict, peer

    def server_send(self, msg_dict: dict[str, Any], peer: Peer) -> int:
        """
        Mengirimkan satu datagram UDP ke alamat `(ip, port)` milik `peer`
        menggunakan `sendto()` tanpa delimiter '\\n'.
        """
        if self._sock is None or not self._running:
            return 0

        raw_json = encode_message(msg_dict)
        try:
            self._sock.sendto(raw_json, peer.address)
            return len(raw_json)
        except OSError as exc:
            self.logger.debug("Gagal mengirim datagram UDP ke %s: %s", peer, exc)
            return 0

    # -----------------------------------------------------------------
    # SISI CLIENT (UDP)
    # -----------------------------------------------------------------

    def client_connect(self, host: str, port: int) -> None:
        """
        Menyiapkan socket client UDP:
        CATATAN LAPORAN:
        Berbeda dengan TCP yang melakukan 3-way handshake pada `connect()`,
        pada UDP fungsi ini HANYA membuat socket lokal `SOCK_DGRAM` dan menyimpan
        tuple `(host, port)` tujuan di memori. Tidak ada paket jaringan apapun
        yang dikirim saat fungsi ini dipanggil!
        """
        # [LAPORAN: INISIALISASI SOCKET CLIENT UDP - TANPA HANDSHAKE]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock = sock
        self._server_addr = (host, port)
        self._running = True

    def client_send(self, msg_dict: dict[str, Any]) -> int:
        """
        Mengirimkan satu pesan sebagai satu datagram UDP tunggal ke server
        menggunakan `sendto()` (tanpa '\\n', tanpa retransmisi).
        """
        if self._sock is None or self._server_addr is None:
            raise RuntimeError("Client UDP belum diinisialisasi (client_connect belum dipanggil)")

        raw_json = encode_message(msg_dict)
        self._sock.sendto(raw_json, self._server_addr)
        return len(raw_json)

    def client_recv(self, timeout: float | None = None) -> dict[str, Any] | None:
        """
        Menerima satu datagram UDP dari server dengan buffer 65535 byte.
        Jika waktu tunggu (`timeout`) habis, mengembalikan `None`.
        Tidak ada pengurutan ulang atau permintaan kirim ulang (retransmisi).
        """
        if self._sock is None or not self._running:
            return None

        self._sock.settimeout(timeout)
        try:
            raw_bytes, _ = self._sock.recvfrom(UDP_BUFFER_SIZE)
        except socket.timeout:
            return None
        except ConnectionResetError:
            # Di Windows, WSAECONNRESET terjadi jika port server UDP tujuan tidak aktif
            return None
        except OSError:
            return None

        msg_dict, err_reason = decode_message(raw_bytes)
        if msg_dict is None:
            self.logger.warning("Client UDP menerima datagram tidak valid: %s", err_reason)
            return None

        return msg_dict

    def close(self) -> None:
        """Menutup socket UDP."""
        self._running = False
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


# =====================================================================
# FUNGSI PEMBUAT TRANSPORT
# =====================================================================

def create_transport(
    proto: str,
    on_error: ErrorCallback | None = None,
    on_disconnect: DisconnectCallback | None = None,
    logger: logging.Logger | None = None,
) -> TcpTransport | UdpTransport:
    """
    Factory function untuk membuat objek TcpTransport atau UdpTransport
    berdasarkan argumen CLI `--proto`.
    """
    normalized = proto.strip().lower()
    if normalized == "tcp":
        return TcpTransport(on_error=on_error, on_disconnect=on_disconnect, logger=logger)
    if normalized == "udp":
        return UdpTransport(on_error=on_error, on_disconnect=on_disconnect, logger=logger)
    raise ValueError(f"Protokol tidak didukung: {proto!r} (pilih 'tcp' atau 'udp')")
