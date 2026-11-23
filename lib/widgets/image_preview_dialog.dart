import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../models/chat_message.dart';

class ImagePreviewDialog extends StatelessWidget {
  final String title;
  final String icon;
  final String caption;
  final String tag;
  final TransportProto proto;
  final int? seq;
  final bool isSelf;

  const ImagePreviewDialog({
    super.key,
    required this.title,
    required this.icon,
    required this.caption,
    required this.tag,
    required this.proto,
    this.seq,
    this.isSelf = false,
  });

  static Future<void> show({
    required BuildContext context,
    required String title,
    required String icon,
    required String caption,
    required String tag,
    required TransportProto proto,
    int? seq,
    bool isSelf = false,
  }) {
    return showDialog(
      context: context,
      barrierDismissible: true,
      builder: (ctx) => ImagePreviewDialog(
        title: title,
        icon: icon,
        caption: caption,
        tag: tag,
        proto: proto,
        seq: seq,
        isSelf: isSelf,
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final isTcp = proto == TransportProto.tcp;
    final protoColor = isTcp ? const Color(0xFF10B981) : const Color(0xFFF59E0B);
    final wirePayload = '[IMAGE:$tag:$title:$icon:$caption]';
    final payloadBytes = wirePayload.length;
    final port = isTcp ? 5000 : 5001;
    final filterText = isTcp ? 'tcp.port == 5000' : 'udp.port == 5001';

    return Dialog(
      backgroundColor: Colors.transparent,
      insetPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 24),
      child: Container(
        constraints: const BoxConstraints(maxWidth: 440),
        decoration: BoxDecoration(
          color: Colors.white,
          borderRadius: BorderRadius.circular(24),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withAlpha(40),
              blurRadius: 24,
              offset: const Offset(0, 8),
            ),
          ],
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            // Top Bar with Close Button & Title
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 16, 12, 12),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Row(
                    children: [
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 8, vertical: 4),
                        decoration: BoxDecoration(
                          color: protoColor.withAlpha(24),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: protoColor.withAlpha(90)),
                        ),
                        child: Text(
                          proto.name.toUpperCase(),
                          style: TextStyle(
                            fontSize: 10,
                            fontWeight: FontWeight.w800,
                            color: protoColor,
                            letterSpacing: 0.5,
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      const Text(
                        'Payload Preview',
                        style: TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.w700,
                          color: Color(0xFF0F172A),
                        ),
                      ),
                    ],
                  ),
                  IconButton(
                    icon: const Icon(Icons.close_rounded,
                        size: 20, color: Color(0xFF64748B)),
                    onPressed: () => Navigator.of(context).pop(),
                  ),
                ],
              ),
            ),
            const Divider(height: 1, color: Color(0xFFF1F5F9)),

            // Image Graphic Canvas
            Container(
              margin: const EdgeInsets.all(16),
              padding: const EdgeInsets.symmetric(vertical: 28, horizontal: 20),
              decoration: BoxDecoration(
                color: const Color(0xFFF8FAFC),
                borderRadius: BorderRadius.circular(16),
                border: Border.all(color: const Color(0xFFE2E8F0)),
              ),
              child: Column(
                children: [
                  Text(
                    icon,
                    style: const TextStyle(fontSize: 72),
                  ),
                  const SizedBox(height: 12),
                  Text(
                    title,
                    textAlign: TextAlign.center,
                    style: const TextStyle(
                      fontSize: 17,
                      fontWeight: FontWeight.w800,
                      color: Color(0xFF0F172A),
                    ),
                  ),
                  if (caption.isNotEmpty) ...[
                    const SizedBox(height: 6),
                    Text(
                      caption,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                        fontSize: 13,
                        color: Color(0xFF475569),
                        height: 1.4,
                      ),
                    ),
                  ],
                ],
              ),
            ),

            // Network Payload Analysis Card
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Container(
                padding: const EdgeInsets.all(14),
                decoration: BoxDecoration(
                  color: const Color(0xFF0F172A),
                  borderRadius: BorderRadius.circular(14),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        const Row(
                          children: [
                            Icon(Icons.terminal,
                                size: 14, color: Color(0xFF38BDF8)),
                            SizedBox(width: 6),
                            Text(
                              'TELEMETRI JARKOM (WIRESHARK)',
                              style: TextStyle(
                                fontSize: 10,
                                fontWeight: FontWeight.w800,
                                color: Color(0xFF38BDF8),
                                letterSpacing: 0.6,
                              ),
                            ),
                          ],
                        ),
                        Text(
                          'Port $port',
                          style: const TextStyle(
                            fontSize: 10,
                            fontWeight: FontWeight.w700,
                            color: Colors.white70,
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    _buildMetaRow('Wire Framing', wirePayload, isMonospace: true),
                    _buildMetaRow('Ukuran Data', '$payloadBytes Bytes (Payload Mentah)'),
                    _buildMetaRow(
                      'Lapisan Transport',
                      isTcp
                          ? 'TCP Byte-Stream (Header 20B, Reliable, ACK)'
                          : 'UDP Datagram (Header 8B, Fast, Connectionless)',
                    ),
                    _buildMetaRow(
                      'Wireshark Filter',
                      filterText,
                      isMonospace: true,
                      trailing: InkWell(
                        onTap: () {
                          Clipboard.setData(ClipboardData(text: filterText));
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(
                              content: Text('Filter Wireshark disalin ke clipboard!'),
                              duration: Duration(milliseconds: 1200),
                              behavior: SnackBarBehavior.floating,
                            ),
                          );
                        },
                        child: const Padding(
                          padding: EdgeInsets.only(left: 6),
                          child: Icon(Icons.copy, size: 12, color: Color(0xFF38BDF8)),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),

            // Action Buttons
            Padding(
              padding: const EdgeInsets.all(16),
              child: ElevatedButton(
                style: ElevatedButton.styleFrom(
                  backgroundColor: const Color(0xFF0F172A),
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
                onPressed: () => Navigator.of(context).pop(),
                child: const Text(
                  'Tutup Pratinjau',
                  style: TextStyle(fontSize: 13.5, fontWeight: FontWeight.w700),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildMetaRow(String label, String value,
      {bool isMonospace = false, Widget? trailing}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 105,
            child: Text(
              '$label:',
              style: const TextStyle(
                fontSize: 10.5,
                fontWeight: FontWeight.w600,
                color: Color(0xFF94A3B8),
              ),
            ),
          ),
          Expanded(
            child: Text(
              value,
              maxLines: 2,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                fontSize: 10.5,
                fontWeight: FontWeight.w500,
                color: Colors.white,
                fontFamily: isMonospace ? 'monospace' : null,
              ),
            ),
          ),
          ?trailing,
        ],
      ),
    );
  }
}
