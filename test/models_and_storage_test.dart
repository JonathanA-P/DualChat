import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:jarkom_project/models/chat_message.dart';
import 'package:jarkom_project/models/chat_room.dart';
import 'package:jarkom_project/models/feed_post.dart';
import 'package:jarkom_project/services/chat_storage_service.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  setUp(() {
    SharedPreferences.setMockInitialValues({});
  });

  group('FeedPost & FeedComment Model Tests', () {
    test('FeedComment serialization and wire payload formatting', () {
      final comment = FeedComment(
        id: 'c_1',
        author: 'rudy',
        authorName: 'Rudy H',
        avatarEmoji: '⚽',
        text: 'Handshake TCP terverifikasi',
        timeAgo: '5m ago',
      );

      final json = comment.toJson();
      expect(json['id'], 'c_1');
      expect(json['author'], 'rudy');
      expect(json['authorName'], 'Rudy H');
      expect(json['text'], 'Handshake TCP terverifikasi');

      final fromJson = FeedComment.fromJson(json);
      expect(fromJson.id, 'c_1');
      expect(fromJson.author, 'rudy');
      expect(fromJson.text, 'Handshake TCP terverifikasi');

      // Wire payload
      final wire = FeedComment.formatWirePayload('post_1', 'Mantap bro');
      expect(wire, '[FEED_COMMENT:post_1] Mantap bro');

      final parsed = FeedComment.parseWirePayload(wire);
      expect(parsed, isNotNull);
      expect(parsed!['postId'], 'post_1');
      expect(parsed['text'], 'Mantap bro');
    });

    test('FeedPost comments list, like toggle, and serialization roundtrip', () {
      final post = FeedPost(
        id: 'post_test',
        author: 'alice',
        authorName: 'Alice W',
        avatarEmoji: '👩',
        avatarColor: const Color(0xFF10B981),
        timeAgo: 'Barusan',
        content: 'Testing feed broadcast',
        likes: 5,
        isLiked: false,
        proto: TransportProto.tcp,
      );

      expect(post.comments.length, 0);
      expect(post.commentsCount, 0);

      // Add comment
      post.addComment(FeedComment(
        id: 'c_test_1',
        author: 'bob',
        authorName: 'Bob M',
        text: 'Nice packet capture!',
      ));
      expect(post.comments.length, 1);
      expect(post.commentsCount, 1);

      // Toggle like
      post.toggleLike();
      expect(post.isLiked, isTrue);
      expect(post.likes, 6);

      post.toggleLike();
      expect(post.isLiked, isFalse);
      expect(post.likes, 5);

      // Roundtrip JSON
      final json = post.toJson();
      expect(json['commentsCount'], 1);
      expect((json['comments'] as List).length, 1);

      final restored = FeedPost.fromJson(json);
      expect(restored.id, 'post_test');
      expect(restored.content, 'Testing feed broadcast');
      expect(restored.comments.length, 1);
      expect(restored.comments.first.text, 'Nice packet capture!');
    });
  });

  group('ChatRoom Media Preview Formatting Tests', () {
    test('Image payload formatted to 📷 Foto: <title>', () {
      const imgPayload = '[IMAGE:topology:Topologi Jaringan DualChat:🖥️:Arsitektur sistem]';
      final preview = ChatRoom.formatMessagePreview(imgPayload);
      expect(preview, '📷 Foto: Topologi Jaringan DualChat');
    });

    test('Voice Note payload formatted to 🎙️ Pesan Suara (<duration>s)', () {
      const voicePayload = '[VOICE:7:12,18,25,30]';
      final preview = ChatRoom.formatMessagePreview(voicePayload);
      expect(preview, '🎙️ Pesan Suara (7s)');
    });

    test('Feed payload formatted to preview text', () {
      const feedPayload = '[FEED] Halo rekan-rekan!';
      final preview = ChatRoom.formatMessagePreview(feedPayload);
      expect(preview, '📰 Feed: Halo rekan-rekan!');
    });

    test('addMessage updates lastMessage with formatted preview', () {
      final room = ChatRoom(
        id: 'room_test',
        name: 'Test Room',
        isGroup: false,
        members: ['alice', 'bob'],
        avatarEmoji: '💬',
        avatarColor: const Color(0xFF0F172A),
        statusText: 'Online',
        lastMessage: 'Halo',
        lastMessageSender: 'alice',
        lastMessageTime: '10:00',
      );

      room.addMessage(ChatMessage(
        type: MessageType.msg,
        seq: 1,
        ts: 1700000000.0,
        user: 'bob',
        data: '[VOICE:4:10,20,30]',
      ));

      expect(room.lastMessage, '🎙️ Pesan Suara (4s)');
      expect(room.lastMessagePreview, '🎙️ Pesan Suara (4s)');
      expect(room.lastMessageSender, 'bob');
    });
  });

  group('ChatStorageService Feed & Likes Persistence Tests', () {
    test('saveFeedPosts and loadFeedPosts work roundtrip', () async {
      final storage = ChatStorageService.instance;

      final posts = [
        FeedPost(
          id: 'p_save_1',
          author: 'charlie',
          authorName: 'Charlie Brown',
          avatarEmoji: '👨',
          avatarColor: const Color(0xFFF59E0B),
          timeAgo: '10m ago',
          content: 'Uji coba penyimpanan SharedPreferences',
          likes: 3,
          isLiked: true,
          proto: TransportProto.udp,
          comments: [
            FeedComment(
              id: 'c_s1',
              author: 'dave',
              authorName: 'Dave',
              text: 'Tersimpan dengan baik',
            ),
          ],
        ),
      ];

      await storage.saveFeedPosts(posts);
      final loaded = await storage.loadFeedPosts();

      expect(loaded.isNotEmpty, isTrue);
      final found = loaded.firstWhere((p) => p.id == 'p_save_1');
      expect(found.author, 'charlie');
      expect(found.content, 'Uji coba penyimpanan SharedPreferences');
      expect(found.isLiked, isTrue);
      expect(found.comments.length, 1);
      expect(found.comments.first.text, 'Tersimpan dengan baik');
    });

    test('saveLikes, loadLikes, and setPostLiked work roundtrip', () async {
      final storage = ChatStorageService.instance;

      await storage.saveLikes({'post_1': true, 'post_2': false});
      var likes = await storage.loadLikes();
      expect(likes['post_1'], isTrue);
      expect(likes['post_2'], isFalse);

      await storage.setPostLiked('post_2', true);
      expect(await storage.isPostLiked('post_2'), isTrue);

      await storage.clearAll();
      likes = await storage.loadLikes();
      expect(likes.isEmpty, isTrue);
    });
  });
}
