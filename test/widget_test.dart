import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarkom_project/main.dart';
import 'package:jarkom_project/models/chat_message.dart';
import 'package:jarkom_project/models/chat_room.dart';
import 'package:jarkom_project/models/user_profile.dart';

void main() {
  testWidgets('DualChatApp UI render and protocol tabs test', (WidgetTester tester) async {
    final testProfile = UserProfile(
      name: 'Jonathan',
      username: 'jonathan',
      avatarIndex: 0,
      createdAt: DateTime.now(),
    );

    await tester.pumpWidget(DualChatApp(initialProfile: testProfile));
    await tester.pump();

    // Verify app title is "DualChat" (replacing Community as requested)
    expect(find.text('DualChat'), findsWidgets);

    // Verify tabs: Grup (>2 orang) and Teman (2 orang) - Feed removed
    expect(find.text('Grup'), findsOneWidget);
    expect(find.text('Teman'), findsOneWidget);
    expect(find.text('Buat Grup'), findsOneWidget);
    expect(find.text('Gabung Grup'), findsOneWidget);

    // Verify bottom navigation items for TCP and UDP mode switching
    expect(find.text('TCP Mode'), findsOneWidget);
    expect(find.text('UDP Mode'), findsOneWidget);
    expect(find.text('Profile'), findsOneWidget);
  });

  test('ChatMessage serialization test', () {
    final msg = ChatMessage(
      type: MessageType.msg,
      seq: 5,
      ts: 1700000000.123,
      user: 'alice',
      data: 'halo test',
    );

    final jsonMap = msg.toJson();
    expect(jsonMap['type'], 'MSG');
    expect(jsonMap['seq'], 5);
    expect(jsonMap['user'], 'alice');
    expect(jsonMap['data'], 'halo test');

    final wire = msg.toWireJson();
    expect(wire, contains('"type":"MSG"'));
    expect(wire, contains('"seq":5'));
  });

  test('Real ChatRoom Friends (2 orang) vs Group (>2 orang) test', () {
    // Real 1-on-1 Friend room (2 people)
    final friendRoom = ChatRoom(
      id: 'friend_alice',
      name: 'alice',
      isGroup: false,
      members: ['jonathan', 'alice'],
      avatarEmoji: '👤',
      avatarColor: const Color(0xFF10B981),
      statusText: 'Active',
      lastMessage: 'Halo',
      lastMessageSender: 'alice',
      lastMessageTime: '12:00',
    );
    expect(friendRoom.isGroup, isFalse);
    expect(friendRoom.members.length, equals(2));

    // Real Group room (>2 people)
    final groupRoom = ChatRoom(
      id: 'group_jarkom',
      name: 'Kelompok Jarkom',
      isGroup: true,
      members: ['jonathan', 'alice', 'bob'],
      avatarEmoji: '👥',
      avatarColor: const Color(0xFF6366F1),
      statusText: 'Grup Jaringan',
      lastMessage: 'Siap',
      lastMessageSender: 'bob',
      lastMessageTime: '12:01',
    );
    expect(groupRoom.isGroup, isTrue);
    expect(groupRoom.members.length, greaterThan(2));

    // Serialization & Deserialization
    final json = groupRoom.toJson();
    final restored = ChatRoom.fromJson(json);
    expect(restored.name, 'Kelompok Jarkom');
    expect(restored.isGroup, isTrue);
    expect(restored.members.length, 3);
  });

  test('UserProfile model serialization test', () {
    final profile = UserProfile(
      name: 'Jonathan',
      username: 'jonathan',
      avatarIndex: 2,
      status: 'Online',
      host: '127.0.0.1',
      tcpPort: 5000,
      udpPort: 5001,
      createdAt: DateTime.now(),
    );

    final json = profile.toJson();
    expect(json['name'], 'Jonathan');
    expect(json['username'], 'jonathan');
    expect(json['tcpPort'], 5000);
    expect(json['udpPort'], 5001);

    final fromJson = UserProfile.fromJson(json);
    expect(fromJson.name, profile.name);
    expect(fromJson.username, profile.username);
  });
}
