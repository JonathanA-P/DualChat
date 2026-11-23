import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarkom_project/models/chat_message.dart';
import 'package:jarkom_project/models/chat_room.dart';
import 'package:jarkom_project/models/feed_post.dart';
import 'package:jarkom_project/services/chat_storage_service.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  group('DualChat Real Features Unit & Routing Tests', () {
    test('Friend routing [DM:<recipient>] processes for recipient and ignores other users', () {
      final myUsername = 'alice';
      final rooms = <ChatRoom>[];

      // Helper function matching _onIncomingMessage routing logic
      void routeIncoming(ChatMessage msg) {
        if (msg.user.toLowerCase() == myUsername.toLowerCase()) return;

        final dmMatch = RegExp(r'^\[DM:([^\]]+)\]\s*(.*)$').firstMatch(msg.data);
        if (dmMatch != null) {
          final toUser = dmMatch.group(1)?.trim().toLowerCase() ?? '';
          final cleanText = dmMatch.group(2) ?? '';

          if (toUser == myUsername.toLowerCase()) {
            ChatRoom? targetRoom;
            for (final r in rooms) {
              if (!r.isGroup && r.members.any((m) => m.toLowerCase() == msg.user.toLowerCase())) {
                targetRoom = r;
                break;
              }
            }
            if (targetRoom == null) {
              targetRoom = ChatRoom(
                id: 'friend_${msg.user}',
                name: msg.user,
                isGroup: false,
                members: [myUsername, msg.user],
                avatarEmoji: '👤',
                avatarColor: const Color(0xFF10B981),
                statusText: 'Active barusan',
                lastMessage: cleanText,
                lastMessageSender: msg.user,
                lastMessageTime: 'Now',
              );
              rooms.insert(0, targetRoom);
            }
            targetRoom.addMessage(ChatMessage(
              type: msg.type,
              seq: msg.seq,
              ts: msg.ts,
              user: msg.user,
              data: cleanText,
            ));
          }
        }
      }

      // Case 1: Bob sends DM to Alice -> Should be delivered into Friends room
      final msgForAlice = ChatMessage(
        type: MessageType.msg,
        seq: 1,
        ts: 100.0,
        user: 'bob',
        data: '[DM:alice] Halo Alice dari Bob!',
      );
      routeIncoming(msgForAlice);

      expect(rooms.length, 1);
      expect(rooms[0].isGroup, isFalse);
      expect(rooms[0].name, 'bob');
      expect(rooms[0].members, containsAll(['alice', 'bob']));
      expect(rooms[0].lastMessage, 'Halo Alice dari Bob!');

      // Case 2: Charlie sends DM to Bob -> Should be ignored by Alice
      final msgForBob = ChatMessage(
        type: MessageType.msg,
        seq: 2,
        ts: 101.0,
        user: 'charlie',
        data: '[DM:bob] Pesan rahasia ke Bob',
      );
      routeIncoming(msgForBob);

      // Room count should still be 1 (Charlie's message ignored)
      expect(rooms.length, 1);
      expect(rooms[0].messages.length, 1);
    });

    test('Group routing [GROUP:<name>] delivers to group members', () {
      final myUsername = 'alice';
      final rooms = <ChatRoom>[
        ChatRoom(
          id: 'group_jarkom',
          name: 'Kelompok Jarkom',
          isGroup: true,
          members: ['alice', 'bob'],
          avatarEmoji: '👥',
          avatarColor: const Color(0xFF6366F1),
          statusText: 'Grup Jaringan',
          lastMessage: '',
          lastMessageSender: '',
          lastMessageTime: '',
        )
      ];

      void routeGroupMessage(ChatMessage msg) {
        if (msg.user.toLowerCase() == myUsername.toLowerCase()) return;

        final groupMatch = RegExp(r'^\[GROUP:([^\]]+)\]\s*(.*)$').firstMatch(msg.data);
        if (groupMatch != null) {
          final groupName = groupMatch.group(1)?.trim() ?? '';
          final cleanText = groupMatch.group(2) ?? '';

          ChatRoom? targetRoom;
          for (final r in rooms) {
            if (r.isGroup && r.name.toLowerCase() == groupName.toLowerCase()) {
              targetRoom = r;
              break;
            }
          }

          if (targetRoom != null) {
            if (!targetRoom.members.contains(msg.user)) {
              targetRoom.members.add(msg.user);
            }
            targetRoom.addMessage(ChatMessage(
              type: msg.type,
              seq: msg.seq,
              ts: msg.ts,
              user: msg.user,
              data: cleanText,
            ));
          }
        }
      }

      final groupMsg = ChatMessage(
        type: MessageType.msg,
        seq: 10,
        ts: 200.0,
        user: 'charlie',
        data: '[GROUP:Kelompok Jarkom] Selamat siang rekan semua',
      );
      routeGroupMessage(groupMsg);

      expect(rooms[0].messages.length, 1);
      expect(rooms[0].messages.first.data, 'Selamat siang rekan semua');
      expect(rooms[0].lastMessage, 'Selamat siang rekan semua');
      expect(rooms[0].members, contains('charlie'));
    });

    test('Feed [FEED] broadcast and [FEED_COMMENT:<postId>] parsing and models', () {
      // 1. FeedPost model with comments
      final post = FeedPost(
        id: 'post_test',
        author: 'alice',
        authorName: 'Alice Smith',
        avatarEmoji: '👩‍💻',
        avatarColor: const Color(0xFF10B981),
        timeAgo: 'Barusan',
        content: 'Pengujian jaringan DualChat berjalan lancar!',
        likes: 5,
        isLiked: false,
        proto: TransportProto.tcp,
      );

      expect(post.commentsCount, 0);
      expect(post.isLiked, isFalse);

      post.toggleLike();
      expect(post.isLiked, isTrue);
      expect(post.likes, 6);

      // 2. FeedComment creation & addition
      final comment = FeedComment(
        id: 'c_1',
        author: 'bob',
        authorName: 'Bob Builder',
        avatarEmoji: '👨‍🔧',
        text: 'Mantap, TCP handshake terkonfirmasi!',
        timeAgo: '1 Menit Lalu',
      );
      post.addComment(comment);

      expect(post.commentsCount, 1);
      expect(post.comments.first.author, 'bob');
      expect(post.comments.first.text, 'Mantap, TCP handshake terkonfirmasi!');

      // 3. Serialization and Deserialization
      final postJson = post.toJson();
      final restoredPost = FeedPost.fromJson(postJson);
      expect(restoredPost.id, 'post_test');
      expect(restoredPost.likes, 6);
      expect(restoredPost.isLiked, isTrue);
      expect(restoredPost.commentsCount, 1);
      expect(restoredPost.comments.first.text, 'Mantap, TCP handshake terkonfirmasi!');

      // 4. Wire formatting and parsing helpers
      final wireComment = FeedComment.formatWirePayload('post_test', 'Komentar baru');
      expect(wireComment, '[FEED_COMMENT:post_test] Komentar baru');

      final parsed = FeedComment.parseWirePayload(wireComment);
      expect(parsed, isNotNull);
      expect(parsed!['postId'], 'post_test');
      expect(parsed['text'], 'Komentar baru');
    });

    test('ChatRoom formatMessagePreview handles Image, Voice, Feed, and Text previews', () {
      // Image preview
      final imgPreview = ChatRoom.formatMessagePreview('[IMAGE:topology:Topologi Mesh:🌐:Deskripsi topologi]');
      expect(imgPreview, '📷 Foto: Topologi Mesh');

      // Voice preview
      final voicePreview = ChatRoom.formatMessagePreview('[VOICE:8:1.0,2.5,3.0]');
      expect(voicePreview, '🎙️ Pesan Suara (8s)');

      // Feed preview
      final feedPreview = ChatRoom.formatMessagePreview('[FEED] Update terkini jaringan');
      expect(feedPreview, '📰 Feed: Update terkini jaringan');

      // Feed comment preview
      final commentPreview = ChatRoom.formatMessagePreview('[FEED_COMMENT:post_1] Halo');
      expect(commentPreview, '💬 Komentar Feed');

      // Regular text
      final plainPreview = ChatRoom.formatMessagePreview('Halo kawan');
      expect(plainPreview, 'Halo kawan');
    });

    test('Media payloads stay within 8192 bytes limit and encode/decode safely', () {
      final largeCaption = 'A' * 5000;
      final imagePayload = '[IMAGE:diag:Diagram Besar:🖼️:$largeCaption]';
      final imageMsg = ChatMessage(
        type: MessageType.msg,
        seq: 50,
        ts: 300.0,
        user: 'alice',
        data: imagePayload,
      );

      final wireJson = imageMsg.toWireJson();
      final byteLen = utf8.encode(wireJson).length;
      expect(byteLen, lessThan(8192));

      final decoded = ChatMessage.fromJson(jsonDecode(wireJson));
      expect(decoded.data, imagePayload);
      expect(decoded.seq, 50);

      // Voice note payload
      final samples = List.generate(30, (i) => (i % 5).toDouble()).join(',');
      final voicePayload = '[VOICE:15:$samples]';
      final voiceMsg = ChatMessage(
        type: MessageType.msg,
        seq: 51,
        ts: 301.0,
        user: 'bob',
        data: voicePayload,
      );

      final voiceWire = voiceMsg.toWireJson();
      final decodedVoice = ChatMessage.fromJson(jsonDecode(voiceWire));
      expect(decodedVoice.data, voicePayload);
    });

    test('ChatStorageService persists and loads FeedPosts and Likes', () async {
      final storage = ChatStorageService.instance;
      final testPosts = [
        FeedPost(
          id: 'test_persist_1',
          author: 'alice',
          authorName: 'Alice',
          avatarEmoji: '👩‍💻',
          avatarColor: const Color(0xFF10B981),
          timeAgo: 'Barusan',
          content: 'Testing storage',
          likes: 3,
          isLiked: true,
          proto: TransportProto.tcp,
        )
      ];

      await storage.saveFeedPosts(testPosts);
      final loaded = await storage.loadFeedPosts();
      expect(loaded.any((p) => p.id == 'test_persist_1'), isTrue);

      final savedPost = loaded.firstWhere((p) => p.id == 'test_persist_1');
      expect(savedPost.author, 'alice');
      expect(savedPost.content, 'Testing storage');
    });
  });
}
