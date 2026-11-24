import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import '../models/chat_message.dart';

enum ConnectionStatus { disconnected, connecting, connected }

class DualChatClient {
  TransportProto proto = TransportProto.tcp;
  String host = '127.0.0.1';
  int port = 5000;
  String username = 'alice';

  ConnectionStatus status = ConnectionStatus.disconnected;
  final NetworkMetrics metrics = NetworkMetrics();

  int _outgoingSeq = 0;
  int _pingSeq = 0;
  final Map<String, int> _expectedSeqByUser = {};

  // TCP resources
  Socket? _tcpSocket;
  final List<int> _tcpBuffer = [];

  // UDP resources
  RawDatagramSocket? _udpSocket;
  InternetAddress? _resolvedUdpAddress;

  Timer? _keepAliveTimer;

  // Stream controllers
  final _messageController = StreamController<ChatMessage>.broadcast();
  final _statusController = StreamController<ConnectionStatus>.broadcast();
  final _metricsController = StreamController<NetworkMetrics>.broadcast();

  Stream<ChatMessage> get onMessage => _messageController.stream;
  Stream<ConnectionStatus> get onStatusChanged => _statusController.stream;
  Stream<ConnectionStatus> get statusStream => _statusController.stream;
  Stream<NetworkMetrics> get onMetricsUpdated => _metricsController.stream;

  int _nextSeq() {
    final s = _outgoingSeq;
    _outgoingSeq++;
    return s;
  }

  double _currentTimestamp() {
    return DateTime.now().millisecondsSinceEpoch / 1000.0;
  }

  Future<bool> connect({
    required TransportProto proto,
    required String host,
    int? port,
    int? tcpPort,
    int? udpPort,
    required String username,
  }) async {
    await disconnect(sendLeave: true);

    this.proto = proto;
    final targetPort = port ??
        (proto == TransportProto.tcp ? (tcpPort ?? 5000) : (udpPort ?? 5001));
    String targetHost = host.trim();
    // Android Emulator loopback alias: 127.0.0.1 on Android points to the emulator itself,
    // while 10.0.2.2 points to 127.0.0.1 on the host machine.
    if (!kIsWeb && Platform.isAndroid && (targetHost == '127.0.0.1' || targetHost == 'localhost')) {
      targetHost = '10.0.2.2';
    }
    this.host = targetHost;
    this.port = targetPort;
    this.username = username.trim();
    _outgoingSeq = 0;
    _expectedSeqByUser.clear();
    metrics.reset();

    final isTest = Platform.environment.containsKey('FLUTTER_TEST') ||
        WidgetsBinding.instance.runtimeType.toString().contains('Test');
    if (kIsWeb || isTest) {
      _setStatus(ConnectionStatus.disconnected);
      if (kIsWeb) {
        _emitSystemMessage(
          'Browser Sandbox: Web Browser (Chrome/Edge) tidak mengizinkan raw socket TCP/UDP (dart:io).\n'
          'Solusi termudah: Jalankan Web UI bawaan di terminal:\n'
          '  python web_ui.py --ui-port 8080\n'
          'lalu buka http://127.0.0.1:8080 (backend Python yang mengelola socket TCP/UDP murni).',
          isError: true,
        );
      }
      return false;
    }

    _setStatus(ConnectionStatus.connecting);

    final joinCompleter = Completer<bool>();
    Timer? joinTimer;

    try {
      if (proto == TransportProto.tcp) {
        // TCP: 3-way handshake on connect()
        _tcpSocket = await Socket.connect(this.host, this.port, timeout: const Duration(seconds: 4));
        _tcpBuffer.clear();

        _tcpSocket!.listen(
          (Uint8List chunk) {
            metrics.packetsReceived++;
            metrics.bytesReceived += chunk.length;
            _metricsController.add(metrics);

            _handleTcpChunk(chunk, joinCompleter);
          },
          onError: (e) {
            _onDisconnected('TCP Socket error: $e');
            if (!joinCompleter.isCompleted) joinCompleter.complete(false);
          },
          onDone: () {
            _onDisconnected('Koneksi TCP ditutup server (EOF/FIN)');
            if (!joinCompleter.isCompleted) joinCompleter.complete(false);
          },
        );

        // Send JOIN over TCP
        final joinMsg = ChatMessage(
          type: MessageType.join,
          seq: _nextSeq(),
          ts: _currentTimestamp(),
          user: this.username,
          data: '',
          isSelf: true,
        );
        _sendTcpRaw(joinMsg.toWireJson());
      } else {
        // UDP: connectionless, bind ephemeral port
        _udpSocket = await RawDatagramSocket.bind(InternetAddress.anyIPv4, 0);
        _udpSocket!.listen(
          (RawSocketEvent event) {
            if (event == RawSocketEvent.read) {
              final dg = _udpSocket?.receive();
              if (dg != null) {
                metrics.packetsReceived++;
                metrics.bytesReceived += dg.data.length;
                _metricsController.add(metrics);

                _handleUdpDatagram(dg.data, joinCompleter);
              }
            }
          },
          onError: (e) {
            _onDisconnected('UDP Socket error: $e');
            if (!joinCompleter.isCompleted) joinCompleter.complete(false);
          },
        );

        // Resolve host address for UDP (handles IP string, hostname like localhost, or mDNS)
        InternetAddress? resolved = InternetAddress.tryParse(this.host);
        if (resolved == null) {
          final lookedUp = await InternetAddress.lookup(this.host);
          if (lookedUp.isNotEmpty) {
            resolved = lookedUp.first;
          }
        }
        if (resolved == null) {
          throw Exception('Host tidak dapat ditemukan: ${this.host}');
        }
        _resolvedUdpAddress = resolved;

        // Send JOIN over UDP
        final joinMsg = ChatMessage(
          type: MessageType.join,
          seq: _nextSeq(),
          ts: _currentTimestamp(),
          user: this.username,
          data: '',
          isSelf: true,
        );
        _sendUdpRaw(joinMsg.toWireJson());

        // UDP rule: timeout 3.5 detik jika tidak ada balasan ACK_JOIN
        joinTimer = Timer(const Duration(milliseconds: 3500), () {
          if (!joinCompleter.isCompleted) {
            _emitSystemMessage(
              'JOIN gagal/hilang (UDP timeout 3.5s — sifat connectionless & unacknowledged)',
              isError: true,
            );
            joinCompleter.complete(false);
            disconnect(sendLeave: false);
          }
        });
      }

      final success = await joinCompleter.future;
      joinTimer?.cancel();

      if (success) {
        _setStatus(ConnectionStatus.connected);
        return true;
      } else {
        _setStatus(ConnectionStatus.disconnected);
        return false;
      }
    } catch (e) {
      joinTimer?.cancel();
      _setStatus(ConnectionStatus.disconnected);
      String hint = '';
      if (!kIsWeb && Platform.isAndroid && (host == '127.0.0.1' || host == '10.0.2.2')) {
        hint = '\n(Tip: Jalankan server di terminal PC Anda: python server.py --proto both)';
      }
      _emitSystemMessage('Gagal terhubung ke ${this.host}:$port: $e$hint', isError: true);
      return false;
    }
  }

  void _handleTcpChunk(Uint8List chunk, Completer<bool> joinCompleter) {
    _tcpBuffer.addAll(chunk);

    while (true) {
      final newlineIdx = _tcpBuffer.indexOf(10); // 0x0A = '\n'
      if (newlineIdx == -1) break;

      final frame = _tcpBuffer.sublist(0, newlineIdx);
      _tcpBuffer.removeRange(0, newlineIdx + 1);

      if (frame.isEmpty) continue;

      try {
        final text = utf8.decode(frame);
        final json = jsonDecode(text) as Map<String, dynamic>;
        _processDecodedMessage(json, joinCompleter);
      } catch (e) {
        _emitSystemMessage('Error decoding TCP frame: $e', isError: true);
      }
    }
  }

  void _handleUdpDatagram(Uint8List data, Completer<bool> joinCompleter) {
    try {
      final text = utf8.decode(data);
      final json = jsonDecode(text) as Map<String, dynamic>;
      _processDecodedMessage(json, joinCompleter);
    } catch (e) {
      _emitSystemMessage('Error decoding UDP datagram: $e', isError: true);
    }
  }

  void _processDecodedMessage(
    Map<String, dynamic> json,
    Completer<bool> joinCompleter,
  ) {
    final typeStr = json['type']?.toString() ?? 'MSG';
    final msgType = MessageType.fromWire(typeStr);
    final sender = json['user']?.toString() ?? 'unknown';
    final seq = (json['seq'] as num?)?.toInt() ?? 0;
    final data = json['data']?.toString() ?? '';
    final ts = (json['ts'] as num?)?.toDouble() ?? 0.0;

    // Handle handshake responses
    if (!joinCompleter.isCompleted) {
      if (msgType == MessageType.ackJoin) {
        _emitSystemMessage('JOIN Berhasil diterima server: "$data"');
        joinCompleter.complete(true);
        return;
      } else if (msgType == MessageType.err) {
        _emitSystemMessage('JOIN Ditolak server: "$data"', isError: true);
        joinCompleter.complete(false);
        return;
      }
    }

    // Sequence checking for out-of-order / packet loss on chat messages
    String? warning;
    if (msgType == MessageType.msg && sender != 'server' && sender != username) {
      final expected = _expectedSeqByUser[sender];
      if (expected != null && seq != expected) {
        // Jangan beri warning palsu jika pengirim baru reconnect (seq kembali ke awal 0 atau 1)
        if (seq > expected || (seq < expected && seq > 1)) {
          warning = 'pesan hilang/out-of-order dari [$sender]: diharapkan seq=$expected, diterima seq=$seq';
          metrics.outOfOrderCount++;
          _metricsController.add(metrics);
        }
      }
      _expectedSeqByUser[sender] = seq + 1;
    }

    // RTT calculation on PONG
    double? rtt;
    if (msgType == MessageType.pong) {
      final now = _currentTimestamp();
      rtt = (now - ts) * 1000.0;
      metrics.lastRttMs = rtt;
      metrics.totalRttMs += rtt;
      metrics.rttSamples++;
      _metricsController.add(metrics);
    } else if (msgType == MessageType.err && data.contains('belum JOIN')) {
      // Auto re-join jika session server ter-reset / timeout
      final joinMsg = ChatMessage(
        type: MessageType.join,
        seq: _nextSeq(),
        ts: _currentTimestamp(),
        user: username,
        data: '',
        isSelf: true,
      );
      _sendPacket(joinMsg);
    }

    final msg = ChatMessage(
      type: msgType,
      seq: seq,
      ts: ts,
      user: sender,
      data: data,
      isSelf: sender == username,
      rttMs: rtt,
      warning: warning,
    );

    _messageController.add(msg);
  }

  Future<void> sendMessage(String text) async {
    if (status != ConnectionStatus.connected) return;

    final trimmed = text.trim();
    if (trimmed.isEmpty) return;

    if (trimmed == '/ping') {
      await sendPing();
      return;
    }

    final msg = ChatMessage(
      type: MessageType.msg,
      seq: _nextSeq(),
      ts: _currentTimestamp(),
      user: username,
      data: text,
      isSelf: true,
    );

    _sendPacket(msg);
    _messageController.add(msg);
  }

  Future<void> sendPing() async {
    if (status != ConnectionStatus.connected) return;

    final pingSeq = _pingSeq++;
    final ping = ChatMessage(
      type: MessageType.ping,
      seq: pingSeq,
      ts: _currentTimestamp(),
      user: username,
      data: '',
      isSelf: true,
    );

    _sendPacket(ping);
    _messageController.add(ping);
  }

  void _sendPacket(ChatMessage msg) {
    final wireJson = msg.toWireJson();
    if (proto == TransportProto.tcp) {
      _sendTcpRaw(wireJson);
    } else {
      _sendUdpRaw(wireJson);
    }
  }

  void _sendTcpRaw(String wireJson) {
    if (_tcpSocket == null) return;
    try {
      final wireBytes = utf8.encode('$wireJson\n');
      _tcpSocket!.add(wireBytes);
      _tcpSocket!.flush();

      metrics.packetsSent++;
      metrics.bytesSent += wireBytes.length;
      _metricsController.add(metrics);
    } catch (e) {
      _emitSystemMessage('Gagal kirim TCP: $e', isError: true);
    }
  }

  void _sendUdpRaw(String wireJson) {
    if (_udpSocket == null || _resolvedUdpAddress == null) return;
    try {
      final wireBytes = utf8.encode(wireJson);
      _udpSocket!.send(wireBytes, _resolvedUdpAddress!, port);

      metrics.packetsSent++;
      metrics.bytesSent += wireBytes.length;
      _metricsController.add(metrics);
    } catch (e) {
      _emitSystemMessage('Gagal kirim UDP: $e', isError: true);
    }
  }

  Future<void> disconnect({bool sendLeave = true}) async {
    if (sendLeave && (status == ConnectionStatus.connected || _udpSocket != null || _tcpSocket != null)) {
      final leaveMsg = ChatMessage(
        type: MessageType.leave,
        seq: _nextSeq(),
        ts: _currentTimestamp(),
        user: username,
        data: '',
        isSelf: true,
      );
      _sendPacket(leaveMsg);
      // Tunggu sejenak agar OS sempat mem-flush datagram UDP sebelum socket di-close
      await Future.delayed(const Duration(milliseconds: 30));
    }

    _setStatus(ConnectionStatus.disconnected);

    try {
      await _tcpSocket?.close();
    } catch (_) {}
    _tcpSocket = null;
    _tcpBuffer.clear();

    try {
      _udpSocket?.close();
    } catch (_) {}
    _udpSocket = null;
    _resolvedUdpAddress = null;
  }

  void _onDisconnected(String reason) {
    if (status != ConnectionStatus.disconnected) {
      _setStatus(ConnectionStatus.disconnected);
      _emitSystemMessage(reason, isError: true);
    }
  }

  void _setStatus(ConnectionStatus newStatus) {
    status = newStatus;
    _statusController.add(status);
    if (status == ConnectionStatus.connected) {
      _keepAliveTimer?.cancel();
      _keepAliveTimer = Timer.periodic(const Duration(seconds: 25), (_) {
        if (status == ConnectionStatus.connected) {
          sendPing();
        }
      });
    } else {
      _keepAliveTimer?.cancel();
      _keepAliveTimer = null;
    }
  }

  void _emitSystemMessage(String text, {bool isError = false}) {
    _messageController.add(
      ChatMessage(
        type: isError ? MessageType.err : MessageType.ackJoin,
        seq: 0,
        ts: _currentTimestamp(),
        user: 'system',
        data: text,
      ),
    );
  }

  void dispose() {
    disconnect(sendLeave: true);
    _messageController.close();
    _statusController.close();
    _metricsController.close();
  }
}
