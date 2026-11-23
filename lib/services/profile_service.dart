import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../models/user_profile.dart';

class ProfileService {
  static final ProfileService instance = ProfileService._internal();
  ProfileService._internal();

  static const String _keyProfile = 'dualchat_user_profile_data';
  UserProfile? _cachedProfile;
  UserProfile? get currentProfile => _cachedProfile;

  File _getStorageFile() {
    // Determine the safest cross-platform directory for local profile persistence
    try {
      final dir = Directory.systemTemp;
      return File('${dir.path}/dualchat_user_profile.json');
    } catch (_) {
      return File('dualchat_user_profile.json');
    }
  }

  Future<UserProfile?> loadProfile() async {
    if (_cachedProfile != null) return _cachedProfile;

    try {
      final prefs = await SharedPreferences.getInstance();
      final content = prefs.getString(_keyProfile);
      if (content != null && content.trim().isNotEmpty) {
        final json = jsonDecode(content) as Map<String, dynamic>;
        _cachedProfile = UserProfile.fromJson(json);
        return _cachedProfile;
      }
    } catch (_) {}

    try {
      final file = _getStorageFile();
      if (await file.exists()) {
        final content = await file.readAsString();
        if (content.trim().isNotEmpty) {
          final json = jsonDecode(content) as Map<String, dynamic>;
          _cachedProfile = UserProfile.fromJson(json);
          return _cachedProfile;
        }
      }
    } catch (e) {
      if (kDebugMode) {
        print('ProfileService: Failed to read local profile: $e');
      }
    }
    return null;
  }

  Future<bool> saveProfile(UserProfile profile) async {
    _cachedProfile = profile;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString(_keyProfile, profile.toWireJson());
    } catch (_) {}

    try {
      final file = _getStorageFile();
      await file.writeAsString(profile.toWireJson(), flush: true);
      return true;
    } catch (e) {
      if (kDebugMode) {
        print('ProfileService: Failed to save local profile: $e');
      }
      return false;
    }
  }

  Future<void> clearProfile() async {
    _cachedProfile = null;
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove(_keyProfile);
    } catch (_) {}
    try {
      final file = _getStorageFile();
      if (await file.exists()) {
        await file.delete();
      }
    } catch (_) {}
  }
}
