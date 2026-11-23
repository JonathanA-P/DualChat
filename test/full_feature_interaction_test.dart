import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:jarkom_project/main.dart';
import 'package:jarkom_project/models/user_profile.dart';

void main() {
  testWidgets('Complete End-to-End User Interaction across all Features and Navigation',
      (WidgetTester tester) async {
    // 1. Initialize user profile
    final testProfile = UserProfile(
      name: 'Jonathan Tester',
      username: 'jonathan',
      avatarIndex: 1,
      host: '127.0.0.1',
      tcpPort: 5000,
      udpPort: 5001,
      status: 'Sedang menguji jaringan...',
      createdAt: DateTime.now(),
    );

    await tester.pumpWidget(DualChatApp(initialProfile: testProfile));
    await tester.pumpAndSettle();

    // 2. Verify Home Screen Components
    expect(find.text('DualChat'), findsWidgets);
    expect(find.text('Grup'), findsOneWidget);
    expect(find.text('Teman'), findsOneWidget);
    expect(find.text('Buat Grup'), findsWidgets);
    expect(find.text('Gabung Grup'), findsWidgets);

    // 3. Test Protocol Toggle Badge in Header
    final tcpBadge = find.text('TCP');
    expect(tcpBadge, findsWidgets);
    await tester.tap(tcpBadge.first);
    await tester.pumpAndSettle();

    final udpBadge = find.text('UDP');
    expect(udpBadge, findsWidgets);
    await tester.tap(udpBadge.first);
    await tester.pumpAndSettle();

    // 4. Test Search Bar
    final searchField = find.byType(TextField).first;
    await tester.enterText(searchField, 'General');
    await tester.pumpAndSettle();
    expect(find.text('General Broadcast'), findsWidgets);

    // Clear search
    final clearSearch = find.byIcon(Icons.close);
    if (clearSearch.evaluate().isNotEmpty) {
      await tester.tap(clearSearch.first);
      await tester.pumpAndSettle();
    }

    // 5. Test Create Group Flow
    final buatGrupBtn = find.widgetWithText(ElevatedButton, 'Buat Grup');
    expect(buatGrupBtn, findsWidgets);
    await tester.tap(buatGrupBtn.first);
    await tester.pumpAndSettle();

    expect(find.text('Buat Grup Baru (> 2 Anggota)'), findsOneWidget);
    final groupNameInput = find.descendant(
      of: find.byType(AlertDialog),
      matching: find.byType(TextField),
    );
    await tester.enterText(groupNameInput, 'JarkomKelasA');
    final submitCreateGroup = find.descendant(
      of: find.byType(AlertDialog),
      matching: find.widgetWithText(ElevatedButton, 'Buat Grup'),
    );
    await tester.tap(submitCreateGroup);
    await tester.pumpAndSettle();

    // Verify now inside ChatScreen for JarkomKelasA
    expect(find.text('JarkomKelasA'), findsOneWidget);

    // Test Group Invite Dialog inside ChatScreen
    final inviteBtn = find.byIcon(Icons.group_add_outlined);
    expect(inviteBtn, findsOneWidget);
    await tester.tap(inviteBtn);
    await tester.pumpAndSettle();
    expect(find.text('Ajak Teman ke Grup'), findsOneWidget);
    await tester.tap(find.text('Tutup'));
    await tester.pumpAndSettle();

    // Test sending text message in ChatScreen
    final chatInput = find.byType(TextField);
    expect(chatInput, findsOneWidget);
    await tester.enterText(chatInput, 'Halo semua anggota grup!');
    await tester.pumpAndSettle();

    final sendBtn = find.byIcon(Icons.send_rounded);
    expect(sendBtn, findsOneWidget);
    await tester.tap(sendBtn);
    await tester.pumpAndSettle();

    // Verify message bubble appeared
    expect(find.text('Halo semua anggota grup!'), findsOneWidget);

    // Test Voice Note recording flow
    final micBtn = find.byIcon(Icons.mic);
    if (micBtn.evaluate().isNotEmpty) {
      await tester.tap(micBtn);
      await tester.pump(const Duration(milliseconds: 300));
      // Cancel voice recording
      final cancelVn = find.byIcon(Icons.close);
      if (cancelVn.evaluate().isNotEmpty) {
        await tester.tap(cancelVn);
        await tester.pumpAndSettle();
      }
    }

    // Go back to Home screen
    final backBtn = find.byIcon(Icons.arrow_back_ios_new);
    expect(backBtn, findsOneWidget);
    await tester.tap(backBtn);
    await tester.pumpAndSettle();

    // 7. Test Join Group Flow from Top Action Bar
    final gabungGrupBtn = find.widgetWithText(OutlinedButton, 'Gabung Grup');
    expect(gabungGrupBtn, findsWidgets);
    await tester.tap(gabungGrupBtn.first);
    await tester.pumpAndSettle();

    expect(find.text('Gabung ke Grup'), findsOneWidget);
    final joinGroupInput = find.descendant(
      of: find.byType(AlertDialog),
      matching: find.byType(TextField),
    );
    await tester.enterText(joinGroupInput, 'GrupSiswa');
    await tester.tap(find.text('Gabung Sekarang'));
    await tester.pumpAndSettle();

    // Verify navigated to GrupSiswa chat
    expect(find.text('GrupSiswa'), findsOneWidget);
    await tester.tap(find.byIcon(Icons.arrow_back_ios_new));
    await tester.pumpAndSettle();

    // 8. Test Friends Tab Navigation
    final temanTab = find.text('Teman');
    await tester.tap(temanTab);
    await tester.pumpAndSettle();

    // Verify Friends Action Bar is present
    expect(find.text('Tambah Teman Baru (1-on-1)'), findsOneWidget);

    // Tap Add Friend button
    await tester.tap(find.text('Tambah Teman Baru (1-on-1)'));
    await tester.pumpAndSettle();

    expect(find.text('Tambah Teman (Obrolan 1-on-1)'), findsOneWidget);
    expect(find.text('Anda sedang login sebagai: @jonathan'), findsOneWidget);
    final friendInput = find.descendant(
      of: find.byType(AlertDialog),
      matching: find.byType(TextField),
    );
    await tester.enterText(friendInput, 'budi_jarkom');
    await tester.tap(find.text('Mulai Chat'));
    await tester.pumpAndSettle();

    // Verify inside ChatScreen for friend budi_jarkom
    expect(find.text('budi_jarkom'), findsOneWidget);
    await tester.tap(find.byIcon(Icons.arrow_back_ios_new));
    await tester.pumpAndSettle();

    // 9. Test Header "+" Action Button Contextual Sheet
    final grupTab = find.text('Grup');
    await tester.tap(grupTab);
    await tester.pumpAndSettle();

    final addActionBtn = find.byIcon(Icons.add).first;
    await tester.tap(addActionBtn);
    await tester.pumpAndSettle();

    // In Grup tab, "+" opens action bottom sheet: Buat Grup Baru & Gabung Grup Teman
    expect(find.text('Aksi Obrolan Grup'), findsOneWidget);
    expect(find.text('Buat Grup Baru'), findsOneWidget);
    expect(find.text('Gabung Grup Teman'), findsOneWidget);

    // Dismiss sheet by tapping barrier
    await tester.tapAt(const Offset(20, 20));
    await tester.pumpAndSettle();

    // 10. Test Quick Menu Capsule ("Menu ☰")
    final menuCapsule = find.text('Menu');
    expect(menuCapsule, findsOneWidget);
    await tester.tap(menuCapsule);
    await tester.pumpAndSettle();

    expect(find.text('Menu Cepat DualChat'), findsOneWidget);
    expect(find.text('Uji Latency (Kirim /ping)'), findsOneWidget);
    expect(find.text('Konfigurasi IP & Profil'), findsOneWidget);

    // Tap Konfigurasi IP & Profil from quick menu -> opens ProfileScreen
    await tester.tap(find.text('Konfigurasi IP & Profil'));
    await tester.pumpAndSettle();

    // Verify ProfileScreen is active
    expect(find.text('Profil Pengguna'), findsOneWidget);
    expect(find.text('Jonathan Tester'), findsOneWidget);

    // Test back button from ProfileScreen
    final profileBackBtn = find.byTooltip('Kembali ke Percakapan');
    expect(profileBackBtn, findsOneWidget);
    await tester.tap(profileBackBtn);
    await tester.pumpAndSettle();

    // Verify returned to DualChat Home
    expect(find.text('DualChat'), findsWidgets);

    // 11. Test Bottom Navigation Bar Mode Switching
    final udpNav = find.text('UDP Mode');
    await tester.tap(udpNav);
    await tester.pumpAndSettle();
    expect(find.text('DualChat'), findsWidgets);

    final tcpNav = find.text('TCP Mode');
    await tester.tap(tcpNav);
    await tester.pumpAndSettle();
    expect(find.text('DualChat'), findsWidgets);

    final profileNav = find.text('Profile');
    await tester.tap(profileNav);
    await tester.pumpAndSettle();
    expect(find.text('Profil Pengguna'), findsOneWidget);

    final dualchatNav = find.text('DualChat');
    await tester.tap(dualchatNav);
    await tester.pumpAndSettle();
    expect(find.text('Grup'), findsOneWidget);
  });
}
