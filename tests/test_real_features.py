"""
tests/test_real_features.py - Real feature integration tests for DualChat.

Verifies:
1. Friend Routing: [DM:<friend>] delivered over TCP & UDP; intended friend accepts,
   other clients filter out.
2. Group Routing: [GROUP:<name>] delivered to group members over TCP & UDP.
3. Feeds & Comments: [FEED] broadcast and [FEED_COMMENT:<post_id>] functionality over TCP & UDP.
4. Media Payloads: [IMAGE:...] and [VOICE:...] transmission, decoding, and MAX_DATA_BYTES validation.
"""

from __future__ import annotations

import re
import tempfile
import time
import unittest
from typing import Any

from chat_core import (
    MAX_DATA_BYTES,
    decode_message,
    encode_message,
    make_message,
    validate_message_dict,
)
from server import ProtocolServerWorker
from transport import create_transport


def filter_client_incoming_message(
    my_username: str,
    my_groups: list[str],
    msg: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Simulates the exact routing and filtering logic from Flutter/DualChat client:
    - Ignores self echoes.
    - [FEED]: broadcast to feed timeline.
    - [FEED_COMMENT:<post_id>]: attached to specific feed post.
    - [DM:<recipient>]: only accepted if my_username matches recipient.
    - [GROUP:<name>]: accepted if user belongs to the group.
    - Plain broadcast: accepted by default general room.
    """
    sender = msg.get("user", "")
    if sender.lower() == my_username.lower():
        return None  # self echo

    data = msg.get("data", "")

    # 1. Feeds
    if data.startswith("[FEED]"):
        content = data[6:].strip()
        return {"category": "FEED", "author": sender, "content": content}

    feed_comment_match = re.match(r"^\[FEED_COMMENT:([^\]]+)\]\s*(.*)$", data)
    if feed_comment_match:
        post_id = feed_comment_match.group(1).strip()
        comment_text = feed_comment_match.group(2).strip()
        return {
            "category": "FEED_COMMENT",
            "postId": post_id,
            "author": sender,
            "comment": comment_text,
        }

    # 2. Friend routing (1-on-1 DM)
    dm_match = re.match(r"^\[DM:([^\]]+)\]\s*(.*)$", data)
    if dm_match:
        recipient = dm_match.group(1).strip().lower()
        clean_text = dm_match.group(2)
        if recipient == my_username.lower():
            return {
                "category": "DM",
                "friend": sender,
                "text": clean_text,
                "displayed_in_tab": "Friends",
            }
        else:
            # Addressed to someone else; discarded by this client
            return None

    # 3. Group routing
    group_match = re.match(r"^\[GROUP:([^\]]+)\]\s*(.*)$", data)
    if group_match:
        group_name = group_match.group(1).strip()
        clean_text = group_match.group(2)
        if any(g.lower() == group_name.lower() for g in my_groups):
            return {
                "category": "GROUP",
                "group": group_name,
                "sender": sender,
                "text": clean_text,
                "displayed_in_tab": "Group",
            }
        else:
            # User not in this group
            return None

    # 4. Plain broadcast
    return {
        "category": "BROADCAST",
        "sender": sender,
        "text": data,
        "displayed_in_tab": "Group",
    }


def parse_media_payload(raw_text: str) -> dict[str, Any] | None:
    """
    Parses [IMAGE:...] and [VOICE:...] payloads according to application specs.
    """
    # Image: [IMAGE:<tag>:<title>:<icon>:<caption?>]
    img_match = re.match(r"^\[IMAGE:([^:]+):([^:]+):([^:]+):?(.*)\]$", raw_text.strip())
    if img_match:
        return {
            "mediaType": "IMAGE",
            "tag": img_match.group(1),
            "title": img_match.group(2),
            "icon": img_match.group(3),
            "caption": img_match.group(4) or "",
        }

    # Voice Note: [VOICE:<duration_seconds>:<samples>]
    vn_match = re.match(r"^\[VOICE:(\d+):([0-9.,]+)\]$", raw_text.strip())
    if vn_match:
        duration = int(vn_match.group(1))
        samples = [float(s) for s in vn_match.group(2).split(",") if s]
        return {
            "mediaType": "VOICE",
            "durationSec": duration,
            "samples": samples,
        }

    return None


class TestRealFeatures(unittest.TestCase):
    """Pengujian komprehensif fitur nyata DualChat pada TCP & UDP."""

    def _setup_server_and_clients(
        self, proto: str, tmpdir: str
    ) -> tuple[ProtocolServerWorker, Any, Any, Any]:
        server = ProtocolServerWorker(
            proto=proto,
            host="127.0.0.1",
            port=0,
            log_dir=tmpdir,
            inactivity_timeout=60.0,
        )
        server.start()
        port = server.bound_port

        c_alice = create_transport(proto)
        c_bob = create_transport(proto)
        c_charlie = create_transport(proto)

        # Connect and join all 3 clients
        for client, user in [(c_alice, "alice"), (c_bob, "bob"), (c_charlie, "charlie")]:
            client.client_connect("127.0.0.1", port)
            client.client_send(make_message("JOIN", 0, user, ""))
            ack = client.client_recv(timeout=2.0)
            self.assertIsNotNone(ack)
            self.assertEqual(ack["type"], "ACK_JOIN")

        # Flush any join notifications
        time.sleep(0.05)
        for client in (c_alice, c_bob, c_charlie):
            while True:
                msg = client.client_recv(timeout=0.1)
                if msg is None:
                    break

        return server, c_alice, c_bob, c_charlie

    def _test_friend_dm_routing_for_proto(self, proto: str) -> None:
        """
        Friend routing: [DM:<friend>]
        Alice sends [DM:bob] text.
        Bob receives and processes it in Friends tab.
        Charlie filters it out.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            server, c_alice, c_bob, c_charlie = self._setup_server_and_clients(proto, tmpdir)
            try:
                dm_payload = "[DM:bob] Halo Bob, apa kabar kawan?"
                c_alice.client_send(make_message("MSG", 1, "alice", dm_payload))

                # Bob must receive the raw message
                msg_bob = c_bob.client_recv(timeout=2.0)
                self.assertIsNotNone(msg_bob)
                self.assertEqual(msg_bob["user"], "alice")
                self.assertEqual(msg_bob["data"], dm_payload)

                # Simulate Bob client routing
                bob_routed = filter_client_incoming_message(
                    my_username="bob",
                    my_groups=["General Broadcast"],
                    msg=msg_bob,
                )
                self.assertIsNotNone(bob_routed)
                self.assertEqual(bob_routed["category"], "DM")
                self.assertEqual(bob_routed["friend"], "alice")
                self.assertEqual(bob_routed["text"], "Halo Bob, apa kabar kawan?")
                self.assertEqual(bob_routed["displayed_in_tab"], "Friends")

                # Charlie also receives broadcast wire message but filters it out
                msg_charlie = c_charlie.client_recv(timeout=2.0)
                self.assertIsNotNone(msg_charlie)
                charlie_routed = filter_client_incoming_message(
                    my_username="charlie",
                    my_groups=["General Broadcast"],
                    msg=msg_charlie,
                )
                self.assertIsNone(charlie_routed, "Charlie harus memfilter DM yang bukan untuknya")

            finally:
                c_alice.close()
                c_bob.close()
                c_charlie.close()
                server.stop()

    def test_friend_dm_routing_tcp(self) -> None:
        self._test_friend_dm_routing_for_proto("tcp")

    def test_friend_dm_routing_udp(self) -> None:
        self._test_friend_dm_routing_for_proto("udp")

    def _test_group_routing_for_proto(self, proto: str) -> None:
        """
        Group routing: [GROUP:<name>]
        Alice sends to group 'Kelompok Jarkom'.
        Bob is a member -> receives and routes to Group tab.
        Charlie is NOT a member -> filtered out.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            server, c_alice, c_bob, c_charlie = self._setup_server_and_clients(proto, tmpdir)
            try:
                group_payload = "[GROUP:Kelompok Jarkom] Laporan bab 3 sudah selesai!"
                c_alice.client_send(make_message("MSG", 1, "alice", group_payload))

                msg_bob = c_bob.client_recv(timeout=2.0)
                self.assertIsNotNone(msg_bob)
                bob_routed = filter_client_incoming_message(
                    my_username="bob",
                    my_groups=["Kelompok Jarkom"],
                    msg=msg_bob,
                )
                self.assertIsNotNone(bob_routed)
                self.assertEqual(bob_routed["category"], "GROUP")
                self.assertEqual(bob_routed["group"], "Kelompok Jarkom")
                self.assertEqual(bob_routed["text"], "Laporan bab 3 sudah selesai!")
                self.assertEqual(bob_routed["displayed_in_tab"], "Group")

                msg_charlie = c_charlie.client_recv(timeout=2.0)
                self.assertIsNotNone(msg_charlie)
                charlie_routed = filter_client_incoming_message(
                    my_username="charlie",
                    my_groups=["Other Group"],
                    msg=msg_charlie,
                )
                self.assertIsNone(charlie_routed, "Charlie bukan anggota grup, pesan harus diabaikan")

            finally:
                c_alice.close()
                c_bob.close()
                c_charlie.close()
                server.stop()

    def test_group_routing_tcp(self) -> None:
        self._test_group_routing_for_proto("tcp")

    def test_group_routing_udp(self) -> None:
        self._test_group_routing_for_proto("udp")

    def _test_feed_and_comment_for_proto(self, proto: str) -> None:
        """
        Feeds: [FEED] broadcast and [FEED_COMMENT:<post_id>] functionality.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            server, c_alice, c_bob, c_charlie = self._setup_server_and_clients(proto, tmpdir)
            try:
                # 1. Feed Broadcast
                feed_payload = "[FEED] Berhasil capture 3-way handshake TCP di Wireshark!"
                c_alice.client_send(make_message("MSG", 1, "alice", feed_payload))

                for client, uname in [(c_bob, "bob"), (c_charlie, "charlie")]:
                    raw = client.client_recv(timeout=2.0)
                    self.assertIsNotNone(raw)
                    routed = filter_client_incoming_message(
                        my_username=uname,
                        my_groups=["General Broadcast"],
                        msg=raw,
                    )
                    self.assertIsNotNone(routed)
                    self.assertEqual(routed["category"], "FEED")
                    self.assertEqual(routed["author"], "alice")
                    self.assertEqual(
                        routed["content"],
                        "Berhasil capture 3-way handshake TCP di Wireshark!",
                    )

                # 2. Feed Comment
                comment_payload = "[FEED_COMMENT:post_1] Mantap, RTT-nya sangat stabil!"
                c_bob.client_send(make_message("MSG", 2, "bob", comment_payload))

                for client, uname in [(c_alice, "alice"), (c_charlie, "charlie")]:
                    raw = client.client_recv(timeout=2.0)
                    self.assertIsNotNone(raw)
                    routed = filter_client_incoming_message(
                        my_username=uname,
                        my_groups=["General Broadcast"],
                        msg=raw,
                    )
                    self.assertIsNotNone(routed)
                    self.assertEqual(routed["category"], "FEED_COMMENT")
                    self.assertEqual(routed["postId"], "post_1")
                    self.assertEqual(routed["author"], "bob")
                    self.assertEqual(routed["comment"], "Mantap, RTT-nya sangat stabil!")

            finally:
                c_alice.close()
                c_bob.close()
                c_charlie.close()
                server.stop()

    def test_feed_and_comment_tcp(self) -> None:
        self._test_feed_and_comment_for_proto("tcp")

    def test_feed_and_comment_udp(self) -> None:
        self._test_feed_and_comment_for_proto("udp")

    def _test_media_payloads_for_proto(self, proto: str) -> None:
        """
        Image and Voice Note payloads transmission and decoding.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            server, c_alice, c_bob, c_charlie = self._setup_server_and_clients(proto, tmpdir)
            try:
                # 1. Image payload transmission & parsing
                img_data = "[IMAGE:net_topology:Diagram Jaringan Lab:🌐:Topologi 3-Tier Jarkom]"
                c_alice.client_send(make_message("MSG", 10, "alice", img_data))

                recv_msg = c_bob.client_recv(timeout=2.0)
                self.assertIsNotNone(recv_msg)
                parsed_img = parse_media_payload(recv_msg["data"])
                self.assertIsNotNone(parsed_img)
                self.assertEqual(parsed_img["mediaType"], "IMAGE")
                self.assertEqual(parsed_img["tag"], "net_topology")
                self.assertEqual(parsed_img["title"], "Diagram Jaringan Lab")
                self.assertEqual(parsed_img["icon"], "🌐")
                self.assertEqual(parsed_img["caption"], "Topologi 3-Tier Jarkom")

                # 2. Voice Note payload transmission & parsing
                vn_data = "[VOICE:4:2.5,4.0,1.8,3.2,0.5]"
                c_alice.client_send(make_message("MSG", 11, "alice", vn_data))

                recv_vn = c_bob.client_recv(timeout=2.0)
                self.assertIsNotNone(recv_vn)
                parsed_vn = parse_media_payload(recv_vn["data"])
                self.assertIsNotNone(parsed_vn)
                self.assertEqual(parsed_vn["mediaType"], "VOICE")
                self.assertEqual(parsed_vn["durationSec"], 4)
                self.assertEqual(parsed_vn["samples"], [2.5, 4.0, 1.8, 3.2, 0.5])

                # 3. Payload size boundary check (under MAX_DATA_BYTES)
                large_caption = "x" * 7000
                large_img = f"[IMAGE:large:Besar:🖼️:{large_caption}]"
                self.assertLess(len(large_img.encode("utf-8")), MAX_DATA_BYTES)

                c_alice.client_send(make_message("MSG", 12, "alice", large_img))
                recv_large = c_bob.client_recv(timeout=2.0)
                self.assertIsNotNone(recv_large)
                parsed_large = parse_media_payload(recv_large["data"])
                self.assertIsNotNone(parsed_large)
                self.assertEqual(parsed_large["caption"], large_caption)

                # 4. Oversized payload (> MAX_DATA_BYTES) validation
                oversized_data = "a" * (MAX_DATA_BYTES + 10)
                is_valid, err = validate_message_dict(
                    {"type": "MSG", "seq": 1, "ts": 1.0, "user": "alice", "data": oversized_data}
                )
                self.assertFalse(is_valid)
                self.assertIn("melebihi batas maksimum", str(err))

            finally:
                c_alice.close()
                c_bob.close()
                c_charlie.close()
                server.stop()

    def test_media_payloads_tcp(self) -> None:
        self._test_media_payloads_for_proto("tcp")

    def test_media_payloads_udp(self) -> None:
        self._test_media_payloads_for_proto("udp")


if __name__ == "__main__":
    unittest.main()
