import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import '../models/user_profile.dart';
import '../services/profile_service.dart';

class CreateProfileDialog extends StatefulWidget {
  final UserProfile? initialProfile;
  final bool isInitialSetup;

  const CreateProfileDialog({
    super.key,
    this.initialProfile,
    this.isInitialSetup = false,
  });

  @override
  State<CreateProfileDialog> createState() => _CreateProfileDialogState();
}

class _CreateProfileDialogState extends State<CreateProfileDialog> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _nameController;
  late final TextEditingController _usernameController;
  late final TextEditingController _statusController;
  late final TextEditingController _hostController;
  late final TextEditingController _tcpPortController;
  late final TextEditingController _udpPortController;
  int _selectedAvatar = 0;

  @override
  void initState() {
    super.initState();
    final p = widget.initialProfile;
    _nameController = TextEditingController(text: p?.name ?? '');
    _usernameController = TextEditingController(text: p?.username ?? '');
    _statusController = TextEditingController(text: p?.status ?? 'Online');
    final defaultHost = (!kIsWeb && Platform.isAndroid) ? '10.0.2.2' : '127.0.0.1';
    _hostController = TextEditingController(text: p?.host ?? defaultHost);
    _tcpPortController =
        TextEditingController(text: p != null ? '${p.tcpPort}' : '5000');
    _udpPortController =
        TextEditingController(text: p != null ? '${p.udpPort}' : '5001');
    _selectedAvatar = p?.avatarIndex ?? 0;
  }

  @override
  void dispose() {
    _nameController.dispose();
    _usernameController.dispose();
    _statusController.dispose();
    _hostController.dispose();
    _tcpPortController.dispose();
    _udpPortController.dispose();
    super.dispose();
  }

  Future<void> _handleSave() async {
    if (!_formKey.currentState!.validate()) return;

    final profile = UserProfile(
      name: _nameController.text.trim(),
      username: _usernameController.text.trim().replaceAll(' ', '_'),
      avatarIndex: _selectedAvatar,
      status: _statusController.text.trim(),
      host: _hostController.text.trim(),
      tcpPort: int.tryParse(_tcpPortController.text.trim()) ?? 5000,
      udpPort: int.tryParse(_udpPortController.text.trim()) ?? 5001,
      createdAt: widget.initialProfile?.createdAt ?? DateTime.now(),
    );

    await ProfileService.instance.saveProfile(profile);
    if (!mounted) return;
    Navigator.of(context).pop(profile);
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(24)),
      backgroundColor: Colors.white,
      insetPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 24),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 460),
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Form(
            key: _formKey,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Header
                Row(
                  children: [
                    Container(
                      width: 44,
                      height: 44,
                      decoration: BoxDecoration(
                        color: const Color(0xFF0F172A),
                        borderRadius: BorderRadius.circular(14),
                      ),
                      alignment: Alignment.center,
                      child: Text(
                        UserProfile.avatarPresets[_selectedAvatar],
                        style: const TextStyle(fontSize: 22),
                      ),
                    ),
                    const SizedBox(width: 14),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            widget.isInitialSetup
                                ? 'Buat Profil Lokal'
                                : 'Edit Profil Lokal',
                            style: const TextStyle(
                              fontSize: 19,
                              fontWeight: FontWeight.w700,
                              color: Color(0xFF0F172A),
                              letterSpacing: -0.3,
                            ),
                          ),
                          const SizedBox(height: 2),
                          const Text(
                            'Identitas disimpan secara lokal di perangkat ini',
                            style: TextStyle(
                              fontSize: 12,
                              color: Color(0xFF64748B),
                            ),
                          ),
                        ],
                      ),
                    ),
                    if (!widget.isInitialSetup)
                      IconButton(
                        icon: const Icon(Icons.close, size: 20),
                        onPressed: () => Navigator.of(context).pop(),
                      ),
                  ],
                ),
                const SizedBox(height: 20),
                const Divider(height: 1, color: Color(0xFFF1F5F9)),
                const SizedBox(height: 18),

                // Avatar Choice
                const Text(
                  'Pilih Avatar',
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                    color: Color(0xFF334155),
                  ),
                ),
                const SizedBox(height: 8),
                SizedBox(
                  height: 52,
                  child: ListView.separated(
                    scrollDirection: Axis.horizontal,
                    itemCount: UserProfile.avatarPresets.length,
                    separatorBuilder: (_, _) => const SizedBox(width: 8),
                    itemBuilder: (context, idx) {
                      final isSelected = _selectedAvatar == idx;
                      return GestureDetector(
                        onTap: () => setState(() => _selectedAvatar = idx),
                        child: AnimatedContainer(
                          duration: const Duration(milliseconds: 180),
                          width: 48,
                          height: 48,
                          decoration: BoxDecoration(
                            color: isSelected
                                ? const Color(0xFF0F172A)
                                : const Color(0xFFF8FAFC),
                            borderRadius: BorderRadius.circular(14),
                            border: Border.all(
                              color: isSelected
                                  ? const Color(0xFF0F172A)
                                  : const Color(0xFFE2E8F0),
                              width: isSelected ? 2 : 1,
                            ),
                          ),
                          alignment: Alignment.center,
                          child: Text(
                            UserProfile.avatarPresets[idx],
                            style: const TextStyle(fontSize: 22),
                          ),
                        ),
                      );
                    },
                  ),
                ),
                const SizedBox(height: 16),

                // Name & Username
                Row(
                  children: [
                    Expanded(
                      flex: 6,
                      child: _buildInput(
                        label: 'Nama Tampilan',
                        hint: 'mis. Salma atau Lian',
                        controller: _nameController,
                        validator: (v) =>
                            v == null || v.trim().isEmpty ? 'Nama wajib diisi' : null,
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      flex: 5,
                      child: _buildInput(
                        label: 'Username',
                        hint: 'mis. salma atau lian',
                        controller: _usernameController,
                        validator: (v) => v == null || v.trim().isEmpty
                            ? 'Username wajib'
                            : null,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),

                // Status / Bio
                _buildInput(
                  label: 'Status / Catatan',
                  hint: 'mis. Active, Siap Uji TCP/UDP',
                  controller: _statusController,
                ),
                const SizedBox(height: 14),

                // Server IP & Ports
                const Text(
                  'Konfigurasi Jaringan Server (Jarkom)',
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                    color: Color(0xFF334155),
                  ),
                ),
                const SizedBox(height: 8),
                Row(
                  children: [
                    Expanded(
                      flex: 6,
                      child: _buildInput(
                        label: 'Host / IP Server',
                        hint: '127.0.0.1 / 192.168.1.x',
                        controller: _hostController,
                        validator: (v) =>
                            v == null || v.trim().isEmpty ? 'IP wajib diisi' : null,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      flex: 3,
                      child: _buildInput(
                        label: 'TCP Port',
                        hint: '5000',
                        controller: _tcpPortController,
                        keyboardType: TextInputType.number,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      flex: 3,
                      child: _buildInput(
                        label: 'UDP Port',
                        hint: '5001',
                        controller: _udpPortController,
                        keyboardType: TextInputType.number,
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                Wrap(
                  spacing: 6,
                  children: [
                    ActionChip(
                      label: const Text('10.0.2.2 (Emulator)', style: TextStyle(fontSize: 10)),
                      padding: EdgeInsets.zero,
                      visualDensity: VisualDensity.compact,
                      onPressed: () => setState(() => _hostController.text = '10.0.2.2'),
                    ),
                    ActionChip(
                      label: const Text('192.168.1.66 (LAN)', style: TextStyle(fontSize: 10)),
                      padding: EdgeInsets.zero,
                      visualDensity: VisualDensity.compact,
                      onPressed: () => setState(() => _hostController.text = '192.168.1.66'),
                    ),
                    ActionChip(
                      label: const Text('127.0.0.1 (Local)', style: TextStyle(fontSize: 10)),
                      padding: EdgeInsets.zero,
                      visualDensity: VisualDensity.compact,
                      onPressed: () => setState(() => _hostController.text = '127.0.0.1'),
                    ),
                  ],
                ),
                const SizedBox(height: 24),

                // Action Button
                SizedBox(
                  width: double.infinity,
                  height: 48,
                  child: ElevatedButton(
                    onPressed: _handleSave,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: const Color(0xFF0F172A),
                      foregroundColor: Colors.white,
                      elevation: 0,
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(14),
                      ),
                    ),
                    child: Text(
                      widget.isInitialSetup
                          ? 'Simpan Profil & Masuk DualChat'
                          : 'Perbarui Profil',
                      style: const TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildInput({
    required String label,
    required String hint,
    required TextEditingController controller,
    String? Function(String?)? validator,
    TextInputType keyboardType = TextInputType.text,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(
            fontSize: 11,
            fontWeight: FontWeight.w600,
            color: Color(0xFF64748B),
          ),
        ),
        const SizedBox(height: 4),
        TextFormField(
          controller: controller,
          validator: validator,
          keyboardType: keyboardType,
          style: const TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w500,
            color: Color(0xFF0F172A),
          ),
          decoration: InputDecoration(
            isDense: true,
            hintText: hint,
            hintStyle: const TextStyle(color: Color(0xFF94A3B8), fontSize: 13),
            filled: true,
            fillColor: const Color(0xFFF8FAFC),
            contentPadding:
                const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            border: OutlineInputBorder(
              borderRadius: BorderRadius.circular(10),
              borderSide: const BorderSide(color: Color(0xFFE2E8F0)),
            ),
            enabledBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(10),
              borderSide: const BorderSide(color: Color(0xFFE2E8F0)),
            ),
            focusedBorder: OutlineInputBorder(
              borderRadius: BorderRadius.circular(10),
              borderSide:
                  const BorderSide(color: Color(0xFF0F172A), width: 1.5),
            ),
          ),
        ),
      ],
    );
  }
}
