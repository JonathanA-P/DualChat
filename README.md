# DualChat — Perbandingan TCP vs UDP pada Application Layer

Aplikasi chat berbasis teks (**DualChat**) untuk tugas mata kuliah **Jaringan Komputer**. Proyek ini dirancang agar **satu logika aplikasi yang 100% identik** dapat dijalankan di atas dua protokol *Transport Layer* yang berbeda: **TCP (`SOCK_STREAM`)** dan **UDP (`SOCK_DGRAM`)**.

Dengan beban kerja (*workload*) dan format pesan aplikasi yang sama persis, perilaku kedua protokol dapat dibandingkan secara *apple-to-apple* menggunakan **Wireshark** dan **log CSV** (3-way handshake, header overhead, RTT, throughput, serta penanganan *packet loss* dan *out-of-order*).

---

## Struktur Proyek

```text
dualchat/
├── chat_core.py     # Format pesan JSON, encode/decode, StreamFramer, CsvLogger, logika inti server
├── transport.py     # TcpTransport & UdpTransport (SATU-SATUNYA lokasi kode socket)
├── server.py        # Server chat (--proto tcp|udp|both)
├── client.py        # Client interaktif CLI (--proto tcp|udp)
├── benchmark.py     # Client otomatis dengan workload deterministik (--mode ping|msg)
├── web_ui.py        # Full Web UI/UX Telemetry & Chat Studio (terintegrasi penuh dengan socket TCP/UDP)
├── tests/           # Unit test & integration test (unittest)
│   ├── __init__.py
│   └── test_dualchat.py
├── results/         # Direktori output file log CSV (dibuat otomatis saat berjalan)
└── README.md
```

---

## Spesifikasi Protokol Aplikasi & Framing

### 1. Format Pesan (JSON UTF-8)
Setiap pesan yang dikirim melalui jaringan adalah objek JSON terenkode UTF-8 dengan 5 *field* wajib:

| Field  | Tipe    | Deskripsi                                                                 |
| :----- | :------ | :------------------------------------------------------------------------ |
| `type` | `str`   | `"JOIN"` \| `"ACK_JOIN"` \| `"MSG"` \| `"PING"` \| `"PONG"` \| `"LEAVE"` \| `"ERR"` |
| `seq`  | `int`   | Nomor urut pesan per pengirim, dimulai dari `0`, naik `1` setiap pesan    |
| `ts`   | `float` | Timestamp `time.time()` saat pesan dibuat oleh pengirim                   |
| `user` | `str`   | Username pengirim                                                         |
| `data` | `str`   | Isi pesan teks (kosong `""` untuk `PING`, `PONG`, dan `LEAVE`, maks 8192 byte) |

**Contoh Payload JSON:**
```json
{"type":"MSG","seq":17,"ts":1764561234.123456,"user":"alice","data":"halo semua"}
```

### 2. Perbedaan Framing: TCP vs UDP (Bagian Inti untuk Laporan)
Perbedaan penanganan batas pesan (*message framing*) diimplementasikan secara eksplisit di [`transport.py`](file:///c:/Users/LOQ/.gemini/antigravity/scratch/Jarkom/transport.py) dan [`chat_core.py`](file:///c:/Users/LOQ/.gemini/antigravity/scratch/Jarkom/chat_core.py):

1. **TCP (`SOCK_STREAM`) — Byte Stream dengan Delimiter `\n`**:
   - TCP adalah protokol berbasis aliran byte (*byte-stream*) tanpa batas pesan bawaan. Satu panggilan `send()` dapat terpotong menjadi beberapa `recv()`, atau beberapa `send()` dapat digabung oleh kernel (algoritma Nagle) ke dalam satu `recv()`.
   - Setiap pesan JSON diakhiri dengan byte newline `\n` (`0x0A`). Karakter newline di dalam isi pesan (`data`) sudah otomatis di-*escape* menjadi `\\n` oleh `json.dumps()`.
   - Penerima **wajib** menggunakan [`StreamFramer`](file:///c:/Users/LOQ/.gemini/antigravity/scratch/Jarkom/chat_core.py) yang mengakumulasi hasil `recv()`, memisahkan pesan berdasarkan `\n`, dan menyimpan sisa byte yang belum lengkap di dalam buffer untuk `recv()` berikutnya.

2. **UDP (`SOCK_DGRAM`) — Satu Datagram = Satu Pesan**:
   - UDP mempertahankan batas pesan (*message boundary*) di level datagram.
   - Satu panggilan `sendto()` mengirim tepat satu pesan JSON (tanpa delimiter `\n`), dan satu panggilan `recvfrom(65535)` membaca tepat satu datagram utuh.
   - **Tanpa Retransmisi Aplikasi**: `UdpTransport` sengaja tidak memiliki mekanisme *retry*, *acknowledgment* buatan, ataupun pengurutan ulang (*reordering*) agar sifat asli UDP terlihat jelas saat diuji dan ditangkap di Wireshark.

---

## Panduan Bagian Kode untuk Ditunjuk pada Laporan

| Konsep Jaringan Komputer | Lokasi File & Fungsi/Kelas |
| :--- | :--- |
| **Inisialisasi Socket Server TCP (`SO_REUSEADDR`, `bind`, `listen`, `accept`)** | [`TcpTransport.server_listen`](file:///c:/Users/LOQ/.gemini/antigravity/scratch/Jarkom/transport.py) & [`TcpTransport._accept_loop`](file:///c:/Users/LOQ/.gemini/antigravity/scratch/Jarkom/transport.py) |
| **3-Way Handshake Client TCP (`connect`)** | [`TcpTransport.client_connect`](file:///c:/Users/LOQ/.gemini/antigravity/scratch/Jarkom/transport.py) |
| **Framing Byte-Stream TCP (`\n` buffer)** | [`StreamFramer`](file:///c:/Users/LOQ/.gemini/antigravity/scratch/Jarkom/chat_core.py) & [`TcpTransport._client_reader_loop`](file:///c:/Users/LOQ/.gemini/antigravity/scratch/Jarkom/transport.py) |
| **Inisialisasi Socket Server UDP (tanpa `listen`/`accept`)** | [`UdpTransport.server_listen`](file:///c:/Users/LOQ/.gemini/antigravity/scratch/Jarkom/transport.py) |
| **Inisialisasi Client UDP (tanpa handshake jaringan)** | [`UdpTransport.client_connect`](file:///c:/Users/LOQ/.gemini/antigravity/scratch/Jarkom/transport.py) |
| **Logika Ruangan Chat & Broadcast Identik (bebas `if proto`)** | [`ChatServerEngine`](file:///c:/Users/LOQ/.gemini/antigravity/scratch/Jarkom/chat_core.py) & [`ProtocolServerWorker`](file:///c:/Users/LOQ/.gemini/antigravity/scratch/Jarkom/server.py) |

---

## Cara Menjalankan

### 0. Menjalankan Full Web UI/UX Studio (All-in-One Dashboard)
Untuk membuka antarmuka grafis lengkap di browser (menggabungkan Server TCP & UDP, Multi-Client Chat Arena, Benchmark Lab + Grafik RTT/Throughput, Wire Framing Visualizer, dan CSV Explorer):
```bash
python web_ui.py --ui-port 8080 --tcp-port 5000 --udp-port 5001
```
Lalu buka **`http://127.0.0.1:8080`** di browser Anda. Seluruh aksi di UI menggunakan socket `TcpTransport` (`SOCK_STREAM`) dan `UdpTransport` (`SOCK_DGRAM`) asli di belakang layar sehingga paketnya tetap murni dan siap ditangkap di **Wireshark**.

### 0.1 Menjalankan Native GUI Client Flutter (Windows Desktop & Mobile)
Selain Web UI, proyek ini kini dilengkapi client native Flutter yang langsung berkomunikasi lewat socket OS murni (`dart:io`):
```bash
flutter run -d windows
```
Client Flutter ini menyediakan toggle live TCP/UDP, Telemetry HUD (RTT ms, Packets TX/RX, Deteksi Out-of-Order / Packet Loss), chat room, dan tombol `/ping` cepat.

> **Catatan Antar-Perangkat (LAN)**: Secara bawaan, seluruh komponen berjalan di `127.0.0.1` (*localhost*). Saat nanti diuji antar-laptop di jaringan LAN, cukup tambahkan argumen `--host <IP_SERVER>` pada perintah server, client, maupun benchmark **tanpa mengubah satu baris kode pun**.

### 1. Menjalankan Server (TCP & UDP Bersamaan)
```bash
python server.py --proto both --host 127.0.0.1 --tcp-port 5000 --udp-port 5001
```
Server akan membuat log otomatis di `results/server_tcp.csv` dan `results/server_udp.csv`. Tekan `Ctrl+C` untuk menghentikan server dengan rapi.

### 2. Menjalankan 2 Client Interaktif (Mode TCP)
Buka dua terminal baru:
```bash
# Terminal Client 1 (Alice - TCP)
python client.py --proto tcp --host 127.0.0.1 --port 5000 --user alice

# Terminal Client 2 (Bob - TCP)
python client.py --proto tcp --host 127.0.0.1 --port 5000 --user bob
```

### 3. Menjalankan 2 Client Interaktif (Mode UDP)
Buka dua terminal baru:
```bash
# Terminal Client 1 (Alice - UDP)
python client.py --proto udp --host 127.0.0.1 --port 5001 --user alice

# Terminal Client 2 (Bob - UDP)
python client.py --proto udp --host 127.0.0.1 --port 5001 --user bob
```
**Perintah di dalam Client Interaktif:**
- Ketik teks biasa lalu tekan `Enter` untuk mengirim pesan chat (`MSG`) ke semua pengguna aktif.
- Ketik `/ping` untuk mengirim `PING` ke server dan menampilkan nilai **RTT (ms)** saat `PONG` diterima.
- Ketik `/quit` untuk mengirim `LEAVE` dan keluar dari aplikasi.

---

## Menjalankan Benchmark Otomatis

Pastikan `server.py --proto both` sudah berjalan terlebih dahulu.

### A. Benchmark RTT (`--mode ping`)
Mengirim 200 pesan `PING` dan mencatat RTT untuk setiap `PONG`:
```bash
# Benchmark Ping TCP
python benchmark.py --proto tcp --mode ping --count 200 --interval 0.01 --scenario S1 --run 1

# Benchmark Ping UDP
python benchmark.py --proto udp --mode ping --count 200 --interval 0.01 --scenario S1 --run 1
```
Output CSV tersimpan di:
- `results/bench_tcp_S1_run1_ping.csv`
- `results/bench_udp_S1_run1_ping.csv`

### B. Benchmark Throughput & Loss (`--mode msg`)
Jalankan **receiver** terlebih dahulu di satu terminal, lalu jalankan **sender** di terminal lain:

**Untuk TCP:**
```bash
# Terminal 1: Receiver TCP (menunggu pesan masuk hingga 3 detik idle)
python benchmark.py --proto tcp --mode msg --role receiver --count 200 --size 64 --scenario S2 --run 1

# Terminal 2: Sender TCP (mengirim 200 pesan berukuran payload 64 byte)
python benchmark.py --proto tcp --mode msg --role sender --count 200 --size 64 --interval 0.005 --scenario S2 --run 1
```

**Untuk UDP:**
```bash
# Terminal 1: Receiver UDP
python benchmark.py --proto udp --mode msg --role receiver --count 200 --size 64 --scenario S2 --run 1

# Terminal 2: Sender UDP
python benchmark.py --proto udp --mode msg --role sender --count 200 --size 64 --interval 0.005 --scenario S2 --run 1
```

---

## Menjalankan Unit & Integration Tests

Seluruh pengujian otomatis (encode/decode, framing TCP, integrasi server-client TCP/UDP, dan smoke test benchmark CSV) dijalankan dengan modul `unittest` bawaan Python:

```bash
python -m unittest discover tests -v
```

---

## Catatan Asumsi Desain & Hal yang Ditunda (Tahap Berikutnya)

### Keputusan & Asumsi Desain
1. **Pembersihan Pesan Sistem pada Benchmark Receiver**: Ketika sender bergabung (`JOIN`) atau keluar (`LEAVE`), server menyiarkan pesan sistem (`user="server"`). Agar perhitungan jumlah pesan dan baris CSV pada benchmark `receiver` akurat terhadap `--count`, benchmark `receiver` mengabaikan pesan dari `user="server"` dan hanya mencatat pesan workload dari client `sender`.
2. **Satu Baris per Siklus pada Benchmark Ping**: Pada file CSV benchmark, setiap siklus `PING` menghasilkan 1 baris (`event="RECV"` dengan `t_send`, `t_recv`, dan `rtt_ms` terisi jika `PONG` diterima, atau `event="TIMEOUT"` jika batas waktu 2 detik terlampaui pada UDP). Dengan demikian jumlah baris CSV selalu konsisten dengan `--count`.
3. **Penanganan `ConnectionResetError` UDP di Windows**: Di Windows, socket UDP (`SOCK_DGRAM`) dapat melempar `WSAECONNRESET` (`WinError 10054`) pada `recvfrom()` apabila paket sebelumnya dikirim ke port lokal yang sudah tutup. `UdpTransport` menangkap kondisi ini agar server maupun client UDP tidak pernah *crash*.

### Pekerjaan yang Ditunda (Di Luar Ruang Lingkup Tahap Ini)
Sesuai spesifikasi tahap ini, hal-hal berikut **belum dikerjakan** dan disiapkan melalui *hook* log CSV di `results/`:
- Konfigurasi topologi jaringan LAN fisik/virtual antar-laptop.
- Pengambilan paket (*packet capture*) `.pcapng` menggunakan Wireshark.
- Simulasi *packet loss*, *delay*, dan *jitter* (misalnya dengan `tc netem` atau Clumsy).
- Script pengolahan data statistik lanjutan dan pembuatan grafik visualisasi (matplotlib/pandas).
- Antarmuka pengguna grafis (GUI).
"# Jarkom_project" 
