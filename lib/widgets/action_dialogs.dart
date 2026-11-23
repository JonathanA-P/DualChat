import 'package:flutter/material.dart';

/// Contextual dialogs for Friends, Groups, and Feed Posts
/// fulfilling the clean extreme architecture and network transport requirements.

class ActionDialogs {
  // ==========================================
  // 1. DIALOG TAMBAH TEMAN (Obrolan 1-on-1)
  // ==========================================
  static Future<void> showAddFriendDialog({
    required BuildContext context,
    required ValueChanged<String> onAddFriend,
    String? currentUsername,
    List<String> availableFriends = const [],
  }) {
    final controller = TextEditingController();
    String? validationError;

    final candidates = availableFriends
        .where((f) =>
            f.trim().isNotEmpty &&
            f.trim().toLowerCase() != 'server' &&
            f.trim().toLowerCase() != 'system' &&
            !f.trim().startsWith('~') &&
            (currentUsername == null ||
                f.trim().toLowerCase() != currentUsername.toLowerCase()))
        .toSet()
        .toList();

    return showDialog(
      context: context,
      builder: (ctx) {
        return StatefulBuilder(
          builder: (ctx, setDialogState) {
            return AlertDialog(
              backgroundColor: Colors.white,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
              titlePadding: const EdgeInsets.fromLTRB(20, 20, 20, 0),
              contentPadding: const EdgeInsets.fromLTRB(20, 14, 20, 10),
              actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
              title: Row(
                children: [
                  Container(
                    width: 36,
                    height: 36,
                    decoration: BoxDecoration(
                      color: const Color(0xFF10B981).withAlpha(25),
                      shape: BoxShape.circle,
                    ),
                    alignment: Alignment.center,
                    child: const Icon(
                      Icons.person_add_alt_1_rounded,
                      color: Color(0xFF10B981),
                      size: 20,
                    ),
                  ),
                  const SizedBox(width: 10),
                  const Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Tambah Teman',
                          style: TextStyle(
                            fontSize: 17,
                            fontWeight: FontWeight.w800,
                            color: Color(0xFF0F172A),
                          ),
                        ),
                        Text(
                          'Tambah Teman (Obrolan 1-on-1)',
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                            color: Color(0xFF10B981),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              content: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  // Current user identity notice
                  if (currentUsername != null && currentUsername.isNotEmpty)
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                      margin: const EdgeInsets.only(bottom: 8),
                      decoration: BoxDecoration(
                        color: const Color(0xFF10B981).withAlpha(20),
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: const Color(0xFF10B981).withAlpha(60)),
                      ),
                      child: Row(
                        children: [
                          const Icon(Icons.account_circle, size: 14, color: Color(0xFF059669)),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Text(
                              'Anda sedang login sebagai: @$currentUsername',
                              style: const TextStyle(
                                fontSize: 11,
                                fontWeight: FontWeight.w700,
                                color: Color(0xFF065F46),
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),

                  // Notice: Destination Tab
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                    decoration: BoxDecoration(
                      color: const Color(0xFFF1F5F9),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: const Row(
                      children: [
                        Icon(Icons.tab_rounded, size: 14, color: Color(0xFF64748B)),
                        SizedBox(width: 6),
                        Text(
                          'Masuk ke tab: Teman',
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                            color: Color(0xFF475569),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 8),

                  // Transport Path Notice
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                    decoration: BoxDecoration(
                      color: const Color(0xFF0F172A),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: const Row(
                      children: [
                        Icon(Icons.alt_route_rounded,
                            size: 14, color: Color(0xFF38BDF8)),
                        SizedBox(width: 6),
                        Expanded(
                          child: Text(
                            'Jalur: TCP (Port 5000) / UDP (Port 5001)',
                            style: TextStyle(
                              fontSize: 11,
                              fontWeight: FontWeight.w700,
                              color: Color(0xFF38BDF8),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),

                  // Available online candidates chips
                  if (candidates.isNotEmpty) ...[
                    const Text(
                      'Teman Terdeteksi di Jaringan (Tap untuk pilih):',
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w700,
                        color: Color(0xFF334155),
                      ),
                    ),
                    const SizedBox(height: 6),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: candidates.map((fName) {
                        return ActionChip(
                          avatar: const Icon(Icons.person, size: 14, color: Color(0xFF10B981)),
                          label: Text(fName, style: const TextStyle(fontWeight: FontWeight.w600)),
                          backgroundColor: const Color(0xFFF1F5F9),
                          onPressed: () {
                            setDialogState(() {
                              controller.text = fName;
                              validationError = null;
                            });
                          },
                        );
                      }).toList(),
                    ),
                    const SizedBox(height: 10),
                  ],

                  TextField(
                    controller: controller,
                    autofocus: candidates.isEmpty,
                    onChanged: (_) {
                      if (validationError != null) {
                        setDialogState(() => validationError = null);
                      }
                    },
                    decoration: InputDecoration(
                      labelText: 'Username Teman Tujuan',
                      hintText: currentUsername != null && currentUsername.toLowerCase() == 'lian'
                          ? 'mis. salma'
                          : 'mis. lian atau bob',
                      prefixIcon: const Icon(Icons.alternate_email, size: 18),
                      errorText: validationError,
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                      ),
                      contentPadding:
                          const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                    ),
                  ),
                  const SizedBox(height: 6),
                  const Text(
                    'Obrolan privat akan dirouting via paket direct [DM:<user>].',
                    style: TextStyle(fontSize: 10.5, color: Color(0xFF94A3B8)),
                  ),
                ],
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(ctx),
                  child: const Text('Batal'),
                ),
                ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF0F172A),
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                  ),
                  onPressed: () {
                    final text = controller.text.trim();
                    if (text.isEmpty) return;

                    if (currentUsername != null &&
                        text.toLowerCase() == currentUsername.toLowerCase()) {
                      setDialogState(() {
                        validationError = 'Tidak bisa menambahkan diri sendiri (@$currentUsername)!';
                      });
                      return;
                    }

                    Navigator.pop(ctx);
                    onAddFriend(text);
                  },
                  child: const Text('Mulai Chat'),
                ),
              ],
            );
          },
        );
      },
    );
  }

  // ==========================================
  // 2. DIALOG BUAT GRUP (> 2 Anggota)
  // ==========================================
  static Future<void> showCreateGroupDialog({
    required BuildContext context,
    required ValueChanged<String> onCreateGroup,
  }) {
    final controller = TextEditingController();

    return showDialog(
      context: context,
      builder: (ctx) {
        return AlertDialog(
          backgroundColor: Colors.white,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
          titlePadding: const EdgeInsets.fromLTRB(20, 20, 20, 0),
          contentPadding: const EdgeInsets.fromLTRB(20, 14, 20, 10),
          actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          title: Row(
            children: [
              Container(
                width: 36,
                height: 36,
                decoration: BoxDecoration(
                  color: const Color(0xFF6366F1).withAlpha(25),
                  shape: BoxShape.circle,
                ),
                alignment: Alignment.center,
                child: const Icon(
                  Icons.group_add_rounded,
                  color: Color(0xFF6366F1),
                  size: 20,
                ),
              ),
              const SizedBox(width: 10),
              const Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Buat Grup',
                      style: TextStyle(
                        fontSize: 17,
                        fontWeight: FontWeight.w800,
                        color: Color(0xFF0F172A),
                      ),
                    ),
                    Text(
                      'Buat Grup Baru (> 2 Anggota)',
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                        color: Color(0xFF6366F1),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // Notice: Destination Tab
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                decoration: BoxDecoration(
                  color: const Color(0xFFF1F5F9),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: const Row(
                  children: [
                    Icon(Icons.tab_rounded, size: 14, color: Color(0xFF64748B)),
                    SizedBox(width: 6),
                    Text(
                      'Masuk ke tab: Grup',
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                        color: Color(0xFF475569),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 8),

              // Transport Path Notice
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                decoration: BoxDecoration(
                  color: const Color(0xFF0F172A),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: const Row(
                  children: [
                    Icon(Icons.alt_route_rounded,
                        size: 14, color: Color(0xFF38BDF8)),
                    SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        'Jalur: TCP (Port 5000) / UDP (Port 5001)',
                        style: TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.w700,
                          color: Color(0xFF38BDF8),
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 14),

              TextField(
                controller: controller,
                autofocus: true,
                decoration: InputDecoration(
                  labelText: 'Nama Grup Baru',
                  hintText: 'mis. Jarkom Study Club',
                  prefixIcon: const Icon(Icons.groups_rounded, size: 18),
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                ),
              ),
              const SizedBox(height: 6),
              const Text(
                'Pesan grup akan dirouting broadcast via [GROUP:<nama_grup>].',
                style: TextStyle(fontSize: 10.5, color: Color(0xFF94A3B8)),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Batal'),
            ),
            ElevatedButton(
              style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0xFF0F172A),
                foregroundColor: Colors.white,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(10),
                ),
              ),
              onPressed: () {
                final text = controller.text.trim();
                if (text.isEmpty) return;
                Navigator.pop(ctx);
                onCreateGroup(text);
              },
              child: const Text('Buat Grup'),
            ),
          ],
        );
      },
    );
  }

  // ==========================================
  // 3. DIALOG GABUNG GRUP (Join Existing Group)
  // ==========================================
  static Future<void> showJoinGroupDialog({
    required BuildContext context,
    required List<String> availableGroups,
    required ValueChanged<String> onJoinGroup,
  }) {
    final controller = TextEditingController();

    return showDialog(
      context: context,
      builder: (ctx) {
        return StatefulBuilder(
          builder: (ctx, setDialogState) {
            return AlertDialog(
              backgroundColor: Colors.white,
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
              titlePadding: const EdgeInsets.fromLTRB(20, 20, 20, 0),
              contentPadding: const EdgeInsets.fromLTRB(20, 14, 20, 10),
              actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
              title: Row(
                children: [
                  Container(
                    width: 36,
                    height: 36,
                    decoration: BoxDecoration(
                      color: const Color(0xFF0284C7).withAlpha(25),
                      shape: BoxShape.circle,
                    ),
                    alignment: Alignment.center,
                    child: const Icon(
                      Icons.group_add_rounded,
                      color: Color(0xFF0284C7),
                      size: 20,
                    ),
                  ),
                  const SizedBox(width: 10),
                  const Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Gabung ke Grup',
                          style: TextStyle(
                            fontSize: 17,
                            fontWeight: FontWeight.w800,
                            color: Color(0xFF0F172A),
                          ),
                        ),
                        Text(
                          'Masuk ke Grup yang Dibuat Teman / HP Lain',
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                            color: Color(0xFF0284C7),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              content: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                    decoration: BoxDecoration(
                      color: const Color(0xFFF0FDF4),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: const Color(0xFFBBF7D0)),
                    ),
                    child: const Row(
                      children: [
                        Icon(Icons.info_outline, size: 15, color: Color(0xFF16A34A)),
                        SizedBox(width: 6),
                        Expanded(
                          child: Text(
                            'Ketik nama grup yang sama dengan yang dibuat di HP lain untuk mulai mengobrol bersama.',
                            style: TextStyle(
                              fontSize: 11,
                              fontWeight: FontWeight.w500,
                              color: Color(0xFF15803D),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: controller,
                    autofocus: true,
                    decoration: InputDecoration(
                      labelText: 'Nama Grup',
                      hintText: 'mis. Jarkom Study Club',
                      prefixIcon: const Icon(Icons.search, size: 18),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(12),
                      ),
                      contentPadding:
                          const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
                    ),
                  ),
                  if (availableGroups.isNotEmpty) ...[
                    const SizedBox(height: 12),
                    const Text(
                      'Grup yang Terdeteksi di Jaringan:',
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w700,
                        color: Color(0xFF475569),
                      ),
                    ),
                    const SizedBox(height: 6),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: availableGroups.map((gName) {
                        return ActionChip(
                          avatar: const Icon(Icons.groups, size: 14),
                          label: Text(gName),
                          backgroundColor: const Color(0xFFF1F5F9),
                          onPressed: () {
                            setDialogState(() {
                              controller.text = gName;
                            });
                          },
                        );
                      }).toList(),
                    ),
                  ],
                ],
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(ctx),
                  child: const Text('Batal'),
                ),
                ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF0F172A),
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                  ),
                  onPressed: () {
                    final text = controller.text.trim();
                    if (text.isEmpty) return;
                    Navigator.pop(ctx);
                    onJoinGroup(text);
                  },
                  child: const Text('Gabung Sekarang'),
                ),
              ],
            );
          },
        );
      },
    );
  }

  // ==========================================
  // 4. DIALOG TULIS POST (Feed Jaringan)
  // ==========================================
  static Future<void> showCreateFeedPostDialog({
    required BuildContext context,
    required void Function(String text, Map<String, String>? imagePreset)
        onPublishPost,
  }) {
    final controller = TextEditingController();
    Map<String, String>? selectedPreset;

    final presets = [
      {
        'title': 'Diagram Handshake TCP',
        'desc': '3-Way Handshake SYN, SYN-ACK, ACK',
        'icon': '🤝',
        'tag': 'tcp_handshake',
      },
      {
        'title': 'Capture Wireshark',
        'desc': 'Lalu lintas paket TCP vs UDP',
        'icon': '🦈',
        'tag': 'wireshark_capture',
      },
      {
        'title': 'Topologi Jaringan LAN',
        'desc': 'Client, Switch, dan Server DualChat',
        'icon': '🖥️',
        'tag': 'network_topology',
      },
      {
        'title': 'Server Datagram UDP',
        'desc': 'Transmisi cepat connectionless 8-byte header',
        'icon': '⚡',
        'tag': 'udp_datagram',
      },
      {
        'title': 'Meme Jarkom Lab',
        'desc': 'When UDP doesn\'t care if you received it',
        'icon': '📦',
        'tag': 'jarkom_meme',
      },
    ];

    return showDialog(
      context: context,
      builder: (ctx) {
        return StatefulBuilder(
          builder: (ctx, setDialogState) {
            return AlertDialog(
              backgroundColor: Colors.white,
              shape:
                  RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
              titlePadding: const EdgeInsets.fromLTRB(20, 20, 20, 0),
              contentPadding: const EdgeInsets.fromLTRB(20, 14, 20, 10),
              actionsPadding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
              title: Row(
                children: [
                  Container(
                    width: 36,
                    height: 36,
                    decoration: BoxDecoration(
                      color: const Color(0xFF0F172A).withAlpha(15),
                      shape: BoxShape.circle,
                    ),
                    alignment: Alignment.center,
                    child: const Icon(
                      Icons.edit_note_rounded,
                      color: Color(0xFF0F172A),
                      size: 22,
                    ),
                  ),
                  const SizedBox(width: 10),
                  const Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          'Tulis Post',
                          style: TextStyle(
                            fontSize: 17,
                            fontWeight: FontWeight.w800,
                            color: Color(0xFF0F172A),
                          ),
                        ),
                        Text(
                          'Publikasi ke Feed Jaringan',
                          style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w600,
                            color: Color(0xFF64748B),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
              content: SingleChildScrollView(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    // Transport Path Notice
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 10, vertical: 6),
                      decoration: BoxDecoration(
                        color: const Color(0xFF0F172A),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: const Row(
                        children: [
                          Icon(Icons.alt_route_rounded,
                              size: 14, color: Color(0xFF38BDF8)),
                          SizedBox(width: 6),
                          Expanded(
                            child: Text(
                              'Jalur: TCP (Port 5000) / UDP (Port 5001)',
                              style: TextStyle(
                                fontSize: 11,
                                fontWeight: FontWeight.w700,
                                color: Color(0xFF38BDF8),
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(height: 12),

                    TextField(
                      controller: controller,
                      maxLines: 3,
                      autofocus: true,
                      decoration: InputDecoration(
                        labelText: 'Konten Postingan',
                        hintText:
                            'mis. Hasil uji performa throughput TCP vs UDP di Wireshark...',
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(12),
                        ),
                        contentPadding: const EdgeInsets.symmetric(
                            horizontal: 14, vertical: 12),
                      ),
                    ),
                    const SizedBox(height: 12),

                    // Preset Diagram / Image Picker
                    const Text(
                      'Lampirkan Diagram / Gambar (Opsional):',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.w700,
                        color: Color(0xFF334155),
                      ),
                    ),
                    const SizedBox(height: 6),
                    Wrap(
                      spacing: 6,
                      runSpacing: 6,
                      children: [
                        ...presets.map((p) {
                          final isSelected = selectedPreset?['tag'] == p['tag'];
                          return ChoiceChip(
                            avatar: Text(p['icon']!),
                            label: Text(
                              p['title']!,
                              style: TextStyle(
                                fontSize: 11,
                                fontWeight: isSelected
                                    ? FontWeight.w700
                                    : FontWeight.w500,
                              ),
                            ),
                            selected: isSelected,
                            onSelected: (val) {
                              setDialogState(() {
                                selectedPreset = val ? p : null;
                              });
                            },
                          );
                        }),
                        if (selectedPreset != null)
                          ActionChip(
                            avatar: const Icon(Icons.close, size: 14),
                            label: const Text('Hapus Lampiran',
                                style: TextStyle(fontSize: 11)),
                            onPressed: () {
                              setDialogState(() {
                                selectedPreset = null;
                              });
                            },
                          ),
                      ],
                    ),
                  ],
                ),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(ctx),
                  child: const Text('Batal'),
                ),
                ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF0F172A),
                    foregroundColor: Colors.white,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                  ),
                  onPressed: () {
                    final text = controller.text.trim();
                    if (text.isEmpty) return;
                    Navigator.pop(ctx);
                    onPublishPost(text, selectedPreset);
                  },
                  child: const Text('Kirim Post'),
                ),
              ],
            );
          },
        );
      },
    );
  }

  // ==========================================
  // 4. CONTEXTUAL ROUTER BASED ON ACTIVE TAB
  // ==========================================
  static void showContextualActionDialog({
    required BuildContext context,
    required int activeTabIndex, // 0: Grup, 1: Teman
    required ValueChanged<String> onAddFriend,
    required ValueChanged<String> onCreateGroup,
    required ValueChanged<String> onJoinGroup,
    required List<String> availableGroups,
    String? currentUsername,
    List<String> availableFriends = const [],
  }) {
    if (activeTabIndex == 0) {
      showModalBottomSheet(
        context: context,
        backgroundColor: Colors.white,
        shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
        ),
        builder: (ctx) => SafeArea(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 16),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Center(
                  child: Container(
                    width: 40,
                    height: 4,
                    decoration: BoxDecoration(
                      color: const Color(0xFFE2E8F0),
                      borderRadius: BorderRadius.circular(2),
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                const Text(
                  'Aksi Obrolan Grup',
                  style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.bold,
                    color: Color(0xFF0F172A),
                  ),
                ),
                const SizedBox(height: 4),
                const Text(
                  'Pilih untuk membuat grup baru atau bergabung ke grup di jaringan',
                  style: TextStyle(fontSize: 12, color: Color(0xFF64748B)),
                ),
                const SizedBox(height: 16),
                ListTile(
                  leading: Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: const Color(0xFF6366F1).withAlpha(25),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.group_add_rounded, color: Color(0xFF6366F1)),
                  ),
                  title: const Text('Buat Grup Baru', style: TextStyle(fontWeight: FontWeight.w700)),
                  subtitle: const Text('Mulai grup obrolan baru untuk banyak perangkat'),
                  trailing: const Icon(Icons.arrow_forward_ios_rounded, size: 14),
                  onTap: () {
                    Navigator.pop(ctx);
                    showCreateGroupDialog(context: context, onCreateGroup: onCreateGroup);
                  },
                ),
                const Divider(height: 1),
                ListTile(
                  leading: Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: const Color(0xFF0284C7).withAlpha(25),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.login_rounded, color: Color(0xFF0284C7)),
                  ),
                  title: const Text('Gabung Grup Teman', style: TextStyle(fontWeight: FontWeight.w700)),
                  subtitle: const Text('Masuk ke grup yang dibuat di HP lain via nama grup'),
                  trailing: const Icon(Icons.arrow_forward_ios_rounded, size: 14),
                  onTap: () {
                    Navigator.pop(ctx);
                    showJoinGroupDialog(
                      context: context,
                      availableGroups: availableGroups,
                      onJoinGroup: onJoinGroup,
                    );
                  },
                ),
              ],
            ),
          ),
        ),
      );
    } else {
      showAddFriendDialog(
        context: context,
        onAddFriend: onAddFriend,
        currentUsername: currentUsername,
        availableFriends: availableFriends,
      );
    }
  }
}
