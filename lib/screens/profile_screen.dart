import 'package:flutter/material.dart';
import '../models/chat_message.dart';
import '../models/user_profile.dart';
import '../services/dualchat_client.dart';
import '../widgets/create_profile_dialog.dart';

class ProfileScreen extends StatelessWidget {
  final UserProfile profile;
  final DualChatClient client;
  final TransportProto currentProto;
  final ConnectionStatus connectionStatus;
  final ValueChanged<UserProfile> onProfileUpdated;
  final VoidCallback onToggleConnection;
  final ValueChanged<TransportProto> onSwitchProtocol;
  final bool isEmbedded;
  final VoidCallback? onBackToChats;

  const ProfileScreen({
    super.key,
    required this.profile,
    required this.client,
    required this.currentProto,
    required this.connectionStatus,
    required this.onProfileUpdated,
    required this.onToggleConnection,
    required this.onSwitchProtocol,
    this.isEmbedded = false,
    this.onBackToChats,
  });

  @override
  Widget build(BuildContext context) {
    final isTcp = currentProto == TransportProto.tcp;
    final isConnected = connectionStatus == ConnectionStatus.connected;
    final isConnecting = connectionStatus == ConnectionStatus.connecting;

    return Scaffold(
      backgroundColor: const Color(0xFFF8FAFC),
      appBar: AppBar(
        automaticallyImplyLeading: !isEmbedded,
        leading: isEmbedded && onBackToChats != null
            ? IconButton(
                tooltip: 'Kembali ke Percakapan',
                icon: const Icon(Icons.arrow_back_rounded, color: Color(0xFF0F172A)),
                onPressed: onBackToChats,
              )
            : null,
        backgroundColor: Colors.white,
        elevation: 0,
        surfaceTintColor: Colors.transparent,
        title: const Text(
          'Profil Pengguna',
          style: TextStyle(
            fontSize: 20,
            fontWeight: FontWeight.w700,
            color: Color(0xFF0F172A),
            letterSpacing: -0.3,
          ),
        ),
        actions: [
          TextButton.icon(
            icon: const Icon(Icons.edit_outlined, size: 16),
            label: const Text('Edit'),
            style: TextButton.styleFrom(
              foregroundColor: const Color(0xFF0F172A),
              textStyle: const TextStyle(fontWeight: FontWeight.w600, fontSize: 13),
            ),
            onPressed: () async {
              final updated = await showDialog<UserProfile>(
                context: context,
                builder: (context) => CreateProfileDialog(
                  initialProfile: profile,
                  isInitialSetup: false,
                ),
              );
              if (updated != null) {
                onProfileUpdated(updated);
              }
            },
          ),
          const SizedBox(width: 8),
        ],
        bottom: PreferredSize(
          preferredSize: const Size.fromHeight(1),
          child: Container(color: const Color(0xFFE2E8F0), height: 1),
        ),
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // User Card
            Container(
              padding: const EdgeInsets.all(20),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(20),
                border: Border.all(color: const Color(0xFFE2E8F0)),
                boxShadow: [
                  BoxShadow(
                    color: Colors.black.withAlpha(5),
                    blurRadius: 8,
                    offset: const Offset(0, 2),
                  ),
                ],
              ),
              child: Row(
                children: [
                  Container(
                    width: 68,
                    height: 68,
                    decoration: BoxDecoration(
                      color: const Color(0xFF0F172A),
                      borderRadius: BorderRadius.circular(20),
                    ),
                    alignment: Alignment.center,
                    child: Text(
                      UserProfile.avatarPresets[
                          profile.avatarIndex % UserProfile.avatarPresets.length],
                      style: const TextStyle(fontSize: 34),
                    ),
                  ),
                  const SizedBox(width: 16),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          profile.name,
                          style: const TextStyle(
                            fontSize: 18,
                            fontWeight: FontWeight.w700,
                            color: Color(0xFF0F172A),
                          ),
                        ),
                        const SizedBox(height: 2),
                        Text(
                          '@${profile.username}',
                          style: const TextStyle(
                            fontSize: 13,
                            color: Color(0xFF64748B),
                            fontWeight: FontWeight.w500,
                          ),
                        ),
                        const SizedBox(height: 8),
                        Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 8, vertical: 3),
                          decoration: BoxDecoration(
                            color: const Color(0xFFF1F5F9),
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Text(
                            profile.status,
                            style: const TextStyle(
                              fontSize: 11,
                              color: Color(0xFF475569),
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Mode Transport Selector (TCP vs UDP)
            const Text(
              'Mode Transport Layer (Jaringan)',
              style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.w700,
                color: Color(0xFF334155),
              ),
            ),
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(20),
                border: Border.all(color: const Color(0xFFE2E8F0)),
              ),
              child: Column(
                children: [
                  Row(
                    children: [
                      Expanded(
                        child: GestureDetector(
                          onTap: () => onSwitchProtocol(TransportProto.tcp),
                          child: Container(
                            padding: const EdgeInsets.symmetric(vertical: 12),
                            decoration: BoxDecoration(
                              color: isTcp
                                  ? const Color(0xFF10B981).withAlpha(20)
                                  : const Color(0xFFF8FAFC),
                              borderRadius: BorderRadius.circular(12),
                              border: Border.all(
                                color: isTcp
                                    ? const Color(0xFF10B981)
                                    : const Color(0xFFE2E8F0),
                                width: isTcp ? 1.5 : 1,
                              ),
                            ),
                            child: Column(
                              children: [
                                Icon(
                                  Icons.lock_outline,
                                  color: isTcp
                                      ? const Color(0xFF10B981)
                                      : const Color(0xFF64748B),
                                  size: 20,
                                ),
                                const SizedBox(height: 4),
                                Text(
                                  'TCP Mode',
                                  style: TextStyle(
                                    fontSize: 13,
                                    fontWeight: FontWeight.w700,
                                    color: isTcp
                                        ? const Color(0xFF047857)
                                        : const Color(0xFF475569),
                                  ),
                                ),
                                Text(
                                  'Port ${profile.tcpPort} • Reliable',
                                  style: const TextStyle(
                                    fontSize: 10,
                                    color: Color(0xFF94A3B8),
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: GestureDetector(
                          onTap: () => onSwitchProtocol(TransportProto.udp),
                          child: Container(
                            padding: const EdgeInsets.symmetric(vertical: 12),
                            decoration: BoxDecoration(
                              color: !isTcp
                                  ? const Color(0xFFF59E0B).withAlpha(20)
                                  : const Color(0xFFF8FAFC),
                              borderRadius: BorderRadius.circular(12),
                              border: Border.all(
                                color: !isTcp
                                    ? const Color(0xFFF59E0B)
                                    : const Color(0xFFE2E8F0),
                                width: !isTcp ? 1.5 : 1,
                              ),
                            ),
                            child: Column(
                              children: [
                                Icon(
                                  Icons.bolt_outlined,
                                  color: !isTcp
                                      ? const Color(0xFFF59E0B)
                                      : const Color(0xFF64748B),
                                  size: 20,
                                ),
                                const SizedBox(height: 4),
                                Text(
                                  'UDP Mode',
                                  style: TextStyle(
                                    fontSize: 13,
                                    fontWeight: FontWeight.w700,
                                    color: !isTcp
                                        ? const Color(0xFFB45309)
                                        : const Color(0xFF475569),
                                  ),
                                ),
                                Text(
                                  'Port ${profile.udpPort} • Fast',
                                  style: const TextStyle(
                                    fontSize: 10,
                                    color: Color(0xFF94A3B8),
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 14),
                  // Connection button
                  SizedBox(
                    width: double.infinity,
                    height: 44,
                    child: ElevatedButton.icon(
                      icon: Icon(
                        isConnected ? Icons.link_off : Icons.link,
                        size: 18,
                      ),
                      label: Text(
                        isConnected
                            ? 'Putuskan Koneksi (${currentProto.name.toUpperCase()})'
                            : isConnecting
                                ? 'Menghubungkan...'
                                : 'Hubungkan ke ${profile.host}:${isTcp ? profile.tcpPort : profile.udpPort}',
                        style: const TextStyle(
                          fontSize: 13,
                          fontWeight: FontWeight.w700,
                        ),
                      ),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: isConnected
                            ? const Color(0xFFEF4444)
                            : const Color(0xFF0F172A),
                        foregroundColor: Colors.white,
                        elevation: 0,
                        shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(12),
                        ),
                      ),
                      onPressed: isConnecting ? null : onToggleConnection,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Live Telemetry / Metrics
            const Text(
              'Statistik Jaringan (Live Telemetry)',
              style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.w700,
                color: Color(0xFF334155),
              ),
            ),
            const SizedBox(height: 8),
            Container(
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: Colors.white,
                borderRadius: BorderRadius.circular(20),
                border: Border.all(color: const Color(0xFFE2E8F0)),
              ),
              child: Column(
                children: [
                  Row(
                    children: [
                      _buildMetricItem(
                        'Paket Kirim',
                        '${client.metrics.packetsSent}',
                        const Color(0xFF0284C7),
                      ),
                      _buildMetricItem(
                        'Paket Terima',
                        '${client.metrics.packetsReceived}',
                        const Color(0xFF10B981),
                      ),
                      _buildMetricItem(
                        'Loss/Out-of-Order',
                        '${client.metrics.outOfOrderCount}',
                        const Color(0xFFEF4444),
                      ),
                      _buildMetricItem(
                        'Avg RTT',
                        '${client.metrics.avgRttMs.toStringAsFixed(1)} ms',
                        const Color(0xFF8B5CF6),
                      ),
                    ],
                  ),
                  const SizedBox(height: 12),
                  const Divider(height: 1, color: Color(0xFFF1F5F9)),
                  const SizedBox(height: 12),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(
                        'Total Data Terkirim: ${client.metrics.bytesSent} B',
                        style: const TextStyle(fontSize: 11, color: Color(0xFF64748B)),
                      ),
                      Text(
                        'Total Data Diterima: ${client.metrics.bytesReceived} B',
                        style: const TextStyle(fontSize: 11, color: Color(0xFF64748B)),
                      ),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: 20),

            // Analisis Transport Layer & Header Overhead (Tugas Jarkom / Wireshark)
            const Text(
              'Analisis Transport Layer & Overhead',
              style: TextStyle(
                fontSize: 14,
                fontWeight: FontWeight.w700,
                color: Color(0xFF334155),
              ),
            ),
            const SizedBox(height: 8),
            _buildOverheadCard(isTcp),
          ],
        ),
      ),
    );
  }

  Widget _buildOverheadCard(bool isTcp) {
    final headerSize = isTcp ? 20 : 8; // TCP min 20B, UDP fixed 8B
    final totalHeaderSent = client.metrics.packetsSent * headerSize;
    final totalWireBytes = client.metrics.bytesSent + totalHeaderSent;
    final overheadPercent = totalWireBytes > 0
        ? ((totalHeaderSent / totalWireBytes) * 100).toStringAsFixed(1)
        : '0.0';

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(20),
        border: Border.all(color: const Color(0xFFE2E8F0)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.all(6),
                decoration: BoxDecoration(
                  color: isTcp
                      ? const Color(0xFF10B981).withAlpha(25)
                      : const Color(0xFFF59E0B).withAlpha(25),
                  shape: BoxShape.circle,
                ),
                child: Icon(
                  isTcp ? Icons.shield_outlined : Icons.speed_outlined,
                  size: 18,
                  color: isTcp ? const Color(0xFF047857) : const Color(0xFFB45309),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  isTcp
                      ? 'TCP Header: 20 Bytes (Overhead: $overheadPercent%)'
                      : 'UDP Header: 8 Bytes (Overhead: $overheadPercent%)',
                  style: const TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                    color: Color(0xFF0F172A),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          // Comparison Table
          Container(
            decoration: BoxDecoration(
              color: const Color(0xFFF8FAFC),
              borderRadius: BorderRadius.circular(12),
              border: Border.all(color: const Color(0xFFE2E8F0)),
            ),
            child: Table(
              columnWidths: const {
                0: FlexColumnWidth(2.0),
                1: FlexColumnWidth(2.2),
                2: FlexColumnWidth(2.2),
              },
              children: [
                const TableRow(
                  decoration: BoxDecoration(
                    border: Border(bottom: BorderSide(color: Color(0xFFE2E8F0))),
                  ),
                  children: [
                    Padding(
                      padding: EdgeInsets.symmetric(vertical: 8, horizontal: 8),
                      child: Text('Parameter', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: Color(0xFF64748B))),
                    ),
                    Padding(
                      padding: EdgeInsets.symmetric(vertical: 8, horizontal: 8),
                      child: Text('TCP (Port 5000)', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: Color(0xFF047857))),
                    ),
                    Padding(
                      padding: EdgeInsets.symmetric(vertical: 8, horizontal: 8),
                      child: Text('UDP (Port 5001)', style: TextStyle(fontSize: 11, fontWeight: FontWeight.w700, color: Color(0xFFB45309))),
                    ),
                  ],
                ),
                _buildTableRow('Header Size', '20 Bytes', '8 Bytes'),
                _buildTableRow('Handshake', '3-Way (SYN/ACK)', 'None (0-RTT)'),
                _buildTableRow('Reliability', 'Ack & Retransmit', 'Best Effort'),
                _buildTableRow('Framing', r'Stream (\n split)', 'Datagram boundary'),
              ],
            ),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              const Icon(Icons.info_outline, size: 14, color: Color(0xFF64748B)),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  'Estimasi overhead header aktif terkirim: $totalHeaderSent Bytes dari $totalWireBytes Bytes di wire layer.',
                  style: const TextStyle(fontSize: 11, color: Color(0xFF64748B)),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }

  TableRow _buildTableRow(String p, String tcp, String udp) {
    return TableRow(
      children: [
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 8),
          child: Text(p, style: const TextStyle(fontSize: 11, color: Color(0xFF475569), fontWeight: FontWeight.w600)),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 8),
          child: Text(tcp, style: const TextStyle(fontSize: 11, color: Color(0xFF047857))),
        ),
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 8),
          child: Text(udp, style: const TextStyle(fontSize: 11, color: Color(0xFFB45309))),
        ),
      ],
    );
  }

  Widget _buildMetricItem(String label, String value, Color color) {
    return Expanded(
      child: Column(
        children: [
          Text(
            value,
            style: TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.w800,
              color: color,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            label,
            textAlign: TextAlign.center,
            style: const TextStyle(
              fontSize: 10,
              color: Color(0xFF64748B),
              fontWeight: FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }
}
