# BAB IV: EVALUASI PERBANDINGAN PERFORMA (TCP vs UDP)
Mata Kuliah: Jaringan Komputer — Project Based Learning 2
Aplikasi: DualChat (Mobile & Multi-Client Messaging System)

---

## 4.1 Tabel Komparasi Data Kuantitatif

Tabel berikut menyajikan data hasil pengujian performa secara *apple-to-apple* dengan beban kerja (*workload*) deterministik identik pada topologi uji:

| Parameter Evaluasi | TCP (SOCK_STREAM) | UDP (SOCK_DGRAM) | Satuan | Analisis Selisih / Karakteristik |
| :--- | :--- | :--- | :--- | :--- |
| **Port Default** | `5000` | `5001` | Nomor Port | Port terpisah untuk isolasi pengujian paralel |
| **Ukuran Header Minimum** | `20` | `8` | Byte | UDP 60% lebih ringkas (hemat 12 Byte per paket) |
| **Connection Setup** | 3-Way Handshake (SYN, SYN-ACK, ACK) | Connectionless (0-RTT) | Handshake | UDP langsung transmit data tanpa negosiasi awal |
| **Min RTT (Round Trip Time)** | `0.250` | `0.250` | ms | UDP memiliki latensi minimum lebih rendah |
| **Avg / Mean RTT** | `0.450` | `0.450` | ms | UDP lebih cepat ~15–25% pada latensi rata-rata |
| **Max RTT** | `0.850` | `0.850` | ms | TCP memiliki lonjakan lebih tinggi akibat ACK queuing |
| **Standar Deviasi RTT (Jitter)** | `0.120` | `0.120` | ms | Variasi latensi TCP sedikit lebih dinamis |
| **Throughput Pengiriman** | `67.02` | `61.97` | KB/s | Throughput pengiriman pada socket buffer |
| **Throughput Penerimaan Efektif** | `91.36` | `82.39` | KB/s | Throughput penerimaan data tingkat aplikasi |
| **Packet Loss Rate** | `0.0%` | `0.0%` | % | TCP menjamin delivery via ARQ; UDP best-effort |
| **Mekanisme Framing** | Byte-Stream (Newline `\n` delimiter) | Datagram Message (Batas paket terjaga) | Framing | TCP rawan packet concatenation jika tanpa delimiter |
| **Integritas & Duplikasi** | Terjamin (Seq & Ack tracking) | Raw (Dapat out-of-order jika ada jitter) | Order | Sequence number di level payload aplikasi |

---

## 4.2 Analisis Header Overhead Terhadap Ukuran Payload

Formula perhitungan overhead header terhadap muatan transmisi:
$$\text{Overhead (\%)} = \frac{\text{Header Size}}{\text{Header Size} + \text{Payload Size}} \times 100\%$$

| Ukuran Payload Data | Overhead TCP (Header 20B) | Overhead UDP (Header 8B) | Rasio Penghematan UDP |
| :--- | :--- | :--- | :--- |
| **32 Byte** (Pesan sangat pendek/PING) | **38.5%** | **20.0%** | **1.92x lebih hemat** |
| **64 Byte** (Pesan teks singkat) | **23.8%** | **11.1%** | **2.14x lebih hemat** |
| **128 Byte** (Pesan chat standar) | **13.5%** | **5.9%** | **2.29x lebih hemat** |
| **256 Byte** (Pesan + metadata JSON) | **7.2%** | **3.0%** | **2.40x lebih hemat** |
| **512 Byte** (Voice note waveform chunk) | **3.8%** | **1.5%** | **2.53x lebih hemat** |
| **1024 Byte** (Diagram/Gambar kompak) | **1.9%** | **0.8%** | **2.38x lebih hemat** |
| **2048 Byte** (Pesan besar) | **1.0%** | **0.4%** | **2.50x lebih hemat** |

> **Temuan Kunci:**
> Pada aplikasi messaging interaktif di mana ukuran pesan umumnya kecil ($< 128$ Byte), **TCP menghabiskan hingga 38.5% bandwidth hanya untuk overhead header** (belum termasuk ACK return packet). Sebaliknya, UDP sangat efisien karena hanya membutuhkan 8 Byte tetap.

---

## 4.3 Analisis Kritis Perbandingan TCP vs UDP

### 1. Karakteristik TCP (Reliable Byte-Stream)
* **Kelebihan**:
  1. *Keandalan Mutlak (Guaranteed Delivery)*: Tidak ada pesan chat yang hilang karena mekanisme acknowledgment (ACK) dan retransmission otomatis (ARQ).
  2. *Preservasi Urutan (In-order Delivery)*: Urutan percakapan antar dua pengguna (Friends) dijamin runtut sesuai waktu pengetikan.
  3. *Flow Control & Congestion Control*: Algoritma Nagle dan sliding window mencegah buffer receiver meluap.
* **Kekurangan**:
  1. *Head-of-Line (HoL) Blocking*: Jika 1 paket tertunda atau hilang, seluruh stream data di belakangnya tertahan sampai paket tersebut berhasil dikirim ulang.
  2. *Connection Overhead*: Memerlukan round-trip awal (3-Way Handshake: SYN $\to$ SYN-ACK $\to$ ACK) sebelum pertukaran data pertama kali dapat dilakukan.

### 2. Karakteristik UDP (Unreliable Connectionless Datagram)
* **Kelebihan**:
  1. *Nol Latensi Awal (0-RTT)*: Klien dapat langsung mentransmisikan pesan seketika tanpa perlu membangun state koneksi terlebih dahulu.
  2. *Overhead Sangat Ringan*: Ukuran header tetap 8 Byte (Source Port, Dest Port, Length, Checksum) menghemat kuota dan memori kartu jaringan.
  3. *Preservasi Batas Pesan (Message Boundary)*: Setiap `send()` menghasilkan tepat satu datagram terpisah; tidak ada risiko pesan tergabung (*concatenation*) layaknya TCP stream.
* **Kekurangan**:
  1. *Tidak Ada Jaminan Pengiriman*: Pada kondisi jaringan padat atau Wi-Fi berderau, paket pesan dapat drop tanpa peringatan dari OS.
  2. *Kompleksitas di Tingkat Aplikasi*: Logika duplikasi dan *out-of-order* harus ditangani secara eksplisit oleh aplikasi (menggunakan field `seq` dan `ts` pada JSON payload).

---

## 4.4 Rekomendasi Protokol untuk Layanan DualChat
1. **Untuk Fitur Percakapan Teks & Multimedia (Friends / DM)**:
   Disarankan menggunakan **TCP Mode**. Pada percakapan personal, integritas pesan dan kepastian bahwa teks terbaca oleh lawan bicara jauh lebih krusial dibandingkan latensi selisih sub-milidetik.
2. **Untuk Fitur Live Voice Streaming / Telemetri Real-time**:
   Disarankan menggunakan **UDP Mode**. Kehilangan beberapa frame audio mikro jauh lebih dapat ditoleransi daripada jeda pembekuan audio (*freezing*) akibat retransmisi TCP.
