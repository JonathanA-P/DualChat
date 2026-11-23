import 'package:flutter/material.dart';
import 'chat_message.dart';

class ChatRoom extends ChangeNotifier {
  final String id;
  String name;
  final bool isGroup; // false: Friends (2 orang), true: Group (>2 orang)
  final List<String> members;
  final String avatarEmoji;
  final Color avatarColor;
  String statusText;
  String lastMessage;
  String lastMessageSender;
  String lastMessageTime;
  int unreadCount;
  final List<ChatMessage> messages;

  ChatRoom({
    required this.id,
    required this.name,
    required this.isGroup,
    required this.members,
    required this.avatarEmoji,
    required this.avatarColor,
    required this.statusText,
    required this.lastMessage,
    required this.lastMessageSender,
    required this.lastMessageTime,
    this.unreadCount = 0,
    List<ChatMessage>? initialMessages,
  }) : messages = initialMessages ?? [];

  int get memberCount => members.length;

  /// Formats raw message payloads (e.g. [IMAGE:...], [VOICE:...], [FEED]) into human-readable preview text
  static String formatMessagePreview(String rawMessage) {
    final trimmed = rawMessage.trim();
    if (trimmed.startsWith('[VOICE:')) {
      final match = RegExp(r'^\[VOICE:(\d+)(?::[^\]]*)?\]').firstMatch(trimmed);
      if (match != null) {
        final duration = match.group(1);
        return '🎙️ Pesan Suara (${duration}s)';
      }
      return '🎙️ Pesan Suara';
    }
    if (trimmed.startsWith('[IMG_DATA:')) {
      final match = RegExp(r'^\[IMG_DATA:([^:]+):').firstMatch(trimmed);
      if (match != null) {
        final fileName = match.group(1)?.trim() ?? '';
        return fileName.isNotEmpty ? '📷 Foto: $fileName' : '📷 Foto';
      }
      return '📷 Foto';
    }
    if (trimmed.startsWith('[IMAGE:')) {
      final match = RegExp(r'^\[IMAGE:([^:]+):([^:]+):([^:]*):?(.*)\]').firstMatch(trimmed);
      if (match != null) {
        final title = match.group(2)?.trim() ?? '';
        return title.isNotEmpty ? '📷 Foto: $title' : '📷 Foto';
      }
      return '📷 Foto';
    }
    if (trimmed.startsWith('[FEED]')) {
      final text = trimmed.substring(6).trim();
      return text.isNotEmpty ? '📰 Feed: $text' : '📰 Postingan Feed';
    }
    if (trimmed.startsWith('[FEED_COMMENT:')) {
      return '💬 Komentar Feed';
    }
    return rawMessage;
  }

  String get lastMessagePreview => formatMessagePreview(lastMessage);

  void addMessage(ChatMessage msg) {
    messages.add(msg);
    lastMessage = formatMessagePreview(msg.data);
    lastMessageSender = msg.user;
    final dt = DateTime.fromMillisecondsSinceEpoch((msg.ts * 1000).toInt());
    lastMessageTime =
        '${dt.hour.toString().padLeft(2, '0')}:${dt.minute.toString().padLeft(2, '0')}';
    notifyListeners();
  }

  void markAsRead() {
    if (unreadCount > 0) {
      unreadCount = 0;
      notifyListeners();
    }
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'isGroup': isGroup,
        'members': members,
        'avatarEmoji': avatarEmoji,
        'avatarColor': avatarColor.value,
        'statusText': statusText,
        'lastMessage': lastMessage,
        'lastMessageSender': lastMessageSender,
        'lastMessageTime': lastMessageTime,
        'unreadCount': unreadCount,
        'messages': messages.map((m) => m.toJson()).toList(),
      };

  factory ChatRoom.fromJson(Map<String, dynamic> json) {
    final rawMessages = json['messages'] as List<dynamic>? ?? [];
    return ChatRoom(
      id: json['id']?.toString() ?? '',
      name: json['name']?.toString() ?? '',
      isGroup: json['isGroup'] == true,
      members: (json['members'] as List<dynamic>? ?? [])
          .map((e) => e.toString())
          .toList(),
      avatarEmoji: json['avatarEmoji']?.toString() ?? '💬',
      avatarColor: Color((json['avatarColor'] as num?)?.toInt() ?? 0xFF0F172A),
      statusText: json['statusText']?.toString() ?? '',
      lastMessage: formatMessagePreview(json['lastMessage']?.toString() ?? ''),
      lastMessageSender: json['lastMessageSender']?.toString() ?? '',
      lastMessageTime: json['lastMessageTime']?.toString() ?? '',
      unreadCount: (json['unreadCount'] as num?)?.toInt() ?? 0,
      initialMessages: rawMessages
          .map((m) => ChatMessage.fromJson(m as Map<String, dynamic>))
          .toList(),
    );
  }

  /// Initial room setup without fake dummy messages or dummy users.
  /// Only contains the standard public room for network broadcasts.
  static List<ChatRoom> initialRooms(String currentUserName) {
    return [
      ChatRoom(
        id: 'group_general',
        name: 'General Broadcast',
        isGroup: true,
        members: [currentUserName, 'server'],
        avatarEmoji: '🌐',
        avatarColor: const Color(0xFF0F172A),
        statusText: 'Ruang publik server (TCP/UDP)',
        lastMessage: 'Ruang obrolan publik siap digunakan',
        lastMessageSender: 'server',
        lastMessageTime: 'Now',
        unreadCount: 0,
        initialMessages: [],
      ),
    ];
  }
}
