import 'dart:async';
import 'package:flutter/material.dart';
import 'models/chat_message.dart';
import 'models/chat_room.dart';
import 'models/user_profile.dart';
import 'screens/chat_screen.dart';
import 'screens/profile_screen.dart';
import 'services/chat_storage_service.dart';
import 'services/dualchat_client.dart';
import 'services/profile_service.dart';
import 'services/encryption_service.dart';
import 'widgets/action_dialogs.dart';
import 'widgets/create_profile_dialog.dart';
import 'widgets/floating_menu.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const DualChatApp());
}

class DualChatApp extends StatelessWidget {
  final UserProfile? initialProfile;
  final DualChatClient? client;

  const DualChatApp({
    super.key,
    this.initialProfile,
    this.client,
  });

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'DualChat — Messaging TCP vs UDP',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        brightness: Brightness.light,
        scaffoldBackgroundColor: const Color(0xFFF8FAFC),
        colorScheme: const ColorScheme.light(
          primary: Color(0xFF0F172A),
          secondary: Color(0xFF10B981), // Emerald for TCP
          tertiary: Color(0xFFF59E0B), // Amber for UDP
          surface: Colors.white,
          error: Color(0xFFEF4444),
        ),
        useMaterial3: true,
      ),
      home: DualChatHomeScreen(
        initialProfile: initialProfile,
        client: client,
      ),
    );
  }
}

class DualChatHomeScreen extends StatefulWidget {
  final UserProfile? initialProfile;
  final DualChatClient? client;

  const DualChatHomeScreen({
    super.key,
    this.initialProfile,
    this.client,
  });

  @override
  State<DualChatHomeScreen> createState() => _DualChatHomeScreenState();
}

class _DualChatHomeScreenState extends State<DualChatHomeScreen>
    with SingleTickerProviderStateMixin {
  late final DualChatClient _client;
  UserProfile? _profile;
  late final TabController _tabController;
  final TextEditingController _searchController = TextEditingController();

  TransportProto _selectedProto = TransportProto.tcp;
  ConnectionStatus _status = ConnectionStatus.disconnected;
  int _currentNavIndex = 0; // 0: Percakapan (Grup & Teman), 1: TCP Mode, 2: UDP Mode, 3: Profile

  List<ChatRoom> _rooms = [];
  final Set<String> _discoveredNetworkGroups = {};
  final Set<String> _discoveredNetworkUsers = {};
  String _searchQuery = '';

  StreamSubscription? _msgSub;
  StreamSubscription? _statusSub;
  StreamSubscription? _metricsSub;

  @override
  void initState() {
    super.initState();
    _client = widget.client ?? DualChatClient();
    _tabController = TabController(length: 2, vsync: this);
    _tabController.addListener(() {
      if (mounted) setState(() {});
    });

    _msgSub = _client.onMessage.listen(_onIncomingMessage);
    _statusSub = _client.onStatusChanged.listen((status) {
      if (!mounted) return;
      setState(() => _status = status);
    });
    _metricsSub = _client.onMetricsUpdated.listen((_) {
      if (!mounted) return;
      setState(() {});
    });

    if (widget.initialProfile != null) {
      _profile = widget.initialProfile;
      _rooms = ChatRoom.initialRooms(widget.initialProfile!.username);
      _autoConnect();
    } else {
      // Load real local profile or prompt creation
      _initProfileAndRooms();
    }
  }

  Future<void> _initProfileAndRooms() async {
    final loaded = await ProfileService.instance.loadProfile();
    if (!mounted) return;

    if (loaded == null) {
      // Must create profile first locally
      WidgetsBinding.instance.addPostFrameCallback((_) async {
        final created = await showDialog<UserProfile>(
          context: context,
          barrierDismissible: false,
          builder: (context) => const CreateProfileDialog(isInitialSetup: true),
        );
        if (created != null && mounted) {
          final loadedRooms =
              await ChatStorageService.instance.loadRooms(created.username);
          setState(() {
            _profile = created;
            _rooms = loadedRooms;
          });
          _autoConnect();
        }
      });
    } else {
      final loadedRooms =
          await ChatStorageService.instance.loadRooms(loaded.username);
      setState(() {
        _profile = loaded;
        _rooms = loadedRooms;
      });
      _autoConnect();
    }
  }

  Future<void> _autoConnect() async {
    if (_profile == null) return;
    final port = _selectedProto == TransportProto.tcp
        ? _profile!.tcpPort
        : _profile!.udpPort;

    try {
      await _client.connect(
        proto: _selectedProto,
        host: _profile!.host,
        port: port,
        username: _profile!.username,
      );
    } catch (_) {}
  }

  void _onIncomingMessage(ChatMessage msg) {
    if (!mounted || _profile == null) return;

    // Track active network users
    if (msg.user.isNotEmpty &&
        msg.user.toLowerCase() != 'server' &&
        msg.user.toLowerCase() != 'system' &&
        !msg.user.startsWith('~') &&
        msg.user.toLowerCase() != _profile!.username.toLowerCase()) {
      _discoveredNetworkUsers.add(msg.user);
    }

    // Server join notifications: "<user> bergabung"
    if (msg.user.toLowerCase() == 'server') {
      final joinMatch = RegExp(r'^(\S+)\s+bergabung').firstMatch(msg.data.trim());
      if (joinMatch != null) {
        final joinedUser = joinMatch.group(1)?.trim();
        if (joinedUser != null &&
            joinedUser.isNotEmpty &&
            joinedUser.toLowerCase() != 'server' &&
            joinedUser.toLowerCase() != 'system' &&
            !joinedUser.startsWith('~') &&
            joinedUser.toLowerCase() != _profile!.username.toLowerCase()) {
          _discoveredNetworkUsers.add(joinedUser);
        }
      }

      final leaveMatch = RegExp(r'^(\S+)\s+keluar').firstMatch(msg.data.trim());
      if (leaveMatch != null) {
        final leftUser = leaveMatch.group(1)?.trim();
        if (leftUser != null) {
          _discoveredNetworkUsers.removeWhere((u) => u.toLowerCase() == leftUser.toLowerCase());
        }
      }
    }

    // Ignore self echoes
    if (msg.user.toLowerCase() == _profile!.username.toLowerCase()) return;

    setState(() {
      String cleanText = msg.data;
      ChatRoom? targetRoom;

      // 1. Direct Message (Friends, 2 people): [DM:<recipient>] <text>
      final dmMatch = RegExp(r'^\[DM:([^\]]+)\]\s*(.*)$').firstMatch(msg.data);
      if (dmMatch != null) {
        final toUser = dmMatch.group(1)?.trim().toLowerCase() ?? '';
        final rawContent = dmMatch.group(2) ?? '';
        final channelId = EncryptionService.instance.getDirectChannelId(
          msg.user,
          _profile!.username,
        );
        cleanText = EncryptionService.instance.decryptPayload(
          rawContent,
          channelId: channelId,
        );

        if (toUser == _profile!.username.toLowerCase()) {
          // Addressed to me from msg.user
          for (final r in _rooms) {
            if (!r.isGroup &&
                r.members.any((m) => m.toLowerCase() == msg.user.toLowerCase())) {
              targetRoom = r;
              break;
            }
          }

          // If no friend conversation exists yet, auto-create a 2-person friend room
          if (targetRoom == null) {
            targetRoom = ChatRoom(
              id: 'friend_${DateTime.now().millisecondsSinceEpoch}',
              name: msg.user,
              isGroup: false,
              members: [_profile!.username, msg.user],
              avatarEmoji: '👤',
              avatarColor: const Color(0xFF10B981),
              statusText: 'Active barusan',
              lastMessage: cleanText,
              lastMessageSender: msg.user,
              lastMessageTime: 'Now',
            );
            _rooms.insert(0, targetRoom);
          }
        } else {
          // Direct message meant for someone else in broadcast domain
          return;
        }
      }

      // 2. Group Message (>2 people): [GROUP:<groupName>] <text>
      final groupMatch = RegExp(r'^\[GROUP:([^\]]+)\]\s*(.*)$').firstMatch(msg.data);
      if (groupMatch != null) {
        final groupName = groupMatch.group(1)?.trim() ?? '';
        final rawContent = groupMatch.group(2) ?? '';
        final channelId = EncryptionService.instance.getGroupChannelId(groupName);
        cleanText = EncryptionService.instance.decryptPayload(
          rawContent,
          channelId: channelId,
        );

        if (groupName.isNotEmpty) {
          _discoveredNetworkGroups.add(groupName);
        }

        for (final r in _rooms) {
          if (r.isGroup && r.name.toLowerCase() == groupName.toLowerCase()) {
            targetRoom = r;
            break;
          }
        }

        // Auto-discover group if not created yet
        if (targetRoom == null) {
          targetRoom = ChatRoom(
            id: 'group_${DateTime.now().millisecondsSinceEpoch}',
            name: groupName,
            isGroup: true,
            members: [_profile!.username, msg.user],
            avatarEmoji: '👥',
            avatarColor: const Color(0xFF6366F1),
            statusText: 'Grup Jaringan',
            lastMessage: cleanText,
            lastMessageSender: msg.user,
            lastMessageTime: 'Now',
          );
          _rooms.insert(0, targetRoom);
        } else {
          if (!targetRoom.members.contains(msg.user)) {
            targetRoom.members.add(msg.user);
          }
        }
      }

      // 3. Plain broadcast or server status (CLI client.py compatibility)
      targetRoom ??= _rooms.firstWhere(
        (r) => r.id == 'group_general',
        orElse: () {
          final gen = ChatRoom(
            id: 'group_general',
            name: 'General Broadcast',
            isGroup: true,
            members: [_profile!.username, 'server'],
            avatarEmoji: '🌐',
            avatarColor: const Color(0xFF0F172A),
            statusText: 'Ruang publik server',
            lastMessage: '',
            lastMessageSender: 'server',
            lastMessageTime: 'Now',
          );
          _rooms.add(gen);
          return gen;
        },
      );

      final displayMsg = ChatMessage(
        type: msg.type,
        seq: msg.seq,
        ts: msg.ts,
        user: msg.user,
        data: cleanText,
        isSelf: false,
        rttMs: msg.rttMs,
        warning: msg.warning,
      );

      targetRoom.addMessage(displayMsg);
      targetRoom.unreadCount++;
      ChatStorageService.instance.saveRooms(_rooms);
    });
  }

  @override
  void dispose() {
    _msgSub?.cancel();
    _statusSub?.cancel();
    _metricsSub?.cancel();
    _client.dispose();
    _tabController.dispose();
    _searchController.dispose();
    super.dispose();
  }

  void _switchProtocol(TransportProto newProto) async {
    if (_selectedProto == newProto && _status == ConnectionStatus.connected) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            'Sudah aktif di mode ${newProto.name.toUpperCase()} (Port ${newProto == TransportProto.tcp ? _profile?.tcpPort ?? 5000 : _profile?.udpPort ?? 5001})',
          ),
          duration: const Duration(milliseconds: 1400),
          behavior: SnackBarBehavior.floating,
        ),
      );
      return;
    }

    setState(() {
      _selectedProto = newProto;
    });

    final port = newProto == TransportProto.tcp
        ? (_profile?.tcpPort ?? 5000)
        : (_profile?.udpPort ?? 5001);

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Row(
          children: [
            Icon(
              newProto == TransportProto.tcp
                  ? Icons.lock_outline
                  : Icons.bolt_outlined,
              color: Colors.white,
              size: 16,
            ),
            const SizedBox(width: 8),
            Text(
              'Beralih ke mode ${newProto.name.toUpperCase()} (Port $port)...',
              style: const TextStyle(fontWeight: FontWeight.w600),
            ),
          ],
        ),
        backgroundColor: newProto == TransportProto.tcp
            ? const Color(0xFF059669)
            : const Color(0xFFD97706),
        duration: const Duration(seconds: 2),
        behavior: SnackBarBehavior.floating,
      ),
    );

    if (_profile != null) {
      await _client.connect(
        proto: newProto,
        host: _profile!.host,
        port: port,
        username: _profile!.username,
      );
    }
  }

  Future<void> _toggleConnection() async {
    if (_status == ConnectionStatus.connected) {
      await _client.disconnect(sendLeave: true);
    } else {
      await _autoConnect();
    }
  }

  void _openChat(ChatRoom room) {
    room.markAsRead();
    ChatStorageService.instance.saveRooms(_rooms);

    Navigator.of(context).push(
      MaterialPageRoute(
        builder: (context) => ChatScreen(
          room: room,
          profile: _profile ??
              UserProfile(
                name: 'User',
                username: 'user_1',
                createdAt: DateTime.now(),
              ),
          client: _client,
          currentProto: _selectedProto,
          connectionStatus: _status,
          onPing: () => _client.sendPing(),
          onSwitchProtocol: _switchProtocol,
          onMessageSent: (msg) {
            ChatStorageService.instance.saveRooms(_rooms);
            setState(() {});
          },
        ),
      ),
    );
  }

  void _confirmDeleteRoom(ChatRoom room) {
    if (room.id == 'group_general') {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Ruang publik General Broadcast tidak dapat dihapus.'),
          behavior: SnackBarBehavior.floating,
        ),
      );
      return;
    }

    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: Colors.white,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
        title: Text(
          room.isGroup ? 'Hapus Grup?' : 'Hapus Obrolan Teman?',
          style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 17),
        ),
        content: Text(
          'Apakah Anda yakin ingin menghapus "${room.name}" beserta riwayat pesannya dari perangkat lokal?',
          style: const TextStyle(color: Color(0xFF64748B), fontSize: 13),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Batal'),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFFEF4444),
              foregroundColor: Colors.white,
            ),
            onPressed: () {
              Navigator.pop(ctx);
              setState(() {
                _rooms.removeWhere((r) => r.id == room.id);
              });
              ChatStorageService.instance.saveRooms(_rooms);
              ScaffoldMessenger.of(context).showSnackBar(
                SnackBar(
                  content: Text('Obrolan "${room.name}" berhasil dihapus.'),
                  behavior: SnackBarBehavior.floating,
                ),
              );
            },
            child: const Text('Hapus'),
          ),
        ],
      ),
    );
  }

  void _onAddFriend(String friendName) {
    final cleanName = friendName.trim();
    if (cleanName.isEmpty) return;

    if (_profile != null &&
        cleanName.toLowerCase() == _profile!.username.toLowerCase()) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('Anda tidak bisa menambahkan username sendiri (@$cleanName) sebagai teman.'),
          backgroundColor: const Color(0xFFEF4444),
          behavior: SnackBarBehavior.floating,
        ),
      );
      return;
    }

    // Check if room already exists - open it instead of creating duplicates
    for (final r in _rooms) {
      if (!r.isGroup && r.name.toLowerCase() == cleanName.toLowerCase()) {
        _tabController.animateTo(1);
        _openChat(r);
        return;
      }
    }

    final newRoom = ChatRoom(
      id: 'friend_${DateTime.now().millisecondsSinceEpoch}',
      name: cleanName,
      isGroup: false,
      members: [_profile?.username ?? 'me', cleanName],
      avatarEmoji: '👤',
      avatarColor: const Color(0xFF10B981),
      statusText: 'Active',
      lastMessage: 'Obrolan baru dibuat',
      lastMessageSender: 'system',
      lastMessageTime: 'Now',
    );

    setState(() {
      _rooms.insert(0, newRoom);
      _discoveredNetworkUsers.add(cleanName);
    });
    ChatStorageService.instance.saveRooms(_rooms);
    _tabController.animateTo(1);
    _openChat(newRoom);
  }

  void _onCreateGroup(String groupName) {
    final cleanName = groupName.trim();
    if (cleanName.isEmpty) return;

    for (final r in _rooms) {
      if (r.isGroup && r.name.toLowerCase() == cleanName.toLowerCase()) {
        _tabController.animateTo(0);
        _openChat(r);
        return;
      }
    }

    final newRoom = ChatRoom(
      id: 'group_${DateTime.now().millisecondsSinceEpoch}',
      name: cleanName,
      isGroup: true,
      members: [_profile?.username ?? 'me'],
      avatarEmoji: '👥',
      avatarColor: const Color(0xFF6366F1),
      statusText: 'Grup Percakapan',
      lastMessage: 'Grup obrolan dibuat',
      lastMessageSender: 'system',
      lastMessageTime: 'Now',
    );

    setState(() {
      _rooms.insert(0, newRoom);
      _discoveredNetworkGroups.add(cleanName);
    });
    ChatStorageService.instance.saveRooms(_rooms);

    if (_profile != null) {
      final channelId = EncryptionService.instance.getGroupChannelId(cleanName);
      final encPayload = EncryptionService.instance.encryptPayload(
        '[SISTEM] ${_profile!.username} membuat grup baru!',
        channelId: channelId,
      );
      _client.sendMessage('[GROUP:$cleanName] $encPayload');
    }

    _tabController.animateTo(0);
    _openChat(newRoom);
  }

  void _onJoinGroup(String groupName) {
    final cleanName = groupName.trim();
    if (cleanName.isEmpty) return;

    ChatRoom? target;
    for (final r in _rooms) {
      if (r.isGroup && r.name.toLowerCase() == cleanName.toLowerCase()) {
        target = r;
        break;
      }
    }

    if (target == null) {
      target = ChatRoom(
        id: 'group_${DateTime.now().millisecondsSinceEpoch}',
        name: cleanName,
        isGroup: true,
        members: [_profile?.username ?? 'me'],
        avatarEmoji: '👥',
        avatarColor: const Color(0xFF6366F1),
        statusText: 'Grup Jaringan',
        lastMessage: 'Bergabung ke grup',
        lastMessageSender: 'system',
        lastMessageTime: 'Now',
      );
      setState(() {
        _rooms.insert(0, target!);
        _discoveredNetworkGroups.add(cleanName);
      });
      ChatStorageService.instance.saveRooms(_rooms);
    }

    if (_profile != null) {
      final channelId = EncryptionService.instance.getGroupChannelId(cleanName);
      final encPayload = EncryptionService.instance.encryptPayload(
        '[SISTEM] ${_profile!.username} bergabung ke grup!',
        channelId: channelId,
      );
      _client.sendMessage('[GROUP:$cleanName] $encPayload');
    }

    _tabController.animateTo(0);
    _openChat(target);
  }

  List<String> get _onlineFriendCandidates {
    return _discoveredNetworkUsers
        .where((u) =>
            u.toLowerCase() != 'server' &&
            u.toLowerCase() != 'system' &&
            !u.startsWith('~') &&
            (_profile == null ||
                u.toLowerCase() != _profile!.username.toLowerCase()) &&
            !_rooms.any((r) =>
                !r.isGroup && r.name.toLowerCase() == u.toLowerCase()))
        .toList();
  }

  void _showAddFriendDialog() {
    ActionDialogs.showAddFriendDialog(
      context: context,
      onAddFriend: _onAddFriend,
      currentUsername: _profile?.username,
      availableFriends: _onlineFriendCandidates,
    );
  }

  void _showCreateGroupDialog() {
    ActionDialogs.showCreateGroupDialog(
      context: context,
      onCreateGroup: _onCreateGroup,
    );
  }

  void _showJoinGroupDialog() {
    ActionDialogs.showJoinGroupDialog(
      context: context,
      availableGroups: _discoveredNetworkGroups.toList(),
      onJoinGroup: _onJoinGroup,
    );
  }

  void _showContextualActionDialog() {
    ActionDialogs.showContextualActionDialog(
      context: context,
      activeTabIndex: _tabController.index,
      onAddFriend: _onAddFriend,
      onCreateGroup: _onCreateGroup,
      onJoinGroup: _onJoinGroup,
      availableGroups: _discoveredNetworkGroups.toList(),
      currentUsername: _profile?.username,
      availableFriends: _onlineFriendCandidates,
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_profile == null) {
      return const Scaffold(
        backgroundColor: Colors.white,
        body: Center(
          child: CircularProgressIndicator(color: Color(0xFF0F172A)),
        ),
      );
    }

    final isTcp = _selectedProto == TransportProto.tcp;
    final protoColor =
        isTcp ? const Color(0xFF10B981) : const Color(0xFFF59E0B);
    final isConnected = _status == ConnectionStatus.connected;
    final isConnecting = _status == ConnectionStatus.connecting;

    // Filter rooms based on search query
    final filteredRooms = _rooms.where((r) {
      if (_searchQuery.isEmpty) return true;
      final q = _searchQuery.toLowerCase();
      return r.name.toLowerCase().contains(q) ||
          r.lastMessage.toLowerCase().contains(q);
    }).toList();

    final groupRooms = filteredRooms.where((r) => r.isGroup).toList();
    final friendRooms = filteredRooms.where((r) => !r.isGroup).toList();

    return Scaffold(
      backgroundColor: const Color(0xFFF8FAFC),
      body: SafeArea(
        child: _currentNavIndex == 3
            // PROFILE SCREEN EMBEDDED
            ? ProfileScreen(
                profile: _profile!,
                client: _client,
                currentProto: _selectedProto,
                connectionStatus: _status,
                isEmbedded: true,
                onBackToChats: () => setState(() => _currentNavIndex = 0),
                onProfileUpdated: (updated) {
                  setState(() => _profile = updated);
                  _autoConnect();
                },
                onToggleConnection: _toggleConnection,
                onSwitchProtocol: _switchProtocol,
              )
            // MAIN DUALCHAT VIEW (FEED, GROUP, FRIENDS)
            : Stack(
                children: [
                  Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      // Top Header: "DualChat" (Title) & Protocol / Status + Add Button
                      Padding(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 20, vertical: 12),
                        child: Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            // Title "DualChat" & Protocol Chip
                            Row(
                              children: [
                                const Text(
                                  'DualChat',
                                  style: TextStyle(
                                    fontSize: 26,
                                    fontWeight: FontWeight.w800,
                                    color: Color(0xFF0F172A),
                                    letterSpacing: -0.5,
                                  ),
                                ),
                                const SizedBox(width: 10),
                                // Protocol Badge (Tap to toggle TCP/UDP)
                                GestureDetector(
                                  onTap: () {
                                    _switchProtocol(isTcp
                                        ? TransportProto.udp
                                        : TransportProto.tcp);
                                  },
                                  child: Container(
                                    padding: const EdgeInsets.symmetric(
                                        horizontal: 9, vertical: 4),
                                    decoration: BoxDecoration(
                                      color: protoColor.withAlpha(24),
                                      borderRadius: BorderRadius.circular(12),
                                      border: Border.all(
                                          color: protoColor.withAlpha(90)),
                                    ),
                                    child: Row(
                                      mainAxisSize: MainAxisSize.min,
                                      children: [
                                        Container(
                                          width: 6,
                                          height: 6,
                                          decoration: BoxDecoration(
                                            shape: BoxShape.circle,
                                            color: isConnected
                                                ? const Color(0xFF10B981)
                                                : isConnecting
                                                    ? const Color(0xFFF59E0B)
                                                    : const Color(0xFF94A3B8),
                                          ),
                                        ),
                                        const SizedBox(width: 5),
                                        Text(
                                          _selectedProto.name.toUpperCase(),
                                          style: TextStyle(
                                            color: protoColor,
                                            fontWeight: FontWeight.w800,
                                            fontSize: 11,
                                            letterSpacing: 0.8,
                                          ),
                                        ),
                                      ],
                                    ),
                                  ),
                                ),
                              ],
                            ),

                            // "+" Action Button
                            Container(
                              width: 38,
                              height: 38,
                              decoration: BoxDecoration(
                                color: const Color(0xFFF1F5F9),
                                borderRadius: BorderRadius.circular(12),
                              ),
                              child: IconButton(
                                padding: EdgeInsets.zero,
                                icon: const Icon(
                                  Icons.add,
                                  size: 22,
                                  color: Color(0xFF0F172A),
                                ),
                                onPressed: _showContextualActionDialog,
                              ),
                            ),
                          ],
                        ),
                      ),

                      // Search Bar
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 20),
                        child: Container(
                          height: 44,
                          decoration: BoxDecoration(
                            color: const Color(0xFFF1F5F9),
                            borderRadius: BorderRadius.circular(22),
                          ),
                          padding: const EdgeInsets.symmetric(horizontal: 14),
                          child: Row(
                            children: [
                              const Icon(
                                Icons.search,
                                size: 19,
                                color: Color(0xFF94A3B8),
                              ),
                              const SizedBox(width: 8),
                              Expanded(
                                child: TextField(
                                  controller: _searchController,
                                  onChanged: (val) {
                                    setState(() => _searchQuery = val.trim());
                                  },
                                  style: const TextStyle(
                                    fontSize: 13,
                                    color: Color(0xFF0F172A),
                                  ),
                                  decoration: const InputDecoration(
                                    hintText:
                                        'Cari grup atau nama teman...',
                                    hintStyle: TextStyle(
                                      color: Color(0xFF94A3B8),
                                      fontSize: 13,
                                    ),
                                    border: InputBorder.none,
                                    isDense: true,
                                  ),
                                ),
                              ),
                              if (_searchQuery.isNotEmpty)
                                GestureDetector(
                                  onTap: () {
                                    _searchController.clear();
                                    setState(() => _searchQuery = '');
                                  },
                                  child: const Icon(
                                    Icons.close,
                                    size: 16,
                                    color: Color(0xFF94A3B8),
                                  ),
                                ),
                            ],
                          ),
                        ),
                      ),
                      const SizedBox(height: 12),

                      // Tab Bar: Grup | Teman
                      Container(
                        decoration: const BoxDecoration(
                          border: Border(
                            bottom:
                                BorderSide(color: Color(0xFFE2E8F0), width: 1),
                          ),
                        ),
                        child: TabBar(
                          controller: _tabController,
                          indicatorColor: const Color(0xFF0F172A),
                          indicatorWeight: 2.5,
                          indicatorSize: TabBarIndicatorSize.label,
                          labelColor: const Color(0xFF0F172A),
                          unselectedLabelColor: const Color(0xFF94A3B8),
                          labelStyle: const TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w700,
                          ),
                          unselectedLabelStyle: const TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w500,
                          ),
                          tabs: [
                            Tab(
                              child: Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  const Text('Grup'),
                                  const SizedBox(width: 6),
                                  Container(
                                    padding: const EdgeInsets.symmetric(
                                        horizontal: 6, vertical: 1),
                                    decoration: BoxDecoration(
                                      color: const Color(0xFFF1F5F9),
                                      borderRadius: BorderRadius.circular(10),
                                    ),
                                    child: Text(
                                      '${groupRooms.length}',
                                      style: const TextStyle(
                                        fontSize: 10,
                                        fontWeight: FontWeight.w600,
                                        color: Color(0xFF64748B),
                                      ),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                            Tab(
                              child: Row(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  const Text('Teman'),
                                  const SizedBox(width: 6),
                                  Container(
                                    padding: const EdgeInsets.symmetric(
                                        horizontal: 6, vertical: 1),
                                    decoration: BoxDecoration(
                                      color: const Color(0xFFF1F5F9),
                                      borderRadius: BorderRadius.circular(10),
                                    ),
                                    child: Text(
                                      '${friendRooms.length}',
                                      style: const TextStyle(
                                        fontSize: 10,
                                        fontWeight: FontWeight.w600,
                                        color: Color(0xFF64748B),
                                      ),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ],
                        ),
                      ),

                      // Tab Content List
                      Expanded(
                        child: TabBarView(
                          controller: _tabController,
                          children: [
                            // 1. GROUP TAB
                            _buildGroupList(groupRooms),

                            // 2. FRIENDS TAB
                            _buildFriendsList(friendRooms),
                          ],
                        ),
                      ),
                    ],
                  ),

                  // Floating Capsule Pill "Menu ☰"
                  Positioned(
                    bottom: 14,
                    left: 0,
                    right: 0,
                    child: Center(
                      child: FloatingMenuCapsule(
                        currentProto: _selectedProto,
                        status: _status,
                        onSwitchProto: _switchProtocol,
                        onPing: () => _client.sendPing(),
                        onNewChat: _showContextualActionDialog,
                        onSettings: () {
                          setState(() => _currentNavIndex = 3);
                        },
                      ),
                    ),
                  ),
                ],
              ),
      ),
      bottomNavigationBar: _buildBottomNav(isTcp, protoColor),
    );
  }

  Widget _buildGroupActionBar() {
    return Container(
      margin: const EdgeInsets.fromLTRB(16, 12, 16, 6),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: const Color(0xFFE2E8F0)),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withAlpha(5),
            blurRadius: 4,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Expanded(
                child: ElevatedButton.icon(
                  onPressed: _showCreateGroupDialog,
                  icon: const Icon(Icons.add_rounded, size: 18),
                  label: const Text('Buat Grup', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.bold)),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF0F172A),
                    foregroundColor: Colors.white,
                    elevation: 0,
                    padding: const EdgeInsets.symmetric(vertical: 10),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: _showJoinGroupDialog,
                  icon: const Icon(Icons.login_rounded, size: 18, color: Color(0xFF0284C7)),
                  label: const Text('Gabung Grup', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.bold, color: Color(0xFF0284C7))),
                  style: OutlinedButton.styleFrom(
                    backgroundColor: const Color(0xFF0284C7).withAlpha(15),
                    side: const BorderSide(color: Color(0xFF0284C7), width: 1.2),
                    elevation: 0,
                    padding: const EdgeInsets.symmetric(vertical: 10),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
            decoration: BoxDecoration(
              color: const Color(0xFFF8FAFC),
              borderRadius: BorderRadius.circular(8),
            ),
            child: const Row(
              children: [
                Icon(Icons.info_outline_rounded, size: 14, color: Color(0xFF64748B)),
                SizedBox(width: 6),
                Expanded(
                  child: Text(
                    'HP lain klik "Gabung Grup" & masukkan nama grup yang sama untuk saling terhubung.',
                    style: TextStyle(fontSize: 11, color: Color(0xFF64748B), height: 1.2),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  // ==========================================
  // TAB 1: GROUP LIST (Percakapan Banyak Perangkat)
  // ==========================================
  Widget _buildGroupList(List<ChatRoom> groups) {
    return Column(
      children: [
        _buildGroupActionBar(),
        Expanded(
          child: groups.isEmpty
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 24),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(Icons.group_outlined, size: 48, color: Color(0xFFCBD5E1)),
                        const SizedBox(height: 12),
                        const Text(
                          'Belum Ada Grup Percakapan',
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.w700,
                            color: Color(0xFF334155),
                          ),
                        ),
                        const SizedBox(height: 4),
                        const Text(
                          'Buat grup baru atau gabung ke grup teman menggunakan tombol di atas',
                          textAlign: TextAlign.center,
                          style: TextStyle(fontSize: 12, color: Color(0xFF94A3B8)),
                        ),
                      ],
                    ),
                  ),
                )
              : ListView.separated(
                  padding: const EdgeInsets.only(top: 4, bottom: 90),
                  itemCount: groups.length,
                  separatorBuilder: (_, _) =>
                      const Divider(height: 1, indent: 76, color: Color(0xFFF1F5F9)),
                  itemBuilder: (context, index) {
                    final room = groups[index];
                    return ListTile(
                      onTap: () => _openChat(room),
                      onLongPress: () => _confirmDeleteRoom(room),
                      contentPadding:
                          const EdgeInsets.symmetric(horizontal: 20, vertical: 6),
                      leading: Container(
                        width: 48,
                        height: 48,
                        decoration: BoxDecoration(
                          color: room.avatarColor.withAlpha(28),
                          shape: BoxShape.circle,
                          border: Border.all(
                            color: room.avatarColor.withAlpha(70),
                            width: 1.5,
                          ),
                        ),
                        alignment: Alignment.center,
                        child: Text(
                          room.avatarEmoji,
                          style: const TextStyle(fontSize: 22),
                        ),
                      ),
                      title: Text(
                        room.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.w700,
                          color: Color(0xFF0F172A),
                        ),
                      ),
                      subtitle: Padding(
                        padding: const EdgeInsets.only(top: 3),
                        child: Text(
                          room.lastMessage.isNotEmpty
                              ? '~${room.lastMessageSender}: ${room.lastMessage}'
                              : 'Belum ada pesan',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 12.5,
                            color: Color(0xFF64748B),
                          ),
                        ),
                      ),
                      trailing: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: [
                          Text(
                            room.lastMessageTime,
                            style: const TextStyle(
                              fontSize: 11,
                              color: Color(0xFF94A3B8),
                              fontWeight: FontWeight.w500,
                            ),
                          ),
                          const SizedBox(height: 4),
                          if (room.unreadCount > 0)
                            Container(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 7, vertical: 2),
                              decoration: BoxDecoration(
                                color: const Color(0xFFDC2626), // Red pill as in reference
                                borderRadius: BorderRadius.circular(10),
                              ),
                              child: Text(
                                '${room.unreadCount}',
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 10,
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                            )
                          else
                            const SizedBox(height: 16),
                        ],
                      ),
                    );
                  },
                ),
        ),
      ],
    );
  }

  Widget _buildFriendsActionBar() {
    return Container(
      margin: const EdgeInsets.fromLTRB(16, 12, 16, 6),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: Colors.white,
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: const Color(0xFFE2E8F0)),
        boxShadow: [
          BoxShadow(
            color: Colors.black.withAlpha(5),
            blurRadius: 4,
            offset: const Offset(0, 2),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          ElevatedButton.icon(
            onPressed: _showAddFriendDialog,
            icon: const Icon(Icons.person_add_rounded, size: 18),
            label: const Text('Tambah Teman Baru (1-on-1)', style: TextStyle(fontSize: 12.5, fontWeight: FontWeight.bold)),
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFF0F172A),
              foregroundColor: Colors.white,
              elevation: 0,
              padding: const EdgeInsets.symmetric(vertical: 10),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
            ),
          ),
          const SizedBox(height: 8),
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
            decoration: BoxDecoration(
              color: const Color(0xFFF8FAFC),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Row(
              children: [
                const Icon(Icons.info_outline_rounded, size: 14, color: Color(0xFF64748B)),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    _profile != null
                        ? 'Login Anda: @${_profile!.username} • Obrolan privat 1-on-1 langsung via direct socket [DM:<teman>].'
                        : 'Percakapan privat 1-on-1 antar 2 orang langsung tersambung jika username aktif di jaringan.',
                    style: const TextStyle(fontSize: 11, color: Color(0xFF64748B), height: 1.2),
                  ),
                ),
              ],
            ),
          ),
          if (_onlineFriendCandidates.isNotEmpty) ...[
            Builder(
              builder: (context) {
                final newCandidates = _onlineFriendCandidates;
                if (newCandidates.isEmpty) return const SizedBox.shrink();

                return Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Row(
                    children: [
                      const Icon(Icons.wifi_tethering, size: 14, color: Color(0xFF10B981)),
                      const SizedBox(width: 5),
                      const Text(
                        'Online:',
                        style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.w700,
                            color: Color(0xFF0F172A)),
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: SingleChildScrollView(
                          scrollDirection: Axis.horizontal,
                          child: Row(
                            children: newCandidates.map((u) {
                              return Padding(
                                padding: const EdgeInsets.only(right: 6),
                                child: ActionChip(
                                  avatar: const Icon(Icons.person_add_alt_1,
                                      size: 13, color: Color(0xFF10B981)),
                                  label: Text(u,
                                      style: const TextStyle(
                                          fontSize: 11,
                                          fontWeight: FontWeight.w700)),
                                  visualDensity: VisualDensity.compact,
                                  backgroundColor: const Color(0xFFECFDF5),
                                  side: const BorderSide(
                                      color: Color(0xFFA7F3D0)),
                                  onPressed: () => _onAddFriend(u),
                                ),
                              );
                            }).toList(),
                          ),
                        ),
                      ),
                    ],
                  ),
                );
              },
            ),
          ],
        ],
      ),
    );
  }

  // ==========================================
  // TAB 2: FRIENDS LIST (Percakapan Privat 1-on-1)
  // ==========================================
  Widget _buildFriendsList(List<ChatRoom> friends) {
    return Column(
      children: [
        _buildFriendsActionBar(),
        Expanded(
          child: friends.isEmpty
              ? Center(
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 24),
                    child: Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(Icons.people_outline, size: 48, color: Color(0xFFCBD5E1)),
                        const SizedBox(height: 12),
                        const Text(
                          'Belum Ada Teman',
                          style: TextStyle(
                            fontSize: 16,
                            fontWeight: FontWeight.w700,
                            color: Color(0xFF334155),
                          ),
                        ),
                        const SizedBox(height: 4),
                        const Text(
                          'Tambah teman untuk memulai chat privat 1-on-1 (2 orang)',
                          textAlign: TextAlign.center,
                          style: TextStyle(fontSize: 12, color: Color(0xFF94A3B8)),
                        ),
                      ],
                    ),
                  ),
                )
              : ListView.separated(
                  padding: const EdgeInsets.only(top: 4, bottom: 90),
                  itemCount: friends.length,
                  separatorBuilder: (_, _) =>
                      const Divider(height: 1, indent: 76, color: Color(0xFFF1F5F9)),
                  itemBuilder: (context, index) {
                    final friend = friends[index];
                    return ListTile(
                      onTap: () => _openChat(friend),
                      onLongPress: () => _confirmDeleteRoom(friend),
                      contentPadding:
                          const EdgeInsets.symmetric(horizontal: 20, vertical: 6),
                      leading: Stack(
                        clipBehavior: Clip.none,
                        children: [
                          Container(
                            width: 48,
                            height: 48,
                            decoration: BoxDecoration(
                              color: friend.avatarColor.withAlpha(28),
                              shape: BoxShape.circle,
                              border: Border.all(
                                color: friend.avatarColor.withAlpha(70),
                                width: 1.5,
                              ),
                            ),
                            alignment: Alignment.center,
                            child: Text(
                              friend.avatarEmoji,
                              style: const TextStyle(fontSize: 22),
                            ),
                          ),
                          Positioned(
                            right: 0,
                            bottom: 0,
                            child: Container(
                              width: 12,
                              height: 12,
                              decoration: BoxDecoration(
                                color: const Color(0xFF10B981),
                                shape: BoxShape.circle,
                                border: Border.all(color: Colors.white, width: 2),
                              ),
                            ),
                          ),
                        ],
                      ),
                      title: Text(
                        friend.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.w700,
                          color: Color(0xFF0F172A),
                        ),
                      ),
                      subtitle: Padding(
                        padding: const EdgeInsets.only(top: 3),
                        child: Text(
                          friend.lastMessage.isNotEmpty
                              ? friend.lastMessage
                              : friend.statusText,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            fontSize: 12,
                            color: Color(0xFF64748B),
                          ),
                        ),
                      ),
                      trailing: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          if (friend.unreadCount > 0) ...[
                            Container(
                              padding:
                                  const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                              decoration: BoxDecoration(
                                color: const Color(0xFFDC2626),
                                borderRadius: BorderRadius.circular(10),
                              ),
                              child: Text(
                                '${friend.unreadCount}',
                                style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 10,
                                  fontWeight: FontWeight.w700,
                                ),
                              ),
                            ),
                            const SizedBox(width: 8),
                          ],
                          SizedBox(
                            height: 32,
                            child: OutlinedButton(
                              onPressed: () => _openChat(friend),
                              style: OutlinedButton.styleFrom(
                                side: const BorderSide(color: Color(0xFFE2E8F0)),
                                shape: RoundedRectangleBorder(
                                  borderRadius: BorderRadius.circular(16),
                                ),
                                padding: const EdgeInsets.symmetric(horizontal: 14),
                                visualDensity: VisualDensity.compact,
                              ),
                              child: const Text(
                                'Chat',
                                style: TextStyle(
                                  fontSize: 12,
                                  fontWeight: FontWeight.w600,
                                  color: Color(0xFF0F172A),
                                ),
                              ),
                            ),
                          ),
                        ],
                      ),
                    );
                  },
                ),
        ),
      ],
    );
  }

  // ==========================================
  // BOTTOM NAVIGATION BAR
  // ==========================================
  Widget _buildBottomNav(bool isTcp, Color protoColor) {
    return Container(
      decoration: const BoxDecoration(
        color: Colors.white,
        border: Border(
          top: BorderSide(color: Color(0xFFE2E8F0), width: 1),
        ),
      ),
      child: SafeArea(
        top: false,
        child: SizedBox(
          height: 60,
          child: Row(
            mainAxisAlignment: MainAxisAlignment.spaceAround,
            children: [
              // 1. Home / DualChat
              _buildNavItem(
                icon: Icons.chat_bubble_outline,
                activeIcon: Icons.chat_bubble,
                label: 'DualChat',
                isSelected: _currentNavIndex == 0,
                onTap: () => setState(() => _currentNavIndex = 0),
              ),

              // 2. TCP Mode Switcher
              _buildNavItem(
                icon: Icons.lock_outline,
                activeIcon: Icons.lock,
                label: 'TCP Mode',
                isSelected: isTcp,
                activeColor: const Color(0xFF10B981),
                onTap: () {
                  _switchProtocol(TransportProto.tcp);
                  if (_currentNavIndex != 0) {
                    setState(() => _currentNavIndex = 0);
                  }
                },
              ),

              // 3. UDP Mode Switcher
              _buildNavItem(
                icon: Icons.bolt_outlined,
                activeIcon: Icons.bolt,
                label: 'UDP Mode',
                isSelected: !isTcp,
                activeColor: const Color(0xFFF59E0B),
                onTap: () {
                  _switchProtocol(TransportProto.udp);
                  if (_currentNavIndex != 0) {
                    setState(() => _currentNavIndex = 0);
                  }
                },
              ),

              // 4. Profile & Telemetry
              _buildNavItem(
                icon: Icons.person_outline,
                activeIcon: Icons.person,
                label: 'Profile',
                isSelected: _currentNavIndex == 3,
                onTap: () => setState(() => _currentNavIndex = 3),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildNavItem({
    required IconData icon,
    required IconData activeIcon,
    required String label,
    required bool isSelected,
    Color activeColor = const Color(0xFF0F172A),
    required VoidCallback onTap,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              isSelected ? activeIcon : icon,
              size: 22,
              color: isSelected ? activeColor : const Color(0xFF94A3B8),
            ),
            const SizedBox(height: 2),
            Text(
              label,
              style: TextStyle(
                fontSize: 10,
                fontWeight: isSelected ? FontWeight.w700 : FontWeight.w500,
                color: isSelected ? activeColor : const Color(0xFF94A3B8),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
