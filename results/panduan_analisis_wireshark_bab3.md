# BAB III: SKENARIO PENGUJIAN & ANALISIS TRAFIK WIRESHARK
Mata Kuliah: Jaringan Komputer — Project Based Learning 2
Aplikasi: DualChat (Transport Layer Comparison)

---

## 3.1 Display Filter Wireshark Resmi

Gunakan filter berikut pada kolom pencarian Wireshark untuk mengisolasi percakapan DualChat:

| Filter Wireshark | Fungsi & Tujuan Analisis |
| :--- | :--- |
| `tcp.port == 5000` | Memantau seluruh percakapan TCP (Handshake, Data ACK, Fin) |
| `udp.port == 5001` | Memantau seluruh aliran datagram UDP (0-RTT, Burst Message) |
| `tcp.port == 5000 || udp.port == 5001` | Memantau perbandingan berdampingan kedua protokol |
| `tcp.flags.syn == 1` | Memfilter proses negosiasi 3-Way Handshake TCP awal |
| `tcp.analysis.retransmission` | Mendeteksi jika terjadi retransmisi paket TCP |

---

## 3.2 Analisis Mekanisme Pembentukan & Pemutusan Koneksi

### A. TCP 3-Way Handshake (Port 5000)
1. **Paket 1: [SYN] (Client $\to$ Server)**
   * Client mengirim flag `SYN` dengan nomor urut acak (*Initial Sequence Number* / ISN).
   * Status koneksi: Client memasuki state `SYN_SENT`.
2. **Paket 2: [SYN, ACK] (Server $\to$ Client)**
   * Server membalas dengan flag `SYN` miliknya dan `ACK` bernilai `ISN_client + 1`.
   * Status koneksi: Server memasuki state `SYN_RCVD`.
3. **Paket 3: [ACK] (Client $\to$ Server)**
   * Client mengonfirmasi penerimaan SYN server dengan mengirim `ACK = ISN_server + 1`.
   * Status koneksi: Kedua belah pihak resmi berada pada state `ESTABLISHED`.

### B. TCP Connection Termination (4-Way Handshake / FIN-ACK)
* Pihak yang ingin menutup koneksi (misal client menekan disconnect) mengirim paket berflag `[FIN, ACK]`.
* Lawan bicara membalas dengan `[ACK]`, kemudian mengirim `[FIN, ACK]` saat resource socket siap dilepas, ditutup dengan `[ACK]` final.

### C. UDP Connectionless Behavior (Port 5001)
* **Tidak Ada Handshake**: Tidak ditemukan paket bertukar flag `SYN` atau `ACK` sama sekali.
* Begitu aplikasi dijalankan, paket data pertama langsung bertipe payload JSON `JOIN` atau `MSG`.
* Port UDP server langsung menerima datagram tanpa menyimpan state stateful (`0-RTT latency`).

---

## 3.3 Checklist Tangkapan Layar (Screenshots) untuk Laporan

Pastikan menyertakan tangkapan layar Wireshark dengan format berikut:
- [ ] **Screenshot 1**: Aliran 3-Way Handshake TCP (Paket SYN, SYN-ACK, ACK berurutan).
- [ ] **Screenshot 2**: Inspeksi Header TCP di panel detail paket (menunjukkan Header Length = 20 Bytes, Flags, Sequence Number, Window Size).
- [ ] **Screenshot 3**: Inspeksi Header UDP di panel detail paket (menunjukkan Header Length = 8 Bytes tetap: Source Port, Dest Port, Length, Checksum).
- [ ] **Screenshot 4**: Payload data JSON di Application Layer (`{"type":"MSG","seq":X,"user":"...","data":"..."}`).
- [ ] **Screenshot 5**: Pengujian Burst Test (Tekan tombol petir ⚡ pada AppBar DualChat untuk memicu 10 paket beruntun, lalu tunjukkan grafik Wireshark *IO Graph*).
