"""
tests/test_dualchat.py - Rangkaian pengujian unit dan integrasi untuk DualChat.

Mencakup 4 area pengujian wajib:
1. Encode/Decode JSON UTF-8 (bolak-balik, pesan rusak ditolak tanpa exception,
   karakter Unicode dan newline di dalam data aman).
2. Framing TCP (StreamFramer: beberapa pesan dalam satu chunk, satu pesan terpecah
   di dua chunk, dan chunk kosong).
3. Integrasi localhost untuk TCP dan UDP (port acak): JOIN berhasil, username duplikat
   ditolak (ERR), dua client saling menerima MSG, PING menghasilkan PONG dengan seq
   dan ts yang sama persis, LEAVE membersihkan client, serta timeout client pasif.
4. Benchmark smoke test (--count 20) di localhost untuk TCP dan UDP menghasilkan
   file CSV dengan skema kolom identik dan jumlah baris yang tepat.
"""

from __future__ import annotations

import csv
import tempfile
import threading
import time
import unittest
from pathlib import Path

from benchmark import run_benchmark
from chat_core import (
    BENCH_CSV_COLUMNS,
    MAX_DATA_BYTES,
    StreamFramer,
    decode_message,
    encode_message,
    make_message,
)
from client import InteractiveChatClient
from server import ProtocolServerWorker
from transport import create_transport
from web_ui import create_http_server


class TestProtocolEncodeDecode(unittest.TestCase):
    """1. Pengujian encode dan decode pesan protokol aplikasi."""

    def test_roundtrip_valid_message(self) -> None:
        """Pesan valid yang di-encode lalu di-decode harus menghasilkan dict yang identik."""
        original = make_message(
            msg_type="MSG",
            seq=17,
            user="alice",
            data="halo semua",
            ts=1764561234.123456,
        )
        raw = encode_message(original)
        decoded, err = decode_message(raw)

        self.assertIsNone(err)
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded["type"], "MSG")
        self.assertEqual(decoded["seq"], 17)
        self.assertAlmostEqual(decoded["ts"], 1764561234.123456, places=6)
        self.assertEqual(decoded["user"], "alice")
        self.assertEqual(decoded["data"], "halo semua")

    def test_unicode_and_newline_in_data_safe(self) -> None:
        """Karakter Unicode (emoji, huruf non-Latin) dan newline ('\\n') di dalam data harus aman."""
        complex_data = "Baris 1\nBaris 2\r\nHalo dunia 🚀✨日本語 dengan \"kutip\" dan \\backslash\\"
        msg = make_message(msg_type="MSG", seq=0, user="budi", data=complex_data)
        raw = encode_message(msg)

        # Pastikan hasil encode JSON tidak mengandung byte newline mentah di luar escape
        self.assertNotIn(b"\n", raw)

        decoded, err = decode_message(raw)
        self.assertIsNone(err)
        self.assertIsNotNone(decoded)
        assert decoded is not None
        self.assertEqual(decoded["data"], complex_data)

    def test_corrupted_and_invalid_messages_rejected_without_exception(self) -> None:
        """Pesan rusak, field hilang, tipe salah, atau melebihi batas ukuran tidak boleh crash."""
        invalid_payloads: list[bytes] = [
            b"",  # kosong
            b"{bukan json valid",  # JSON rusak
            b"12345",  # bukan JSON object
            b"\xff\xfe\xfd",  # UTF-8 tidak valid
            b'{"type":"MSG","seq":1,"ts":1.0,"user":"alice"}',  # field 'data' hilang
            b'{"type":"UNKNOWN","seq":1,"ts":1.0,"user":"alice","data":"hi"}',  # type tak dikenal
            b'{"type":"MSG","seq":-5,"ts":1.0,"user":"alice","data":"hi"}',  # seq negatif
            b'{"type":"MSG","seq":true,"ts":1.0,"user":"alice","data":"hi"}',  # seq boolean
            b'{"type":"MSG","seq":1,"ts":"bukan_angka","user":"alice","data":"hi"}',  # ts salah
            b'{"type":"MSG","seq":1,"ts":1.0,"user":"","data":"hi"}',  # user kosong
        ]

        for payload in invalid_payloads:
            decoded, err = decode_message(payload)
            self.assertIsNone(decoded, f"Seharusnya ditolak untuk payload: {payload!r}")
            self.assertIsNotNone(err)

        # Uji batas maksimum data (8192 byte)
        oversized_data = "a" * (MAX_DATA_BYTES + 1)
        oversized_json = (
            f'{{"type":"MSG","seq":1,"ts":1.0,"user":"alice","data":"{oversized_data}"}}'
        ).encode("utf-8")
        decoded, err = decode_message(oversized_json)
        self.assertIsNone(decoded)
        self.assertIsNotNone(err)


class TestTcpStreamFramer(unittest.TestCase):
    """2. Pengujian StreamFramer untuk framing byte-stream TCP berbasis '\\n'."""

    def test_multiple_messages_in_single_chunk(self) -> None:
        """Beberapa pesan yang bergabung dalam satu chunk recv() harus terpisah dengan tepat."""
        framer = StreamFramer()
        m1 = encode_message(make_message("MSG", 0, "alice", "pesan satu"))
        m2 = encode_message(make_message("MSG", 1, "alice", "pesan dua"))
        m3 = encode_message(make_message("PING", 2, "alice", ""))

        combined_chunk = m1 + b"\n" + m2 + b"\n" + m3 + b"\n"
        frames = framer.feed(combined_chunk)

        self.assertEqual(len(frames), 3)
        self.assertEqual(frames[0], m1)
        self.assertEqual(frames[1], m2)
        self.assertEqual(frames[2], m3)

    def test_single_message_split_across_multiple_chunks(self) -> None:
        """Satu pesan yang terpecah di beberapa chunk recv() harus dirakit utuh saat '\\n' tiba."""
        framer = StreamFramer()
        full_raw = encode_message(
            make_message("MSG", 42, "bob", "pesan panjang yang terpotong di tengah jalan\nbaris dua")
        )
        wire = full_raw + b"\n"

        # Pecah menjadi 3 bagian
        part1 = wire[:15]
        part2 = wire[15:40]
        part3 = wire[40:]

        self.assertEqual(framer.feed(part1), [])
        self.assertEqual(framer.feed(part2), [])
        frames = framer.feed(part3)

        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0], full_raw)
        decoded, err = decode_message(frames[0])
        self.assertIsNone(err)
        assert decoded is not None
        self.assertEqual(decoded["seq"], 42)

    def test_empty_chunk_and_partial_remainder(self) -> None:
        """Chunk kosong tidak boleh mengubah state buffer dan sisa potongan tetap tersimpan."""
        framer = StreamFramer()
        self.assertEqual(framer.feed(b""), [])

        m1 = encode_message(make_message("MSG", 1, "alice", "halo"))
        m2 = encode_message(make_message("MSG", 2, "alice", "dunia"))

        # Kirim m1 lengkap + setengah dari m2
        half_m2 = m2[:10]
        rest_m2 = m2[10:] + b"\n"

        out1 = framer.feed(m1 + b"\n" + half_m2)
        self.assertEqual(out1, [m1])

        # Kirim chunk kosong di tengah-tengah
        self.assertEqual(framer.feed(b""), [])

        # Kirim sisa m2
        out2 = framer.feed(rest_m2)
        self.assertEqual(out2, [m2])


class TestServerClientIntegration(unittest.TestCase):
    """3. Pengujian integrasi Server & Client di localhost untuk TCP dan UDP."""

    def _run_integration_flow_for_proto(self, proto: str) -> None:
        """
        Menjalankan skenario integrasi lengkap untuk protokol tertentu ('tcp' atau 'udp'):
        - JOIN berhasil -> ACK_JOIN
        - Username duplikat ditolak -> ERR
        - Dua client saling berkirim MSG -> diterima dengan benar
        - PING -> dibalas PONG dengan seq dan ts yang SAMA persis
        - LEAVE -> menghapus client dan mengirim notifikasi ke client lain
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            server = ProtocolServerWorker(
                proto=proto,
                host="127.0.0.1",
                port=0,  # Gunakan port acak yang tersedia
                log_dir=tmpdir,
                inactivity_timeout=60.0,
            )
            server.start()
            port = server.bound_port

            c_alice = create_transport(proto)
            c_bob = create_transport(proto)
            c_dup = create_transport(proto)

            try:
                # 1. Alice JOIN
                c_alice.client_connect("127.0.0.1", port)
                c_alice.client_send(make_message("JOIN", 0, "alice", ""))
                ack_alice = c_alice.client_recv(timeout=2.0)
                self.assertIsNotNone(ack_alice)
                assert ack_alice is not None
                self.assertEqual(ack_alice["type"], "ACK_JOIN")

                # 2. Client duplikat mencoba memakai username 'alice' -> harus ditolak dengan ERR
                c_dup.client_connect("127.0.0.1", port)
                c_dup.client_send(make_message("JOIN", 0, "alice", ""))
                err_dup = c_dup.client_recv(timeout=2.0)
                self.assertIsNotNone(err_dup)
                assert err_dup is not None
                self.assertEqual(err_dup["type"], "ERR")
                self.assertIn("username sudah dipakai", err_dup["data"])

                # 3. Bob JOIN -> Bob mendapat ACK_JOIN, Alice mendapat notifikasi sistem
                c_bob.client_connect("127.0.0.1", port)
                c_bob.client_send(make_message("JOIN", 0, "bob", ""))
                ack_bob = c_bob.client_recv(timeout=2.0)
                self.assertIsNotNone(ack_bob)
                assert ack_bob is not None
                self.assertEqual(ack_bob["type"], "ACK_JOIN")

                notif_join = c_alice.client_recv(timeout=2.0)
                self.assertIsNotNone(notif_join)
                assert notif_join is not None
                self.assertEqual(notif_join["type"], "MSG")
                self.assertEqual(notif_join["user"], "server")
                self.assertIn("bob bergabung", notif_join["data"])

                # 4. Alice dan Bob saling berkirim MSG
                c_alice.client_send(make_message("MSG", 1, "alice", "halo bob dari alice"))
                msg_at_bob = c_bob.client_recv(timeout=2.0)
                self.assertIsNotNone(msg_at_bob)
                assert msg_at_bob is not None
                self.assertEqual(msg_at_bob["type"], "MSG")
                self.assertEqual(msg_at_bob["user"], "alice")
                self.assertEqual(msg_at_bob["seq"], 1)
                self.assertEqual(msg_at_bob["data"], "halo bob dari alice")

                c_bob.client_send(make_message("MSG", 1, "bob", "halo juga alice!"))
                msg_at_alice = c_alice.client_recv(timeout=2.0)
                self.assertIsNotNone(msg_at_alice)
                assert msg_at_alice is not None
                self.assertEqual(msg_at_alice["type"], "MSG")
                self.assertEqual(msg_at_alice["user"], "bob")
                self.assertEqual(msg_at_alice["seq"], 1)
                self.assertEqual(msg_at_alice["data"], "halo juga alice!")

                # 5. PING menghasilkan PONG dengan seq dan ts yang SAMA persis
                fixed_ts = 1700000123.456789
                c_alice.client_send(make_message("PING", 99, "alice", "", ts=fixed_ts))
                pong = c_alice.client_recv(timeout=2.0)
                self.assertIsNotNone(pong)
                assert pong is not None
                self.assertEqual(pong["type"], "PONG")
                self.assertEqual(pong["seq"], 99)
                self.assertEqual(pong["ts"], fixed_ts)

                # 6. Bob kirim LEAVE -> Bob dihapus dari daftar aktif, Alice menerima notifikasi keluar
                c_bob.client_send(make_message("LEAVE", 2, "bob", ""))
                notif_leave = c_alice.client_recv(timeout=2.0)
                self.assertIsNotNone(notif_leave)
                assert notif_leave is not None
                self.assertEqual(notif_leave["type"], "MSG")
                self.assertIn("bob keluar", notif_leave["data"])
                self.assertNotIn("bob", server.engine.active_users())

            finally:
                c_alice.close()
                c_bob.close()
                c_dup.close()
                server.stop()

    def test_tcp_integration(self) -> None:
        """Uji integrasi lengkap pada mode TCP."""
        self._run_integration_flow_for_proto("tcp")

    def test_udp_integration(self) -> None:
        """Uji integrasi lengkap pada mode UDP."""
        self._run_integration_flow_for_proto("udp")

    def test_msg_before_join_rejected_with_err(self) -> None:
        """Pesan MSG dari user yang belum JOIN harus ditolak dengan ERR."""
        for proto in ("tcp", "udp"):
            with tempfile.TemporaryDirectory() as tmpdir:
                server = ProtocolServerWorker(proto=proto, host="127.0.0.1", port=0, log_dir=tmpdir)
                server.start()
                client = create_transport(proto)
                try:
                    client.client_connect("127.0.0.1", server.bound_port)
                    client.client_send(make_message("MSG", 0, "ghost", "pesan tanpa join"))
                    reply = client.client_recv(timeout=2.0)
                    self.assertIsNotNone(reply)
                    assert reply is not None
                    self.assertEqual(reply["type"], "ERR")
                finally:
                    client.close()
                    server.stop()

    def test_udp_inactivity_reaper_removes_idle_client(self) -> None:
        """Client UDP yang tidak aktif melebihi batas waktu harus dihapus otomatis."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Gunakan inactivity_timeout kecil (0.2 detik) untuk pengujian cepat
            server = ProtocolServerWorker(
                proto="udp",
                host="127.0.0.1",
                port=0,
                log_dir=tmpdir,
                inactivity_timeout=0.2,
            )
            server.start()
            client = create_transport("udp")
            try:
                client.client_connect("127.0.0.1", server.bound_port)
                client.client_send(make_message("JOIN", 0, "idle_user", ""))
                ack = client.client_recv(timeout=2.0)
                self.assertIsNotNone(ack)
                self.assertIn("idle_user", server.engine.active_users())

                # Tunggu melebihi inactivity_timeout (0.2s)
                time.sleep(0.45)
                self.assertNotIn("idle_user", server.engine.active_users())
            finally:
                client.close()
                server.stop()

    def test_client_sequence_gap_detection(self) -> None:
        """Deteksi lompatan seq di sisi client harus mencatat peringatan."""
        import io
        from contextlib import redirect_stdout

        client = InteractiveChatClient(proto="udp", host="127.0.0.1", port=5001, user="bob")
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                client._check_sequence("alice", 0)
                client._check_sequence("alice", 1)
                # Lompat dari 1 ke 4 (seq 2 dan 3 hilang)
                client._check_sequence("alice", 4)
            output = buf.getvalue()
            self.assertIn("pesan hilang/out-of-order", output)
        finally:
            client.close()


class TestBenchmarkSmoke(unittest.TestCase):
    """4. Benchmark smoke test (--count 20) untuk TCP dan UDP menghasilkan CSV yang benar."""

    def _verify_csv_file(self, csv_path: str | Path, expected_rows: int) -> None:
        path = Path(csv_path)
        self.assertTrue(path.exists(), f"File CSV tidak ditemukan: {path}")
        with open(path, mode="r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            self.assertEqual(reader.fieldnames, BENCH_CSV_COLUMNS)
            rows = list(reader)
        self.assertEqual(len(rows), expected_rows)

    def _run_smoke_for_proto(self, proto: str) -> None:
        count = 20
        with tempfile.TemporaryDirectory() as tmpdir:
            server = ProtocolServerWorker(
                proto=proto,
                host="127.0.0.1",
                port=0,
                log_dir=tmpdir,
            )
            server.start()
            port = server.bound_port

            try:
                # A. Uji Benchmark Mode PING (--count 20)
                ping_summary = run_benchmark(
                    proto=proto,
                    host="127.0.0.1",
                    port=port,
                    user=f"pinger_{proto}",
                    mode="ping",
                    count=count,
                    interval=0.0,
                    scenario="SMOKE",
                    run_id=1,
                    out_dir=tmpdir,
                )
                self.assertEqual(ping_summary["sent"], count)
                self.assertEqual(ping_summary["received"], count)
                self._verify_csv_file(ping_summary["csv_path"], expected_rows=count)

                # B. Uji Benchmark Mode MSG (receiver + sender, --count 20)
                recv_result: dict[str, object] = {}
                recv_ready = threading.Event()

                def _receiver_worker() -> None:
                    recv_ready.set()
                    res = run_benchmark(
                        proto=proto,
                        host="127.0.0.1",
                        port=port,
                        user=f"receiver_{proto}",
                        mode="msg",
                        role="receiver",
                        count=count,
                        size=64,
                        scenario="SMOKE",
                        run_id=1,
                        out_dir=tmpdir,
                        receiver_idle_timeout=0.6,
                    )
                    recv_result["summary"] = res

                recv_thread = threading.Thread(target=_receiver_worker, daemon=True)
                recv_thread.start()
                recv_ready.wait(timeout=1.0)
                # Beri waktu singkat agar receiver selesai JOIN sebelum sender mengirim pesan
                time.sleep(0.15)

                send_summary = run_benchmark(
                    proto=proto,
                    host="127.0.0.1",
                    port=port,
                    user=f"sender_{proto}",
                    mode="msg",
                    role="sender",
                    count=count,
                    size=64,
                    interval=0.002,
                    scenario="SMOKE",
                    run_id=1,
                    out_dir=tmpdir,
                )
                recv_thread.join(timeout=5.0)

                self.assertEqual(send_summary["sent"], count)
                self._verify_csv_file(send_summary["csv_path"], expected_rows=count)

                self.assertIn("summary", recv_result)
                recv_summary = recv_result["summary"]
                assert isinstance(recv_summary, dict)
                self.assertEqual(recv_summary["received"], count)
                self._verify_csv_file(recv_summary["csv_path"], expected_rows=count)

            finally:
                server.stop()

    def test_benchmark_smoke_tcp(self) -> None:
        """Smoke test benchmark (--count 20) pada TCP."""
        self._run_smoke_for_proto("tcp")

    def test_benchmark_smoke_udp(self) -> None:
        """Smoke test benchmark (--count 20) pada UDP."""
        self._run_smoke_for_proto("udp")


class TestWebUiIntegration(unittest.TestCase):
    """5. Pengujian integrasi Full Web UI/UX & Backend API Bridge."""

    def test_web_ui_endpoints_and_full_flow(self) -> None:
        """Menguji halaman HTML, join TCP/UDP, chat, ping, framing simulator, dan benchmark via HTTP API."""
        import json
        import urllib.request

        with tempfile.TemporaryDirectory() as tmpdir:
            httpd, controller = create_http_server(
                ui_host="127.0.0.1",
                ui_port=0,
                chat_host="127.0.0.1",
                tcp_port=0,
                udp_port=0,
                log_dir=tmpdir,
            )
            ui_port = httpd.server_address[1]
            base_url = f"http://127.0.0.1:{ui_port}"

            srv_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            srv_thread.start()

            def _req(path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, str, bytes]:
                data_bytes = None
                headers = {}
                if payload is not None:
                    data_bytes = json.dumps(payload).encode("utf-8")
                    headers["Content-Type"] = "application/json"
                req = urllib.request.Request(f"{base_url}{path}", data=data_bytes, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    return resp.status, resp.headers.get("Content-Type", ""), resp.read()

            try:
                # 1. Halaman utama HTML
                status, ctype, raw_html = _req("/")
                self.assertEqual(status, 200)
                self.assertIn("text/html", ctype)
                self.assertIn(b"DualChat Telemetry & Protocol Studio", raw_html)

                # 2. Join 2 client TCP & 2 client UDP melalui API
                for proto in ("tcp", "udp"):
                    for uname in ("alice", "bob"):
                        st, _, body = _req("/api/clients/join", "POST", {"proto": proto, "user": uname})
                        self.assertEqual(st, 200)
                        parsed = json.loads(body.decode("utf-8"))
                        self.assertTrue(parsed["client"]["connected"])

                # 3. Kirim pesan MSG dari alice ke bob (di TCP dan UDP)
                for proto in ("tcp", "udp"):
                    st, _, _ = _req(
                        "/api/clients/action",
                        "POST",
                        {"client_id": f"{proto}_alice", "action": "msg", "text": f"halo dari {proto}"},
                    )
                    self.assertEqual(st, 200)

                    # Kirim PING dari alice
                    st_ping, _, ping_body = _req(
                        "/api/clients/action",
                        "POST",
                        {"client_id": f"{proto}_alice", "action": "ping"},
                    )
                    self.assertEqual(st_ping, 200)
                    ping_json = json.loads(ping_body.decode("utf-8"))
                    self.assertIsNotNone(ping_json["client"]["last_rtt_ms"])

                time.sleep(0.15)
                _, _, status_body = _req("/api/status")
                status_data = json.loads(status_body.decode("utf-8"))
                self.assertEqual(len(status_data["tcp_server"]["active_users"]), 2)
                self.assertEqual(len(status_data["udp_server"]["active_users"]), 2)

                # 4. Uji Simulator Framing
                st_sim, _, sim_body = _req(
                    "/api/framing/simulate",
                    "POST",
                    {"text": "uji framing baris 1\nbaris 2", "split_at": 20},
                )
                self.assertEqual(st_sim, 200)
                sim_data = json.loads(sim_body.decode("utf-8"))
                self.assertTrue(sim_data["tcp"]["reassembled_ok"])
                self.assertEqual(sim_data["tcp"]["wire_bytes_len"], sim_data["udp"]["wire_bytes_len"] + 1)

                # 5. Uji Benchmark Head-to-Head (both TCP & UDP) dari UI API
                st_bm, _, bm_body = _req(
                    "/api/benchmark/run",
                    "POST",
                    {
                        "proto": "both",
                        "mode": "ping",
                        "count": 10,
                        "size": 64,
                        "interval": 0.0,
                        "scenario": "UI_TEST",
                        "run": 1,
                    },
                )
                self.assertEqual(st_bm, 200)
                bm_data = json.loads(bm_body.decode("utf-8"))
                self.assertEqual(len(bm_data["results"]), 2)
                self.assertEqual(bm_data["results"][0]["received"], 10)
                self.assertEqual(bm_data["results"][1]["received"], 10)

            finally:
                httpd.shutdown()
                httpd.server_close()
                controller.stop_all()


if __name__ == "__main__":
    unittest.main()
