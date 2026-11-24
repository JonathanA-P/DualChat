import 'package:flutter_test/flutter_test.dart';
import 'package:jarkom_project/services/encryption_service.dart';

void main() {
  group('EncryptionService End-to-End Encryption Tests', () {
    final encService = EncryptionService.instance;

    test('Encrypt and Decrypt plain text message', () {
      const original = 'Halo Salma, ini pesan rahasia tugas Jarkom!';
      final channelId = encService.getDirectChannelId('Lian', 'Salma');

      final encrypted = encService.encryptPayload(original, channelId: channelId);
      expect(encService.isEncrypted(encrypted), isTrue);
      expect(encrypted.contains(original), isFalse); // Pastikan plaintext tidak bocor

      final decrypted = encService.decryptPayload(encrypted, channelId: channelId);
      expect(decrypted, equals(original));
    });

    test('Encrypt and Decrypt large image payload', () {
      final imagePayload = '[IMG_DATA:foto_jarkom.jpg:${'A' * 2000}==:]';
      final channelId = encService.getGroupChannelId('Jarkom');

      final encrypted = encService.encryptPayload(imagePayload, channelId: channelId);
      expect(encService.isEncrypted(encrypted), isTrue);
      expect(encrypted.contains('foto_jarkom.jpg'), isFalse);

      final decrypted = encService.decryptPayload(encrypted, channelId: channelId);
      expect(decrypted, equals(imagePayload));
    });

    test('Key isolation: Different channelId cannot decrypt message', () {
      const original = 'Pesan rahasia grup A';
      final channelA = encService.getGroupChannelId('GrupA');
      final channelB = encService.getGroupChannelId('GrupB');

      final encrypted = encService.encryptPayload(original, channelId: channelA);
      final decryptedWrong = encService.decryptPayload(encrypted, channelId: channelB);

      // Decryption with wrong key does not return plaintext
      expect(decryptedWrong, isNot(equals(original)));
    });

    test('Backward compatibility: unencrypted text passes through unchanged', () {
      const plainSystem = '[SISTEM] Salma membuat grup baru!';
      final channelId = encService.getGroupChannelId('Jarkom');

      final result = encService.decryptPayload(plainSystem, channelId: channelId);
      expect(result, equals(plainSystem));
    });
  });
}
