import 'dart:convert';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarkom_project/models/chat_message.dart';

void main() {
  group('DualChat Protocol & Wire Compatibility Tests', () {
    test('Stream framing decomposes concatenated frames on newline', () {
      final msg1 = ChatMessage(
        type: MessageType.msg,
        seq: 0,
        ts: 100.0,
        user: 'alice',
        data: 'pesan 1',
      );
      final msg2 = ChatMessage(
        type: MessageType.msg,
        seq: 1,
        ts: 101.0,
        user: 'alice',
        data: 'pesan 2',
      );

      // Concatenated byte-stream as TCP might deliver
      final wireBytes = utf8.encode('${msg1.toWireJson()}\n${msg2.toWireJson()}\n');

      final buffer = <int>[];
      buffer.addAll(wireBytes);

      final decodedMessages = <ChatMessage>[];
      while (true) {
        final idx = buffer.indexOf(10);
        if (idx == -1) break;
        final frame = buffer.sublist(0, idx);
        buffer.removeRange(0, idx + 1);
        if (frame.isEmpty) continue;
        final jsonMap = jsonDecode(utf8.decode(frame)) as Map<String, dynamic>;
        decodedMessages.add(ChatMessage.fromJson(jsonMap));
      }

      expect(decodedMessages.length, 2);
      expect(decodedMessages[0].data, 'pesan 1');
      expect(decodedMessages[0].seq, 0);
      expect(decodedMessages[1].data, 'pesan 2');
      expect(decodedMessages[1].seq, 1);
    });

    test('Escaped newline within message body is preserved and does not break framing', () {
      final msg = ChatMessage(
        type: MessageType.msg,
        seq: 2,
        ts: 102.0,
        user: 'bob',
        data: 'Baris satu\nBaris dua\nBaris tiga',
      );

      final wireJson = msg.toWireJson();
      // In wire JSON, literal newlines in data are escaped as \n
      expect(wireJson, contains(r'\n'));
      expect(wireJson.contains('\n'), isFalse); // No literal newline byte

      final wireBytes = utf8.encode('$wireJson\n');
      final newlineCount = wireBytes.where((b) => b == 10).length;
      expect(newlineCount, 1); // Only the framing delimiter is 0x0A

      final decoded = ChatMessage.fromJson(jsonDecode(wireJson));
      expect(decoded.data, 'Baris satu\nBaris dua\nBaris tiga');
    });

    test('NetworkMetrics accurately tracks stats', () {
      final metrics = NetworkMetrics();
      expect(metrics.packetsSent, 0);
      expect(metrics.avgRttMs, 0.0);

      metrics.packetsSent += 2;
      metrics.packetsReceived += 2;
      metrics.totalRttMs += 2.0;
      metrics.rttSamples += 1;
      metrics.lastRttMs = 2.0;

      expect(metrics.packetsSent, 2);
      expect(metrics.avgRttMs, 2.0);
      expect(metrics.lastRttMs, 2.0);

      metrics.reset();
      expect(metrics.packetsSent, 0);
      expect(metrics.lastRttMs, isNull);
    });
  });
}
