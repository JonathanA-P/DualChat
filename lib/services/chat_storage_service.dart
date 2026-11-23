import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/chat_room.dart';
import '../models/feed_post.dart';

class ChatStorageService {
  static final ChatStorageService instance = ChatStorageService._internal();
  ChatStorageService._internal();

  static const String _keyRooms = 'dualchat_rooms_data';
  static const String _keyFeedPosts = 'dualchat_feed_posts';
  static const String _keyFeedLikes = 'dualchat_feed_likes';

  File _getStorageFile(String filename) {
    try {
      final dir = Directory.systemTemp;
      return File('${dir.path}/$filename');
    } catch (_) {
      return File(filename);
    }
  }

  // ==========================================
  // ROOMS PERSISTENCE
  // ==========================================

  Future<List<ChatRoom>> loadRooms(String currentUserName) async {
    String? content;
    try {
      final prefs = await SharedPreferences.getInstance();
      content = prefs.getString(_keyRooms);
    } catch (_) {}

    if (content == null || content.trim().isEmpty) {
      try {
        final file = _getStorageFile('dualchat_rooms_data.json');
        if (await file.exists()) {
          content = await file.readAsString();
        }
      } catch (_) {}
    }

    if (content != null && content.trim().isNotEmpty) {
      try {
        final list = jsonDecode(content) as List<dynamic>;
        final rawRooms = list
            .map((item) => ChatRoom.fromJson(item as Map<String, dynamic>))
            .toList();

        // 1. Filter out legacy dummy template rooms
        final filtered = rawRooms.where((r) {
          final isDummy = r.id == 'friend_gretchen' ||
              r.id == 'group_football' ||
              r.name == 'Gretchen Culhane' ||
              r.name == 'Football Fanatics';
          return !isDummy;
        }).toList();

        // 2. Deduplicate rooms by (isGroup + lowerName)
        final seenKeys = <String>{};
        final cleanRooms = <ChatRoom>[];
        for (final r in filtered) {
          final key = '${r.isGroup}_${r.name.trim().toLowerCase()}';
          if (!seenKeys.contains(key)) {
            seenKeys.add(key);
            cleanRooms.add(r);
          }
        }

        // 3. Ensure standard General Broadcast exists
        final hasGeneral = cleanRooms.any((r) => r.id == 'group_general');
        if (!hasGeneral) {
          cleanRooms.insert(
            0,
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
          );
        }

        if (cleanRooms.isNotEmpty) {
          return cleanRooms;
        }
      } catch (e) {
        if (kDebugMode) {
          print('ChatStorageService: Error parsing rooms: $e');
        }
      }
    }
    return ChatRoom.initialRooms(currentUserName);
  }

  Future<void> saveRooms(List<ChatRoom> rooms) async {
    final data = jsonEncode(rooms.map((r) => r.toJson()).toList());
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_keyRooms, data);
    } catch (_) {}

    try {
      final file = _getStorageFile('dualchat_rooms_data.json');
      await file.writeAsString(data, flush: true);
    } catch (e) {
      if (kDebugMode) {
        print('ChatStorageService: Error saving rooms: $e');
      }
    }
  }

  // ==========================================
  // FEED POSTS PERSISTENCE (SharedPreferences + File)
  // ==========================================

  Future<void> saveFeedPosts(List<FeedPost> posts) async {
    final data = jsonEncode(posts.map((p) => p.toJson()).toList());
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_keyFeedPosts, data);
    } catch (_) {}

    try {
      final file = _getStorageFile('dualchat_feed_posts.json');
      await file.writeAsString(data, flush: true);
    } catch (e) {
      if (kDebugMode) {
        print('ChatStorageService: Error saving feed posts: $e');
      }
    }
  }

  Future<List<FeedPost>> loadFeedPosts() async {
    String? content;
    try {
      final prefs = await SharedPreferences.getInstance();
      content = prefs.getString(_keyFeedPosts);
    } catch (_) {}

    if (content == null || content.trim().isEmpty) {
      try {
        final file = _getStorageFile('dualchat_feed_posts.json');
        if (await file.exists()) {
          content = await file.readAsString();
        }
      } catch (_) {}
    }

    if (content != null && content.trim().isNotEmpty) {
      try {
        final list = jsonDecode(content) as List<dynamic>;
        final posts = list
            .map((item) => FeedPost.fromJson(item as Map<String, dynamic>))
            .toList();
        if (posts.isNotEmpty) {
          return posts;
        }
      } catch (e) {
        if (kDebugMode) {
          print('ChatStorageService: Error parsing feed posts: $e');
        }
      }
    }
    return FeedPost.initialPosts();
  }

  // ==========================================
  // LIKES PERSISTENCE (SharedPreferences + File)
  // ==========================================

  Future<void> saveLikes(Map<String, bool> likes) async {
    final data = jsonEncode(likes);
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_keyFeedLikes, data);
    } catch (_) {}

    try {
      final file = _getStorageFile('dualchat_feed_likes.json');
      await file.writeAsString(data, flush: true);
    } catch (e) {
      if (kDebugMode) {
        print('ChatStorageService: Error saving likes: $e');
      }
    }
  }

  Future<Map<String, bool>> loadLikes() async {
    String? content;
    try {
      final prefs = await SharedPreferences.getInstance();
      content = prefs.getString(_keyFeedLikes);
    } catch (_) {}

    if (content == null || content.trim().isEmpty) {
      try {
        final file = _getStorageFile('dualchat_feed_likes.json');
        if (await file.exists()) {
          content = await file.readAsString();
        }
      } catch (_) {}
    }

    if (content != null && content.trim().isNotEmpty) {
      try {
        final map = jsonDecode(content) as Map<String, dynamic>;
        return map.map((k, v) => MapEntry(k, v == true));
      } catch (e) {
        if (kDebugMode) {
          print('ChatStorageService: Error parsing likes: $e');
        }
      }
    }
    return {};
  }

  Future<void> setPostLiked(String postId, bool isLiked) async {
    final likes = await loadLikes();
    likes[postId] = isLiked;
    await saveLikes(likes);
  }

  Future<bool> isPostLiked(String postId) async {
    final likes = await loadLikes();
    return likes[postId] ?? false;
  }

  // ==========================================
  // CLEANUP
  // ==========================================

  Future<void> clearAll() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_keyRooms);
      await prefs.remove(_keyFeedPosts);
      await prefs.remove(_keyFeedLikes);
    } catch (_) {}

    for (final name in [
      'dualchat_rooms_data.json',
      'dualchat_feed_posts.json',
      'dualchat_feed_likes.json',
    ]) {
      try {
        final file = _getStorageFile(name);
        if (await file.exists()) {
          await file.delete();
        }
      } catch (_) {}
    }
  }
}
