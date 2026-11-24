import 'dart:convert';
import 'dart:typed_data';
import 'package:crypto/crypto.dart';
import 'package:encrypt/encrypt.dart' as enc;

/// EncryptionService - Menyediakan enkripsi & dekripsi payload end-to-end (AES-256-CBC)
/// untuk melindungi privasi pesan chat, voice note, dan gambar dari sniffing Wireshark.
class EncryptionService {
  static final EncryptionService instance = EncryptionService._internal();
  EncryptionService._internal();

  static const String encPrefix = '[ENC:';

  /// Menghasilkan 32-byte (256-bit) AES Key dari identifier percakapan (room/DM)
  enc.Key _deriveKey(String channelId) {
    final salt = 'DualChat_Jarkom_PBL_2026_SecretSalt:';
    final hash = sha256.convert(utf8.encode('$salt$channelId'));
    return enc.Key(Uint8List.fromList(hash.bytes));
  }

  /// Mengecek apakah teks berformat terenkripsi
  bool isEncrypted(String payload) {
    return payload.trim().startsWith(encPrefix) && payload.trim().endsWith(']');
  }

  /// Mengenkripsi string payload menjadi `[ENC:<iv_base64>:<ciphertext_base64>]`
  String encryptPayload(String plaintext, {required String channelId}) {
    if (plaintext.isEmpty) return plaintext;
    try {
      final key = _deriveKey(channelId);
      final iv = enc.IV.fromSecureRandom(16);
      final encrypter = enc.Encrypter(enc.AES(key, mode: enc.AESMode.cbc, padding: 'PKCS7'));

      final encrypted = encrypter.encrypt(plaintext, iv: iv);
      return '$encPrefix${iv.base64}:${encrypted.base64}]';
    } catch (e) {
      // Fallback jika terjadi error
      return plaintext;
    }
  }

  /// Mendekripsi string `[ENC:<iv_base64>:<ciphertext_base64>]` kembali ke plaintext asli
  String decryptPayload(String payload, {required String channelId}) {
    final trimmed = payload.trim();
    if (!isEncrypted(trimmed)) {
      return payload; // Bukan payload terenkripsi, kembalikan teks asli
    }

    try {
      final inside = trimmed.substring(encPrefix.length, trimmed.length - 1);
      final colonIndex = inside.indexOf(':');
      if (colonIndex == -1) return payload;

      final ivBase64 = inside.substring(0, colonIndex);
      final cipherBase64 = inside.substring(colonIndex + 1);

      final key = _deriveKey(channelId);
      final iv = enc.IV.fromBase64(ivBase64);
      final encrypter = enc.Encrypter(enc.AES(key, mode: enc.AESMode.cbc, padding: 'PKCS7'));

      final decrypted = encrypter.decrypt(enc.Encrypted.fromBase64(cipherBase64), iv: iv);
      return decrypted;
    } catch (e) {
      return payload; // Jika key berbeda atau rusak, fallback
    }
  }

  /// Menghasilkan channelId deterministik untuk obrolan 1-on-1 (Direct Message)
  String getDirectChannelId(String user1, String user2) {
    final list = [user1.trim().toLowerCase(), user2.trim().toLowerCase()]..sort();
    return 'dm_${list[0]}_${list[1]}';
  }

  /// Menghasilkan channelId untuk obrolan Grup
  String getGroupChannelId(String groupName) {
    return 'group_${groupName.trim().toLowerCase()}';
  }
}
