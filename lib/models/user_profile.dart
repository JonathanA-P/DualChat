import 'dart:convert';

class UserProfile {
  final String name;
  final String username;
  final int avatarIndex;
  final String status;
  final String host;
  final int tcpPort;
  final int udpPort;
  final DateTime createdAt;

  const UserProfile({
    required this.name,
    required this.username,
    this.avatarIndex = 0,
    this.status = 'Active',
    this.host = '127.0.0.1',
    this.tcpPort = 5000,
    this.udpPort = 5001,
    required this.createdAt,
  });

  UserProfile copyWith({
    String? name,
    String? username,
    int? avatarIndex,
    String? status,
    String? host,
    int? tcpPort,
    int? udpPort,
    DateTime? createdAt,
  }) {
    return UserProfile(
      name: name ?? this.name,
      username: username ?? this.username,
      avatarIndex: avatarIndex ?? this.avatarIndex,
      status: status ?? this.status,
      host: host ?? this.host,
      tcpPort: tcpPort ?? this.tcpPort,
      udpPort: udpPort ?? this.udpPort,
      createdAt: createdAt ?? this.createdAt,
    );
  }

  Map<String, dynamic> toJson() => {
        'name': name,
        'username': username,
        'avatarIndex': avatarIndex,
        'status': status,
        'host': host,
        'tcpPort': tcpPort,
        'udpPort': udpPort,
        'createdAt': createdAt.toIso8601String(),
      };

  factory UserProfile.fromJson(Map<String, dynamic> json) {
    return UserProfile(
      name: json['name']?.toString() ?? 'User',
      username: json['username']?.toString() ?? 'user_1',
      avatarIndex: (json['avatarIndex'] as num?)?.toInt() ?? 0,
      status: json['status']?.toString() ?? 'Active',
      host: json['host']?.toString() ?? '127.0.0.1',
      tcpPort: (json['tcpPort'] as num?)?.toInt() ?? 5000,
      udpPort: (json['udpPort'] as num?)?.toInt() ?? 5001,
      createdAt: json['createdAt'] != null
          ? DateTime.tryParse(json['createdAt'].toString()) ?? DateTime.now()
          : DateTime.now(),
    );
  }

  String toWireJson() => jsonEncode(toJson());

  static const List<String> avatarPresets = [
    '⚽', // Soccer
    '🏆', // Trophy
    '⚡', // Flash / UDP speed
    '🛡️', // Shield / TCP reliability
    '🦁', // Premier League lion
    '🦅', // Eagle
    '🔥', // Flame
    '⭐', // Star
    '🚀', // Rocket
  ];
}
