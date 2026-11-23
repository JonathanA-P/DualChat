import 'dart:convert';

enum TransportProto { tcp, udp }

enum MessageType {
  join('JOIN'),
  ackJoin('ACK_JOIN'),
  msg('MSG'),
  ping('PING'),
  pong('PONG'),
  leave('LEAVE'),
  err('ERR');

  final String wireName;
  const MessageType(this.wireName);

  static MessageType fromWire(String raw) {
    return MessageType.values.firstWhere(
      (m) => m.wireName == raw,
      orElse: () => MessageType.msg,
    );
  }
}

class ChatMessage {
  final MessageType type;
  final int seq;
  final double ts;
  final String user;
  final String data;
  final bool isSelf;
  final double? rttMs;
  final String? warning;

  const ChatMessage({
    required this.type,
    required this.seq,
    required this.ts,
    required this.user,
    required this.data,
    this.isSelf = false,
    this.rttMs,
    this.warning,
  });

  Map<String, dynamic> toJson() => {
        'type': type.wireName,
        'seq': seq,
        'ts': ts,
        'user': user,
        'data': data,
      };

  String toWireJson() => jsonEncode(toJson());

  factory ChatMessage.fromJson(Map<String, dynamic> json, {bool isSelf = false}) {
    return ChatMessage(
      type: MessageType.fromWire(json['type']?.toString() ?? 'MSG'),
      seq: (json['seq'] as num?)?.toInt() ?? 0,
      ts: (json['ts'] as num?)?.toDouble() ?? 0.0,
      user: json['user']?.toString() ?? 'unknown',
      data: json['data']?.toString() ?? '',
      isSelf: isSelf,
    );
  }

  DateTime get dateTime => DateTime.fromMillisecondsSinceEpoch((ts * 1000).toInt());
}

class NetworkMetrics {
  int packetsSent = 0;
  int packetsReceived = 0;
  int bytesSent = 0;
  int bytesReceived = 0;
  int outOfOrderCount = 0;
  double? lastRttMs;
  double totalRttMs = 0;
  int rttSamples = 0;

  double get avgRttMs => rttSamples > 0 ? (totalRttMs / rttSamples) : 0.0;

  void reset() {
    packetsSent = 0;
    packetsReceived = 0;
    bytesSent = 0;
    bytesReceived = 0;
    outOfOrderCount = 0;
    lastRttMs = null;
    totalRttMs = 0;
    rttSamples = 0;
  }
}
