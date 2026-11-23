import 'package:flutter/material.dart';
import '../models/chat_message.dart';
import '../services/dualchat_client.dart';

class FloatingMenuCapsule extends StatelessWidget {
  final TransportProto currentProto;
  final ConnectionStatus status;
  final ValueChanged<TransportProto> onSwitchProto;
  final VoidCallback onPing;
  final VoidCallback onNewChat;
  final VoidCallback onSettings;

  const FloatingMenuCapsule({
    super.key,
    required this.currentProto,
    required this.status,
    required this.onSwitchProto,
    required this.onPing,
    required this.onNewChat,
    required this.onSettings,
  });

  void _showMenuSheet(BuildContext context) {
    final isTcp = currentProto == TransportProto.tcp;
    final isConnected = status == ConnectionStatus.connected;

    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (context) {
        return Material(
          color: Colors.white,
          borderRadius: const BorderRadius.vertical(top: Radius.circular(24)),
          clipBehavior: Clip.antiAlias,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 12, 20, 32),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
              // Handle
              Container(
                width: 38,
                height: 4,
                decoration: BoxDecoration(
                  color: const Color(0xFFCBD5E1),
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              const SizedBox(height: 18),
              const Text(
                'Menu Cepat DualChat',
                style: TextStyle(
                  fontSize: 17,
                  fontWeight: FontWeight.w700,
                  color: Color(0xFF0F172A),
                ),
              ),
              const SizedBox(height: 16),

              // Switch Protocol Tile
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(
                    color: isTcp
                        ? const Color(0xFF10B981).withAlpha(25)
                        : const Color(0xFFF59E0B).withAlpha(25),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  alignment: Alignment.center,
                  child: Icon(
                    isTcp ? Icons.swap_horiz : Icons.swap_horiz,
                    color: isTcp ? const Color(0xFF10B981) : const Color(0xFFF59E0B),
                  ),
                ),
                title: Text(
                  'Ganti ke Mode ${isTcp ? "UDP" : "TCP"}',
                  style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                ),
                subtitle: Text(
                  isTcp
                      ? 'Beralih ke UDP (Port 5001 • Fast & Loss-Tolerant)'
                      : 'Beralih ke TCP (Port 5000 • Reliable & Byte-Stream)',
                  style: const TextStyle(fontSize: 12, color: Color(0xFF64748B)),
                ),
                onTap: () {
                  Navigator.pop(context);
                  onSwitchProto(
                    isTcp ? TransportProto.udp : TransportProto.tcp,
                  );
                },
              ),
              const Divider(height: 1, color: Color(0xFFF1F5F9)),

              // Ping Latency Tile
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(
                    color: const Color(0xFF38BDF8).withAlpha(25),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  alignment: Alignment.center,
                  child: const Icon(Icons.speed, color: Color(0xFF0284C7)),
                ),
                title: const Text(
                  'Uji Latency (Kirim /ping)',
                  style: TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                ),
                subtitle: const Text(
                  'Uji RTT soket jaringan secara langsung',
                  style: TextStyle(fontSize: 12, color: Color(0xFF64748B)),
                ),
                enabled: isConnected,
                onTap: () {
                  Navigator.pop(context);
                  onPing();
                },
              ),
              const Divider(height: 1, color: Color(0xFFF1F5F9)),

              // New Friend / Group
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(
                    color: const Color(0xFF8B5CF6).withAlpha(25),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  alignment: Alignment.center,
                  child: const Icon(Icons.person_add_outlined, color: Color(0xFF7C3AED)),
                ),
                title: const Text(
                  'Tambah Teman / Buat Grup',
                  style: TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                ),
                subtitle: const Text(
                  'Buka percakapan baru dengan teman atau grup',
                  style: TextStyle(fontSize: 12, color: Color(0xFF64748B)),
                ),
                onTap: () {
                  Navigator.pop(context);
                  onNewChat();
                },
              ),
              const Divider(height: 1, color: Color(0xFFF1F5F9)),

              // Settings
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Container(
                  width: 40,
                  height: 40,
                  decoration: BoxDecoration(
                    color: const Color(0xFF64748B).withAlpha(25),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  alignment: Alignment.center,
                  child: const Icon(Icons.settings_outlined, color: Color(0xFF475569)),
                ),
                title: const Text(
                  'Konfigurasi IP & Profil',
                  style: TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
                ),
                subtitle: const Text(
                  'Ubah IP server, port, nama, dan avatar',
                  style: TextStyle(fontSize: 12, color: Color(0xFF64748B)),
                ),
                onTap: () {
                  Navigator.pop(context);
                  onSettings();
                },
              ),
            ],
          ),
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () => _showMenuSheet(context),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 10),
        decoration: BoxDecoration(
          color: const Color(0xFF0F172A),
          borderRadius: BorderRadius.circular(30),
          boxShadow: [
            BoxShadow(
              color: Colors.black.withAlpha(50),
              blurRadius: 16,
              offset: const Offset(0, 6),
            ),
          ],
        ),
        child: const Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              'Menu',
              style: TextStyle(
                color: Colors.white,
                fontWeight: FontWeight.w700,
                fontSize: 13,
                letterSpacing: 0.3,
              ),
            ),
            SizedBox(width: 8),
            Icon(Icons.menu, size: 16, color: Colors.white),
          ],
        ),
      ),
    );
  }
}
