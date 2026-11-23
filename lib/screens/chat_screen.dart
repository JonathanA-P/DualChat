import 'dart:async';
import 'dart:convert';
import 'dart:math';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:image_picker/image_picker.dart';
import '../models/chat_message.dart';
import '../models/chat_room.dart';
import '../models/user_profile.dart';
import '../services/dualchat_client.dart';
import '../widgets/image_preview_dialog.dart';

class ChatScreen extends StatefulWidget {
  final ChatRoom room;
  final UserProfile profile;
  final DualChatClient client;
  final TransportProto currentProto;
  final ConnectionStatus connectionStatus;
  final VoidCallback onPing;
  final ValueChanged<ChatMessage> onMessageSent;
  final ValueChanged<TransportProto>? onSwitchProtocol;

  const ChatScreen({
    super.key,
    required this.room,
    required this.profile,
    required this.client,
    required this.currentProto,
    required this.connectionStatus,
    required this.onPing,
    required this.onMessageSent,
    this.onSwitchProtocol,
  });

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> with TickerProviderStateMixin {
  final TextEditingController _inputController = TextEditingController();
  final ScrollController _scrollController = ScrollController();

  late TransportProto _activeProto;
  late ConnectionStatus _connStatus;
  StreamSubscription? _statusSub;

  // Voice Recording state
  bool _isRecordingVoice = false;
  int _recordSeconds = 0;
  Timer? _recordTimer;
  final List<double> _liveWaveform = [];

  // Audio Playback simulation state
  int? _playingSeq;
  double _playbackRatio = 0.0;
  Timer? _playbackTimer;

  // Wireshark Burst test state
  bool _isBursting = false;

  @override
  void initState() {
    super.initState();
    _activeProto = widget.currentProto;
    _connStatus = widget.client.status;
    _statusSub = widget.client.statusStream.listen((s) {
      if (mounted) setState(() => _connStatus = s);
    });
    widget.room.markAsRead();
    widget.room.addListener(_onRoomUpdated);
  }

  @override
  void didUpdateWidget(covariant ChatScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.currentProto != widget.currentProto) {
      _activeProto = widget.currentProto;
    }
  }

  void _toggleProtocol() {
    final nextProto = _activeProto == TransportProto.tcp
        ? TransportProto.udp
        : TransportProto.tcp;

    setState(() {
      _activeProto = nextProto;
    });

    if (widget.onSwitchProtocol != null) {
      widget.onSwitchProtocol!(nextProto);
    } else {
      widget.client.proto = nextProto;
      widget.client.connect(
        host: widget.profile.host,
        tcpPort: widget.profile.tcpPort,
        udpPort: widget.profile.udpPort,
        username: widget.profile.username,
        proto: nextProto,
      );
    }

    final isTcp = nextProto == TransportProto.tcp;
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Row(
          children: [
            Icon(
              isTcp ? Icons.lock : Icons.bolt,
              color: isTcp ? const Color(0xFF10B981) : const Color(0xFFF59E0B),
              size: 18,
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                'Beralih ke ${nextProto.name.toUpperCase()} (Port ${isTcp ? widget.profile.tcpPort : widget.profile.udpPort}) • ${isTcp ? "Reliable Stream" : "Fast Datagram"}',
                style: const TextStyle(
                  fontWeight: FontWeight.w600,
                  fontSize: 12.5,
                ),
              ),
            ),
          ],
        ),
        behavior: SnackBarBehavior.floating,
        duration: const Duration(seconds: 2),
        backgroundColor: const Color(0xFF0F172A),
      ),
    );
  }

  void _onRoomUpdated() {
    if (!mounted) return;
    setState(() {});
    _scrollToBottom();
  }

  @override
  void dispose() {
    _statusSub?.cancel();
    widget.room.removeListener(_onRoomUpdated);
    _recordTimer?.cancel();
    _playbackTimer?.cancel();
    _inputController.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  void _dispatchWirePayload(String rawPayload, String displayPayload) {
    final now = DateTime.now().millisecondsSinceEpoch / 1000.0;
    final msg = ChatMessage(
      type: MessageType.msg,
      seq: widget.room.messages.length + 1,
      ts: now,
      user: widget.profile.username,
      data: displayPayload,
      isSelf: true,
    );

    widget.room.addMessage(msg);
    widget.onMessageSent(msg);

    // Wire protocol routing: Direct Message vs Group Message
    if (!widget.room.isGroup) {
      final friendUser = widget.room.members.firstWhere(
        (m) => m.toLowerCase() != widget.profile.username.toLowerCase(),
        orElse: () => widget.room.name,
      );
      widget.client.sendMessage('[DM:$friendUser] $rawPayload');
    } else {
      if (widget.room.id == 'group_general') {
        widget.client.sendMessage(rawPayload);
      } else {
        widget.client.sendMessage('[GROUP:${widget.room.name}] $rawPayload');
      }
    }

    setState(() {});
    _scrollToBottom();
  }

  void _handleSendText() {
    final text = _inputController.text.trim();
    if (text.isEmpty) return;

    if (text == '/ping') {
      widget.onPing();
      _inputController.clear();
      return;
    }

    _dispatchWirePayload(text, text);
    _inputController.clear();
  }

  // ==========================================
  // WIRESHARK BURST WORKLOAD TEST
  // ==========================================

  Future<void> _sendBurstTest({int count = 10}) async {
    if (_isBursting || _connStatus != ConnectionStatus.connected) return;
    setState(() => _isBursting = true);

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Row(
          children: [
            const Icon(Icons.flash_on, color: Colors.amber, size: 18),
            const SizedBox(width: 8),
            Text('Mengirim burst $count paket (${_activeProto.name.toUpperCase()}) ke Wireshark...'),
          ],
        ),
        duration: const Duration(seconds: 2),
        behavior: SnackBarBehavior.floating,
      ),
    );

    for (int i = 1; i <= count; i++) {
      if (!mounted) break;
      final payload = '[BURST #$i/$count] Uji beban ${_activeProto.name.toUpperCase()} pkt=$i ts=${DateTime.now().millisecondsSinceEpoch}';
      _dispatchWirePayload(payload, payload);
      await Future.delayed(const Duration(milliseconds: 60));
    }

    if (mounted) {
      setState(() => _isBursting = false);
    }
  }

  // ==========================================
  // ROOM & NETWORK INFO SHEET
  // ==========================================

  void _showRoomInfoSheet() {
    final isTcp = _activeProto == TransportProto.tcp;
    final protoColor = isTcp ? const Color(0xFF10B981) : const Color(0xFFF59E0B);

    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (ctx) {
        return Container(
          decoration: const BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
          ),
          padding: const EdgeInsets.fromLTRB(20, 14, 20, 30),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Center(
                child: Container(
                  width: 36,
                  height: 4,
                  decoration: BoxDecoration(
                    color: const Color(0xFFCBD5E1),
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              const SizedBox(height: 18),

              // Room Header
              Row(
                children: [
                  Container(
                    width: 52,
                    height: 52,
                    decoration: BoxDecoration(
                      color: widget.room.avatarColor.withAlpha(30),
                      shape: BoxShape.circle,
                    ),
                    alignment: Alignment.center,
                    child: Text(
                      widget.room.avatarEmoji,
                      style: const TextStyle(fontSize: 28),
                    ),
                  ),
                  const SizedBox(width: 14),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          widget.room.name,
                          style: const TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.w700,
                            color: Color(0xFF0F172A),
                          ),
                        ),
                        Text(
                          widget.room.isGroup
                              ? '${widget.room.memberCount} Anggota Tergabung'
                              : 'Obrolan Privat (2 Orang)',
                          style: const TextStyle(
                            fontSize: 12,
                            color: Color(0xFF64748B),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 18),
              const Divider(height: 1, color: Color(0xFFF1F5F9)),
              const SizedBox(height: 14),

              // Members List
              const Text(
                'Daftar Anggota / Partisipan:',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                  color: Color(0xFF334155),
                ),
              ),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8,
                runSpacing: 6,
                children: widget.room.members.map((m) {
                  final isMe = m.toLowerCase() == widget.profile.username.toLowerCase();
                  return Chip(
                    avatar: CircleAvatar(
                      backgroundColor: isMe ? const Color(0xFF0F172A) : const Color(0xFFE2E8F0),
                      child: Text(
                        m.isNotEmpty ? m[0].toUpperCase() : '?',
                        style: TextStyle(
                          fontSize: 10,
                          color: isMe ? Colors.white : const Color(0xFF0F172A),
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                    ),
                    label: Text(
                      isMe ? '$m (Anda)' : m,
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: isMe ? FontWeight.w700 : FontWeight.w500,
                      ),
                    ),
                    backgroundColor: const Color(0xFFF8FAFC),
                    side: const BorderSide(color: Color(0xFFE2E8F0)),
                  );
                }).toList(),
              ),
              const SizedBox(height: 16),

              // Network Layer Analysis Card (for Jarkom Report!)
              Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: protoColor.withAlpha(15),
                  borderRadius: BorderRadius.circular(14),
                  border: Border.all(color: protoColor.withAlpha(60)),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Icon(
                          isTcp ? Icons.lock_outline : Icons.bolt_outlined,
                          size: 16,
                          color: protoColor,
                        ),
                        const SizedBox(width: 6),
                        Text(
                          'Karakteristik Transport: ${widget.currentProto.name.toUpperCase()}',
                          style: TextStyle(
                            fontSize: 13,
                            fontWeight: FontWeight.w700,
                            color: isTcp ? const Color(0xFF047857) : const Color(0xFFB45309),
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    _buildSpecRow('Header Overhead', isTcp ? '20 Bytes (Fixed, up to 60B)' : '8 Bytes (Fixed)'),
                    _buildSpecRow('Connection Setup', isTcp ? '3-Way Handshake (SYN, SYN-ACK, ACK)' : 'Connectionless (Direct Transmit)'),
                    _buildSpecRow('Framing Data', isTcp ? r'Byte-Stream dengan Delimiter \n' : 'Datagram Diskrit (Maks 65.507B)'),
                    _buildSpecRow('Keamanan', '⚠️ Plaintext / Unencrypted (Dapat disniff di Wireshark)'),
                  ],
                ),
              ),
              const SizedBox(height: 16),

              // Quick Actions
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      icon: const Icon(Icons.flash_on, size: 16),
                      label: const Text('Burst 10 Pkt'),
                      onPressed: () {
                        Navigator.pop(ctx);
                        _sendBurstTest();
                      },
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: ElevatedButton.icon(
                      icon: const Icon(Icons.speed, size: 16),
                      label: const Text('Ping RTT'),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF0F172A),
                        foregroundColor: Colors.white,
                      ),
                      onPressed: () {
                        Navigator.pop(ctx);
                        widget.onPing();
                      },
                    ),
                  ),
                ],
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildSpecRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 110,
            child: Text(
              '$label:',
              style: const TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w600,
                color: Color(0xFF475569),
              ),
            ),
          ),
          Expanded(
            child: Text(
              value,
              style: const TextStyle(
                fontSize: 11,
                color: Color(0xFF0F172A),
                fontWeight: FontWeight.w500,
              ),
            ),
          ),
        ],
      ),
    );
  }

  // ==========================================
  // VOICE NOTE RECORDING & PLAYBACK LOGIC
  // ==========================================

  void _startVoiceRecording() {
    setState(() {
      _isRecordingVoice = true;
      _recordSeconds = 0;
      _liveWaveform.clear();
    });

    _recordTimer = Timer.periodic(const Duration(milliseconds: 300), (timer) {
      if (!mounted) return;
      setState(() {
        _recordSeconds = (timer.tick * 0.3).toInt();
        _liveWaveform.add(0.2 + Random().nextDouble() * 0.8);
        if (_liveWaveform.length > 20) {
          _liveWaveform.removeAt(0);
        }
      });
    });
  }

  void _cancelVoiceRecording() {
    _recordTimer?.cancel();
    setState(() {
      _isRecordingVoice = false;
      _recordSeconds = 0;
      _liveWaveform.clear();
    });
  }

  void _finishVoiceRecording() {
    _recordTimer?.cancel();
    final duration = max(1, _recordSeconds);
    // Generate normalized waveform points for wire framing
    final samples = List.generate(15, (_) => (Random().nextInt(8) + 2).toString()).join(',');
    final vnPayload = '[VOICE:$duration:$samples]';

    setState(() {
      _isRecordingVoice = false;
      _recordSeconds = 0;
      _liveWaveform.clear();
    });

    _dispatchWirePayload(vnPayload, vnPayload);
  }

  void _toggleAudioPlayback(int seq, int durationSec) {
    if (_playingSeq == seq) {
      // Pause
      _playbackTimer?.cancel();
      setState(() {
        _playingSeq = null;
        _playbackRatio = 0.0;
      });
      return;
    }

    _playbackTimer?.cancel();
    setState(() {
      _playingSeq = seq;
      _playbackRatio = 0.0;
    });

    const stepMs = 50;
    final totalSteps = (durationSec * 1000) ~/ stepMs;
    int currentStep = 0;

    _playbackTimer = Timer.periodic(const Duration(milliseconds: stepMs), (t) {
      if (!mounted) {
        t.cancel();
        return;
      }
      currentStep++;
      if (currentStep >= totalSteps) {
        t.cancel();
        setState(() {
          _playingSeq = null;
          _playbackRatio = 0.0;
        });
      } else {
        setState(() {
          _playbackRatio = currentStep / totalSteps;
        });
      }
    });
  }

  // ==========================================
  // IMAGE ATTACHMENT MODAL
  // ==========================================

  void _showImagePickerModal() {
    final List<Map<String, String>> presets = [
      {
        'title': 'Diagram Handshake TCP',
        'desc': '3-Way Handshake SYN, SYN-ACK, ACK',
        'icon': '🤝',
        'tag': 'tcp_handshake',
        'color': '0xFF10B981',
      },
      {
        'title': 'Capture Wireshark',
        'desc': 'Lalu lintas paket TCP vs UDP',
        'icon': '🦈',
        'tag': 'wireshark_capture',
        'color': '0xFF0284C7',
      },
      {
        'title': 'Topologi Jaringan LAN',
        'desc': 'Client, Switch, dan Server DualChat',
        'icon': '🖥️',
        'tag': 'network_topology',
        'color': '0xFF8B5CF6',
      },
      {
        'title': 'Server Datagram UDP',
        'desc': 'Transmisi cepat connectionless 8-byte header',
        'icon': '⚡',
        'tag': 'udp_datagram',
        'color': '0xFFF59E0B',
      },
      {
        'title': 'Meme Jarkom Lab',
        'desc': 'When UDP doesn\'t care if you received it',
        'icon': '📦',
        'tag': 'jarkom_meme',
        'color': '0xFFEC4899',
      },
    ];

    final captionController = TextEditingController();

    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: Colors.transparent,
      builder: (ctx) {
        return Container(
          decoration: const BoxDecoration(
            color: Colors.white,
            borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
          ),
          padding: EdgeInsets.fromLTRB(
              20, 14, 20, MediaQuery.of(ctx).viewInsets.bottom + 24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Center(
                child: Container(
                  width: 36,
                  height: 4,
                  decoration: BoxDecoration(
                    color: const Color(0xFFCBD5E1),
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              const SizedBox(height: 16),
              const Text(
                'Kirim Gambar / Diagram',
                style: TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w700,
                  color: Color(0xFF0F172A),
                ),
              ),
              const SizedBox(height: 4),
              const Text(
                'Pilih media atau diagram jaringan untuk dikirim via socket:',
                style: TextStyle(fontSize: 12, color: Color(0xFF64748B)),
              ),
              const SizedBox(height: 14),

              TextField(
                controller: captionController,
                decoration: InputDecoration(
                  hintText: 'Tulis keterangan/caption gambar (opsional)...',
                  isDense: true,
                  filled: true,
                  fillColor: const Color(0xFFF8FAFC),
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                    borderSide: const BorderSide(color: Color(0xFFE2E8F0)),
                  ),
                ),
              ),
              const SizedBox(height: 14),

              // Local File Actions
              Row(
                children: [
                  Expanded(
                    child: InkWell(
                      borderRadius: BorderRadius.circular(12),
                      onTap: () async {
                        try {
                          final picker = ImagePicker();
                          final picked = await picker.pickImage(
                            source: ImageSource.gallery,
                            maxWidth: 240,
                            maxHeight: 240,
                            imageQuality: 40,
                          );
                          if (picked != null) {
                            final bytes = await picked.readAsBytes();
                            final b64 = base64Encode(bytes);
                            final caption = captionController.text.trim();
                            final safeName = picked.name.replaceAll(':', '_');
                            final payload = '[IMG_DATA:$safeName:$b64:$caption]';
                            if (ctx.mounted) Navigator.pop(ctx);
                            _dispatchWirePayload(payload, payload);
                          }
                        } catch (e) {
                          if (mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(content: Text('Gagal mengambil gambar: $e')),
                            );
                          }
                        }
                      },
                      child: Container(
                        padding: const EdgeInsets.symmetric(vertical: 12),
                        decoration: BoxDecoration(
                          color: const Color(0xFF10B981).withAlpha(20),
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(color: const Color(0xFF10B981).withAlpha(80)),
                        ),
                        child: const Column(
                          children: [
                            Icon(Icons.photo_library_rounded, color: Color(0xFF10B981), size: 24),
                            SizedBox(height: 4),
                            Text('Galeri / File Lokal', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: Color(0xFF0F172A))),
                          ],
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    child: InkWell(
                      borderRadius: BorderRadius.circular(12),
                      onTap: () async {
                        try {
                          final picker = ImagePicker();
                          final picked = await picker.pickImage(
                            source: ImageSource.camera,
                            maxWidth: 240,
                            maxHeight: 240,
                            imageQuality: 40,
                          );
                          if (picked != null) {
                            final bytes = await picked.readAsBytes();
                            final b64 = base64Encode(bytes);
                            final caption = captionController.text.trim();
                            final safeName = picked.name.replaceAll(':', '_');
                            final payload = '[IMG_DATA:$safeName:$b64:$caption]';
                            if (ctx.mounted) Navigator.pop(ctx);
                            _dispatchWirePayload(payload, payload);
                          }
                        } catch (e) {
                          if (mounted) {
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(content: Text('Gagal mengambil kamera: $e')),
                            );
                          }
                        }
                      },
                      child: Container(
                        padding: const EdgeInsets.symmetric(vertical: 12),
                        decoration: BoxDecoration(
                          color: const Color(0xFF0284C7).withAlpha(20),
                          borderRadius: BorderRadius.circular(12),
                          border: Border.all(color: const Color(0xFF0284C7).withAlpha(80)),
                        ),
                        child: const Column(
                          children: [
                            Icon(Icons.camera_alt_rounded, color: Color(0xFF0284C7), size: 24),
                            SizedBox(height: 4),
                            Text('Foto Kamera', style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: Color(0xFF0F172A))),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 14),
              const Text(
                'Atau Pilih Diagram Jaringan Preset:',
                style: TextStyle(fontSize: 12, fontWeight: FontWeight.w700, color: Color(0xFF475569)),
              ),
              const SizedBox(height: 8),

              SizedBox(
                height: 160,
                child: ListView.separated(
                  itemCount: presets.length,
                  separatorBuilder: (_, _) => const SizedBox(height: 8),
                  itemBuilder: (context, idx) {
                    final p = presets[idx];
                    return InkWell(
                      borderRadius: BorderRadius.circular(12),
                      onTap: () {
                        final caption = captionController.text.trim();
                        final imgPayload =
                            '[IMAGE:${p['tag']}:${p['title']}:${p['icon']}:$caption]';
                        Navigator.pop(ctx);
                        _dispatchWirePayload(imgPayload, imgPayload);
                      },
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 12, vertical: 8),
                        decoration: BoxDecoration(
                          border: Border.all(color: const Color(0xFFE2E8F0)),
                          borderRadius: BorderRadius.circular(12),
                          color: const Color(0xFFFAFAFA),
                        ),
                        child: Row(
                          children: [
                            Container(
                              width: 40,
                              height: 40,
                              decoration: BoxDecoration(
                                color: Color(int.parse(p['color']!)).withAlpha(25),
                                borderRadius: BorderRadius.circular(10),
                              ),
                              alignment: Alignment.center,
                              child: Text(
                                p['icon']!,
                                style: const TextStyle(fontSize: 20),
                              ),
                            ),
                            const SizedBox(width: 12),
                            Expanded(
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                    p['title']!,
                                    style: const TextStyle(
                                      fontSize: 13,
                                      fontWeight: FontWeight.w700,
                                      color: Color(0xFF0F172A),
                                    ),
                                  ),
                                  Text(
                                    p['desc']!,
                                    style: const TextStyle(
                                      fontSize: 11,
                                      color: Color(0xFF64748B),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            const Icon(
                              Icons.send_rounded,
                              size: 16,
                              color: Color(0xFF0F172A),
                            ),
                          ],
                        ),
                      ),
                    );
                  },
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  Color _getUserColor(String username) {
    if (username == 'server' || username == 'system') {
      return const Color(0xFF0284C7);
    }
    final hash = username.codeUnits.fold(0, (acc, c) => acc + c);
    const palette = [
      Color(0xFF0284C7),
      Color(0xFF7C3AED),
      Color(0xFF10B981),
      Color(0xFFDB2777),
      Color(0xFFF59E0B),
      Color(0xFF2563EB),
      Color(0xFF0D9488),
    ];
    return palette[hash % palette.length];
  }

  @override
  Widget build(BuildContext context) {
    final isTcp = _activeProto == TransportProto.tcp;
    final protoColor = isTcp ? const Color(0xFF10B981) : const Color(0xFFF59E0B);
    final isConnected = _connStatus == ConnectionStatus.connected;

    return Scaffold(
      backgroundColor: const Color(0xFFF8FAFC),
      appBar: AppBar(
        backgroundColor: Colors.white,
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        titleSpacing: 0,
        leading: IconButton(
          icon: const Icon(Icons.arrow_back_ios_new,
              size: 20, color: Color(0xFF0F172A)),
          onPressed: () => Navigator.of(context).pop(),
        ),
        title: InkWell(
          borderRadius: BorderRadius.circular(12),
          onTap: _showRoomInfoSheet,
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Row(
              children: [
                Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(
                    color: widget.room.avatarColor.withAlpha(28),
                    shape: BoxShape.circle,
                    border: Border.all(
                      color: widget.room.avatarColor.withAlpha(80),
                      width: 1.5,
                    ),
                  ),
                  alignment: Alignment.center,
                  child: Text(
                    widget.room.avatarEmoji,
                    style: const TextStyle(fontSize: 20),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Flexible(
                            child: Text(
                              widget.room.name,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(
                                fontSize: 16,
                                fontWeight: FontWeight.w700,
                                color: Color(0xFF0F172A),
                                letterSpacing: -0.2,
                              ),
                            ),
                          ),
                          const SizedBox(width: 4),
                          const Icon(Icons.arrow_drop_down, size: 18, color: Color(0xFF64748B)),
                        ],
                      ),
                      Row(
                        children: [
                          Container(
                            width: 7,
                            height: 7,
                            decoration: BoxDecoration(
                              shape: BoxShape.circle,
                              color: isConnected
                                  ? const Color(0xFF10B981)
                                  : const Color(0xFF94A3B8),
                            ),
                          ),
                          const SizedBox(width: 5),
                          Text(
                            widget.room.isGroup
                                ? '${widget.room.memberCount} members • ${isTcp ? "TCP Mode" : "UDP Mode"}'
                                : widget.room.statusText,
                            style: const TextStyle(
                              fontSize: 11,
                              color: Color(0xFF64748B),
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
        actions: [
          // Compact Protocol Switcher badge (TCP ⇄ UDP)
          Tooltip(
            message: 'Ketuk untuk beralih mode ke ${isTcp ? "UDP" : "TCP"}',
            child: InkWell(
              borderRadius: BorderRadius.circular(16),
              onTap: _toggleProtocol,
              child: Container(
                margin: const EdgeInsets.symmetric(vertical: 12, horizontal: 2),
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: protoColor.withAlpha(24),
                  borderRadius: BorderRadius.circular(16),
                  border: Border.all(color: protoColor.withAlpha(120), width: 1.2),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      isTcp ? Icons.lock : Icons.bolt,
                      size: 12,
                      color: protoColor,
                    ),
                    const SizedBox(width: 3),
                    Text(
                      _activeProto.name.toUpperCase(),
                      style: TextStyle(
                        color: protoColor,
                        fontSize: 11,
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),

          if (widget.room.isGroup)
            IconButton(
              tooltip: 'Ajak Teman ke Grup',
              icon: const Icon(Icons.group_add_outlined, size: 20, color: Color(0xFF0F172A)),
              onPressed: _showGroupInviteDialog,
            ),

          // Quick Ping button
          IconButton(
            tooltip: 'Uji Latency (Ping RTT)',
            icon: const Icon(Icons.speed, color: Color(0xFF0F172A), size: 20),
            onPressed: isConnected ? widget.onPing : null,
          ),

          // More network tools menu (No AppBar overflow)
          PopupMenuButton<String>(
            icon: const Icon(Icons.more_vert, size: 20, color: Color(0xFF0F172A)),
            tooltip: 'Opsi & Alat Jaringan',
            onSelected: (value) {
              if (value == 'info') {
                _showRoomInfoSheet();
              } else if (value == 'burst') {
                if (isConnected && !_isBursting) _sendBurstTest(count: 10);
              }
            },
            itemBuilder: (ctx) => [
              const PopupMenuItem(
                value: 'info',
                child: Row(
                  children: [
                    Icon(Icons.info_outline, size: 18, color: Color(0xFF64748B)),
                    SizedBox(width: 10),
                    Text('Info Jaringan & Anggota'),
                  ],
                ),
              ),
              PopupMenuItem(
                value: 'burst',
                enabled: isConnected && !_isBursting,
                child: const Row(
                  children: [
                    Icon(Icons.flash_on, size: 18, color: Colors.amber),
                    SizedBox(width: 10),
                    Text('Burst 10 Paket (Wireshark)'),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(width: 4),
        ],
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(1),
          child: Container(color: const Color(0xFFE2E8F0), height: 1),
        ),
      ),
      body: Column(
        children: [
          // Framing/Protocol Notice Banner
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            color: protoColor.withAlpha(16),
            child: Row(
              children: [
                Icon(
                  isTcp ? Icons.lock_outline : Icons.bolt_outlined,
                  size: 14,
                  color: protoColor,
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    isTcp
                        ? 'TCP (SOCK_STREAM): Aliran byte berurutan dengan pemisah delimiter \\n'
                        : 'UDP (SOCK_DGRAM): Datagram mandiri tanpa jaminan urutan/pengiriman',
                    style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w500,
                      color: isTcp
                          ? const Color(0xFF065F46)
                          : const Color(0xFF92400E),
                    ),
                  ),
                ),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: isConnected
                        ? const Color(0xFF10B981).withAlpha(30)
                        : const Color(0xFFEF4444).withAlpha(30),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Text(
                    isConnected ? 'ONLINE' : 'OFFLINE',
                    style: TextStyle(
                      fontSize: 9,
                      fontWeight: FontWeight.w800,
                      color: isConnected
                          ? const Color(0xFF059669)
                          : const Color(0xFFDC2626),
                    ),
                  ),
                ),
              ],
            ),
          ),

          // Message List
          Expanded(
            child: widget.room.messages.isEmpty
                ? _buildEmptyState()
                : ListView.builder(
                    controller: _scrollController,
                    padding:
                        const EdgeInsets.symmetric(horizontal: 16, vertical: 16),
                    itemCount: widget.room.messages.length,
                    itemBuilder: (context, index) {
                      final msg = widget.room.messages[index];
                      return _buildMessageBubble(msg, isTcp, protoColor);
                    },
                  ),
          ),

          // Bottom Input Bar (with Image attachment & Voice Note)
          _buildInputBar(protoColor, isConnected),
        ],
      ),
    );
  }

  Widget _buildEmptyState() {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 64,
            height: 64,
            decoration: BoxDecoration(
              color: const Color(0xFFE2E8F0).withAlpha(120),
              shape: BoxShape.circle,
            ),
            alignment: Alignment.center,
            child: Text(
              widget.room.avatarEmoji,
              style: const TextStyle(fontSize: 32),
            ),
          ),
          const SizedBox(height: 12),
          Text(
            widget.room.isGroup
                ? 'Belum ada pesan di grup ini'
                : 'Mulai obrolan bersama ${widget.room.name}',
            style: const TextStyle(
              fontSize: 15,
              fontWeight: FontWeight.w600,
              color: Color(0xFF475569),
            ),
          ),
          const SizedBox(height: 4),
          const Text(
            'Kirim teks, gambar diagram, atau pesan suara',
            style: TextStyle(fontSize: 12, color: Color(0xFF94A3B8)),
          ),
        ],
      ),
    );
  }

  Widget _buildMessageBubble(ChatMessage msg, bool isTcp, Color protoColor) {
    final isSystem = msg.user == 'server' || msg.user == 'system';
    if (isSystem) {
      return Center(
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 8),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
          decoration: BoxDecoration(
            color: const Color(0xFFE2E8F0),
            borderRadius: BorderRadius.circular(16),
          ),
          child: Text(
            msg.data.isNotEmpty ? msg.data : '[Notifikasi Server]',
            style: const TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w500,
              color: Color(0xFF475569),
            ),
          ),
        ),
      );
    }

    final isSelf = msg.isSelf || msg.user == widget.profile.username;
    final timeStr =
        '${msg.dateTime.hour.toString().padLeft(2, '0')}:${msg.dateTime.minute.toString().padLeft(2, '0')}';

    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: Row(
        mainAxisAlignment:
            isSelf ? MainAxisAlignment.end : MainAxisAlignment.start,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          if (!isSelf) ...[
            Container(
              width: 30,
              height: 30,
              decoration: BoxDecoration(
                color: _getUserColor(msg.user).withAlpha(35),
                shape: BoxShape.circle,
              ),
              alignment: Alignment.center,
              child: Text(
                msg.user.isNotEmpty ? msg.user[0].toUpperCase() : '?',
                style: TextStyle(
                  fontSize: 13,
                  fontWeight: FontWeight.w700,
                  color: _getUserColor(msg.user),
                ),
              ),
            ),
            const SizedBox(width: 8),
          ],
          Flexible(
            child: Container(
              constraints: BoxConstraints(
                maxWidth: MediaQuery.of(context).size.width * 0.76,
              ),
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
              decoration: BoxDecoration(
                color: isSelf ? const Color(0xFF0F172A) : Colors.white,
                borderRadius: BorderRadius.only(
                  topLeft: const Radius.circular(18),
                  topRight: const Radius.circular(18),
                  bottomLeft: Radius.circular(isSelf ? 18 : 4),
                  bottomRight: Radius.circular(isSelf ? 4 : 18),
                ),
                border: Border.all(
                  color: isSelf
                      ? const Color(0xFF0F172A)
                      : const Color(0xFFE2E8F0),
                  width: 1,
                ),
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withAlpha(6),
                    blurRadius: 4,
                    offset: const Offset(0, 2),
                  ),
                ],
              ),
              child: Column(
                crossAxisAlignment:
                    isSelf ? CrossAxisAlignment.end : CrossAxisAlignment.start,
                children: [
                  // Sender name in Group chat
                  if (!isSelf && widget.room.isGroup) ...[
                    Text(
                      '~${msg.user}',
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w700,
                        color: _getUserColor(msg.user),
                      ),
                    ),
                    const SizedBox(height: 4),
                  ],

                  // Bubble Content (Image, Voice Note, or Text)
                  _buildBubbleBody(msg, isSelf),

                  // Warning if any
                  if (msg.warning != null) ...[
                    const SizedBox(height: 4),
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 6, vertical: 2),
                      decoration: BoxDecoration(
                        color: const Color(0xFFEF4444).withAlpha(20),
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: Text(
                        '⚠️ ${msg.warning}',
                        style: const TextStyle(
                          fontSize: 10,
                          color: Color(0xFFDC2626),
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                    ),
                  ],

                  const SizedBox(height: 4),
                  // Metadata: Time & Seq
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text(
                        '#${msg.seq}',
                        style: const TextStyle(
                          fontSize: 10,
                          fontWeight: FontWeight.w600,
                          color: Color(0xFF94A3B8),
                        ),
                      ),
                      const SizedBox(width: 6),
                      Text(
                        timeStr,
                        style: const TextStyle(
                          fontSize: 10,
                          color: Color(0xFF94A3B8),
                        ),
                      ),
                      if (isSelf) ...[
                        const SizedBox(width: 4),
                        const Icon(
                          Icons.done_all,
                          size: 13,
                          color: Color(0xFF38BDF8),
                        ),
                      ],
                    ],
                  ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildBubbleBody(ChatMessage msg, bool isSelf) {
    // 1. Check for Voice Note payload: [VOICE:<seconds>:<samples>]
    final vnMatch =
        RegExp(r'^\[VOICE:(\d+):([0-9,]+)\]$').firstMatch(msg.data.trim());
    if (vnMatch != null) {
      final durationSec = int.tryParse(vnMatch.group(1) ?? '1') ?? 1;
      final samples = (vnMatch.group(2) ?? '')
          .split(',')
          .map((s) => double.tryParse(s) ?? 4.0)
          .toList();
      return _buildVoiceNotePlayer(
          msg.seq, durationSec, samples, isSelf);
    }

    // 2. Check for Local File Base64 Image: [IMG_DATA:<filename>:<base64>:<caption?>]
    final imgDataMatch = RegExp(r'^\[IMG_DATA:([^:]+):([^:]+):?(.*)\]$')
        .firstMatch(msg.data.trim());
    if (imgDataMatch != null) {
      final fileName = imgDataMatch.group(1) ?? 'file.jpg';
      final b64 = imgDataMatch.group(2) ?? '';
      final caption = imgDataMatch.group(3) ?? '';
      return _buildLocalImageCard(fileName, b64, caption, isSelf, msg.seq);
    }

    // 3. Check for Image preset payload: [IMAGE:<tag>:<title>:<icon>:<caption?>]
    final imgMatch = RegExp(r'^\[IMAGE:([^:]+):([^:]+):([^:]+):?(.*)\]$')
        .firstMatch(msg.data.trim());
    if (imgMatch != null) {
      final tag = imgMatch.group(1) ?? 'image';
      final title = imgMatch.group(2) ?? 'Gambar';
      final icon = imgMatch.group(3) ?? '🖼️';
      final caption = imgMatch.group(4) ?? '';
      return _buildImageCard(tag, title, icon, caption, isSelf, msg.seq);
    }

    // 3. Standard Text
    return Text(
      msg.data,
      style: TextStyle(
        fontSize: 14,
        color: isSelf ? Colors.white : const Color(0xFF0F172A),
        height: 1.35,
      ),
    );
  }

  Widget _buildVoiceNotePlayer(
      int seq, int durationSec, List<double> samples, bool isSelf) {
    final isPlaying = _playingSeq == seq;

    return Container(
      width: 220,
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              // Play/Pause button
              GestureDetector(
                onTap: () => _toggleAudioPlayback(seq, durationSec),
                child: Container(
                  width: 38,
                  height: 38,
                  decoration: BoxDecoration(
                    color: isSelf
                        ? Colors.white.withAlpha(40)
                        : const Color(0xFF0F172A),
                    shape: BoxShape.circle,
                  ),
                  alignment: Alignment.center,
                  child: Icon(
                    isPlaying ? Icons.pause_rounded : Icons.play_arrow_rounded,
                    color: Colors.white,
                    size: 20,
                  ),
                ),
              ),
              const SizedBox(width: 10),

              // Waveform bars
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    SizedBox(
                      height: 24,
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.center,
                        children: List.generate(
                          samples.length,
                          (idx) {
                            final barHeight = min(22.0, max(6.0, samples[idx] * 2.2));
                            final progressIdx =
                                (samples.length * _playbackRatio).toInt();
                            final isPassed = isPlaying && idx <= progressIdx;

                            return Expanded(
                              child: Container(
                                margin:
                                    const EdgeInsets.symmetric(horizontal: 1.2),
                                height: barHeight,
                                decoration: BoxDecoration(
                                  color: isPassed
                                      ? (isSelf
                                          ? const Color(0xFF38BDF8)
                                          : const Color(0xFF0284C7))
                                      : (isSelf
                                          ? Colors.white.withAlpha(120)
                                          : const Color(0xFF94A3B8)),
                                  borderRadius: BorderRadius.circular(2),
                                ),
                              ),
                            );
                          },
                        ),
                      ),
                    ),
                    const SizedBox(height: 3),
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                          isPlaying
                              ? '0:${((1 - _playbackRatio) * durationSec).ceil().toString().padLeft(2, '0')}'
                              : '0:${durationSec.toString().padLeft(2, '0')}',
                          style: TextStyle(
                            fontSize: 10,
                            fontWeight: FontWeight.w600,
                            color: isSelf
                                ? Colors.white.withAlpha(190)
                                : const Color(0xFF64748B),
                          ),
                        ),
                        Text(
                          'Pesan Suara',
                          style: TextStyle(
                            fontSize: 9,
                            color: isSelf
                                ? Colors.white.withAlpha(150)
                                : const Color(0xFF94A3B8),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  void _showGroupInviteDialog() {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Colors.white,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        title: Row(
          children: [
            Container(
              padding: const EdgeInsets.all(8),
              decoration: BoxDecoration(
                color: const Color(0xFF6366F1).withAlpha(25),
                shape: BoxShape.circle,
              ),
              child: const Icon(Icons.groups_rounded, color: Color(0xFF6366F1), size: 22),
            ),
            const SizedBox(width: 10),
            const Expanded(
              child: Text(
                'Ajak Teman ke Grup',
                style: TextStyle(fontSize: 16, fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'Agar teman atau HP lain dapat bergabung ke obrolan grup ini:',
              style: TextStyle(fontSize: 12, color: Color(0xFF64748B)),
            ),
            const SizedBox(height: 12),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: const Color(0xFFF8FAFC),
                borderRadius: BorderRadius.circular(12),
                border: Border.all(color: const Color(0xFFE2E8F0)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('Nama Grup Anda:', style: TextStyle(fontSize: 11, color: Color(0xFF94A3B8))),
                  const SizedBox(height: 4),
                  SelectableText(
                    widget.room.name,
                    style: const TextStyle(fontSize: 16, fontWeight: FontWeight.bold, color: Color(0xFF0F172A)),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            const Text(
              '1. Buka aplikasi di HP lain.\n2. Masuk ke tab "Grup".\n3. Klik "Gabung Grup".\n4. Masukkan nama grup di atas dan klik Gabung!',
              style: TextStyle(fontSize: 12, color: Color(0xFF334155), height: 1.4),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Tutup'),
          ),
          ElevatedButton.icon(
            icon: const Icon(Icons.copy, size: 15),
            label: const Text('Salin Nama Grup'),
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFF0F172A),
              foregroundColor: Colors.white,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
            ),
            onPressed: () {
              Clipboard.setData(ClipboardData(text: widget.room.name));
              Navigator.pop(ctx);
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(
                  content: Text('Nama grup "${widget.room.name}" berhasil disalin!'),
                  behavior: SnackBarBehavior.floating,
                ),
              );
            },
          ),
        ],
      ),
    );
  }

  Widget _buildLocalImageCard(
      String fileName, String b64, String caption, bool isSelf, int seq) {
    Uint8List? bytes;
    try {
      bytes = base64Decode(b64);
    } catch (_) {}

    final localBytes = bytes;

    return GestureDetector(
      onTap: () {
        if (localBytes != null) {
          showDialog(
            context: context,
            builder: (ctx) => Dialog(
              backgroundColor: Colors.transparent,
              child: Container(
                padding: const EdgeInsets.all(16),
                decoration: BoxDecoration(
                  color: Colors.white,
                  borderRadius: BorderRadius.circular(20),
                ),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Expanded(
                          child: Text(
                            fileName,
                            style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15),
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                        IconButton(
                          icon: const Icon(Icons.close),
                          onPressed: () => Navigator.pop(ctx),
                        ),
                      ],
                    ),
                    const SizedBox(height: 8),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(12),
                      child: Image.memory(
                        localBytes,
                        fit: BoxFit.contain,
                        height: 320,
                      ),
                    ),
                    if (caption.isNotEmpty) ...[
                      const SizedBox(height: 10),
                      Text(caption, style: const TextStyle(fontSize: 13, color: Color(0xFF334155))),
                    ],
                    const SizedBox(height: 10),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                      decoration: BoxDecoration(
                        color: const Color(0xFFF1F5F9),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Text(
                        'Ukuran: ${(localBytes.length / 1024).toStringAsFixed(1)} KB | Protokol: ${_activeProto.name.toUpperCase()}',
                        style: const TextStyle(fontSize: 11, color: Color(0xFF64748B), fontWeight: FontWeight.w600),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          );
        }
      },
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 220,
            decoration: BoxDecoration(
              color: isSelf ? Colors.white.withAlpha(20) : const Color(0xFFF1F5F9),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: isSelf ? Colors.white.withAlpha(40) : const Color(0xFFE2E8F0),
              ),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (localBytes != null)
                  ClipRRect(
                    borderRadius: const BorderRadius.vertical(top: Radius.circular(12)),
                    child: Image.memory(
                      localBytes,
                      width: 220,
                      height: 140,
                      fit: BoxFit.cover,
                      errorBuilder: (context, error, stackTrace) => const Center(
                        child: Icon(Icons.broken_image, size: 48, color: Colors.grey),
                      ),
                    ),
                  )
                else
                  const Padding(
                    padding: EdgeInsets.all(20),
                    child: Center(child: Icon(Icons.image, size: 48)),
                  ),
                Padding(
                  padding: const EdgeInsets.all(8),
                  child: Row(
                    children: [
                      const Icon(Icons.attach_file, size: 14, color: Color(0xFF64748B)),
                      const SizedBox(width: 4),
                      Expanded(
                        child: Text(
                          fileName,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                            color: isSelf ? Colors.white : const Color(0xFF0F172A),
                          ),
                        ),
                      ),
                      Text(
                        bytes != null ? '${(bytes.length / 1024).toStringAsFixed(1)} KB' : '',
                        style: TextStyle(
                          fontSize: 10,
                          color: isSelf ? Colors.white70 : const Color(0xFF94A3B8),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          if (caption.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              caption,
              style: TextStyle(
                fontSize: 13,
                color: isSelf ? Colors.white : const Color(0xFF0F172A),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildImageCard(String tag, String title, String icon, String caption,
      bool isSelf, int seq) {
    return InkWell(
      borderRadius: BorderRadius.circular(14),
      onTap: () {
        ImagePreviewDialog.show(
          context: context,
          title: title,
          icon: icon,
          caption: caption,
          tag: tag,
          proto: _activeProto,
          seq: seq,
          isSelf: isSelf,
        );
      },
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Container(
            width: 220,
            padding: const EdgeInsets.all(14),
            decoration: BoxDecoration(
              color: isSelf
                  ? Colors.white.withAlpha(20)
                  : const Color(0xFFF1F5F9),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(
                color: isSelf
                    ? Colors.white.withAlpha(40)
                    : const Color(0xFFE2E8F0),
              ),
            ),
            child: Column(
              children: [
                Text(
                  icon,
                  style: const TextStyle(fontSize: 48),
                ),
                const SizedBox(height: 8),
                Text(
                  title,
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    fontSize: 12,
                    fontWeight: FontWeight.w700,
                    color: isSelf ? Colors.white : const Color(0xFF0F172A),
                  ),
                ),
                const SizedBox(height: 4),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: isSelf
                        ? Colors.white.withAlpha(30)
                        : const Color(0xFFE2E8F0),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.fullscreen,
                          size: 11,
                          color: isSelf ? Colors.white70 : const Color(0xFF64748B)),
                      const SizedBox(width: 3),
                      Text(
                        'Ketuk Pratinjau Payload',
                        style: TextStyle(
                          fontSize: 9,
                          color: isSelf ? Colors.white70 : const Color(0xFF64748B),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
          if (caption.isNotEmpty) ...[
            const SizedBox(height: 6),
            Text(
              caption,
              style: TextStyle(
                fontSize: 13,
                color: isSelf ? Colors.white : const Color(0xFF0F172A),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildInputBar(Color protoColor, bool isConnected) {
    // If user is currently holding / recording voice note
    if (_isRecordingVoice) {
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        color: const Color(0xFF0F172A),
        child: SafeArea(
          top: false,
          child: Row(
            children: [
              // Blinking Red Dot
              Container(
                width: 10,
                height: 10,
                decoration: const BoxDecoration(
                  color: Color(0xFFEF4444),
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: 8),
              Text(
                '0:${_recordSeconds.toString().padLeft(2, '0')}',
                style: const TextStyle(
                  color: Colors.white,
                  fontWeight: FontWeight.w700,
                  fontSize: 14,
                ),
              ),
              const SizedBox(width: 14),

              // Live Waveform visualizer
              Expanded(
                child: SizedBox(
                  height: 20,
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: _liveWaveform
                        .map(
                          (val) => Container(
                            width: 3,
                            height: 6 + (val * 14),
                            margin: const EdgeInsets.symmetric(horizontal: 1.5),
                            decoration: BoxDecoration(
                              color: const Color(0xFF38BDF8),
                              borderRadius: BorderRadius.circular(2),
                            ),
                          ),
                        )
                        .toList(),
                  ),
                ),
              ),

              // Cancel button
              IconButton(
                icon: const Icon(Icons.close, color: Colors.white70, size: 20),
                onPressed: _cancelVoiceRecording,
              ),

              // Send VN button
              Container(
                decoration: const BoxDecoration(
                  color: Color(0xFF10B981),
                  shape: BoxShape.circle,
                ),
                child: IconButton(
                  icon: const Icon(Icons.send_rounded,
                      color: Colors.white, size: 18),
                  onPressed: _finishVoiceRecording,
                ),
              ),
            ],
          ),
        ),
      );
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: const BoxDecoration(
        color: Colors.white,
        border: Border(
          top: BorderSide(color: Color(0xFFE2E8F0), width: 1),
        ),
      ),
      child: SafeArea(
        top: false,
        child: Row(
          children: [
            // Attach Image Button
            IconButton(
              tooltip: 'Kirim Gambar',
              icon: const Icon(Icons.add_photo_alternate_outlined,
                  size: 22, color: Color(0xFF0F172A)),
              onPressed: isConnected ? _showImagePickerModal : null,
            ),

            // Quick Ping button
            IconButton(
              tooltip: 'Kirim PING latency',
              icon: const Icon(Icons.bolt, size: 22, color: Color(0xFF0F172A)),
              onPressed: isConnected ? widget.onPing : null,
            ),

            // Text Input Field
            Expanded(
              child: Container(
                decoration: BoxDecoration(
                  color: const Color(0xFFF1F5F9),
                  borderRadius: BorderRadius.circular(24),
                ),
                padding: const EdgeInsets.symmetric(horizontal: 14),
                child: TextField(
                  controller: _inputController,
                  textInputAction: TextInputAction.send,
                  onChanged: (val) => setState(() {}),
                  onSubmitted: (_) => _handleSendText(),
                  style: const TextStyle(
                    fontSize: 14,
                    color: Color(0xFF0F172A),
                  ),
                  decoration: const InputDecoration(
                    hintText: 'Ketik pesan...',
                    hintStyle: TextStyle(
                      color: Color(0xFF94A3B8),
                      fontSize: 14,
                    ),
                    border: InputBorder.none,
                    isDense: true,
                    contentPadding: EdgeInsets.symmetric(vertical: 10),
                  ),
                ),
              ),
            ),
            const SizedBox(width: 6),

            // If text input is empty: Show Microphone (Voice Note) button
            // If text input has content: Show Send button
            if (_inputController.text.trim().isEmpty)
              Container(
                width: 42,
                height: 42,
                decoration: const BoxDecoration(
                  color: Color(0xFF0F172A),
                  shape: BoxShape.circle,
                ),
                child: IconButton(
                  tooltip: 'Rekam Pesan Suara',
                  icon: const Icon(Icons.mic, size: 20, color: Colors.white),
                  onPressed: isConnected ? _startVoiceRecording : null,
                ),
              )
            else
              Container(
                width: 42,
                height: 42,
                decoration: const BoxDecoration(
                  color: Color(0xFF0F172A),
                  shape: BoxShape.circle,
                ),
                child: IconButton(
                  icon: const Icon(Icons.send_rounded,
                      size: 18, color: Colors.white),
                  onPressed: _handleSendText,
                ),
              ),
          ],
        ),
      ),
    );
  }
}
