import 'package:flutter/material.dart';
import 'chat_message.dart';

class FeedComment {
  final String id;
  final String author;
  final String authorName;
  final String avatarEmoji;
  final String text;
  final String timeAgo;

  FeedComment({
    required this.id,
    required this.author,
    required this.authorName,
    this.avatarEmoji = '👤',
    required this.text,
    this.timeAgo = 'Barusan',
  });

  Map<String, dynamic> toJson() => {
        'id': id,
        'author': author,
        'authorName': authorName,
        'avatarEmoji': avatarEmoji,
        'text': text,
        'timeAgo': timeAgo,
      };

  factory FeedComment.fromJson(Map<String, dynamic> json) {
    return FeedComment(
      id: json['id']?.toString() ?? '',
      author: json['author']?.toString() ?? '',
      authorName: json['authorName']?.toString() ?? '',
      avatarEmoji: json['avatarEmoji']?.toString() ?? '👤',
      text: json['text']?.toString() ?? '',
      timeAgo: json['timeAgo']?.toString() ?? 'Barusan',
    );
  }

  static String formatWirePayload(String postId, String commentText) =>
      '[FEED_COMMENT:$postId] $commentText';

  static Map<String, String>? parseWirePayload(String payload) {
    final match = RegExp(r'^\[FEED_COMMENT:([^\]]+)\]\s*(.*)$').firstMatch(payload.trim());
    if (match != null) {
      return {
        'postId': match.group(1)?.trim() ?? '',
        'text': match.group(2)?.trim() ?? '',
      };
    }
    return null;
  }
}

class FeedPost {
  final String id;
  final String author;
  final String authorName;
  final String avatarEmoji;
  final Color avatarColor;
  final String timeAgo;
  final String content;
  final String? imageTag;
  final String? imageTitle;
  final String? imageIcon;
  int likes;
  bool isLiked;
  final TransportProto proto;
  final int _commentsCount;
  final List<FeedComment> comments;

  FeedPost({
    required this.id,
    required this.author,
    required this.authorName,
    required this.avatarEmoji,
    required this.avatarColor,
    required this.timeAgo,
    required this.content,
    this.imageTag,
    this.imageTitle,
    this.imageIcon,
    this.likes = 0,
    this.isLiked = false,
    this.proto = TransportProto.tcp,
    int? commentsCount,
    List<FeedComment>? comments,
  })  : _commentsCount = commentsCount ?? comments?.length ?? 0,
        comments = comments ?? [];

  int get commentsCount => comments.isNotEmpty ? comments.length : _commentsCount;

  void addComment(FeedComment comment) {
    comments.add(comment);
  }

  void toggleLike() {
    isLiked = !isLiked;
    likes += isLiked ? 1 : -1;
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'author': author,
        'authorName': authorName,
        'avatarEmoji': avatarEmoji,
        'avatarColor': avatarColor.value,
        'timeAgo': timeAgo,
        'content': content,
        'imageTag': imageTag,
        'imageTitle': imageTitle,
        'imageIcon': imageIcon,
        'likes': likes,
        'isLiked': isLiked,
        'proto': proto.name,
        'commentsCount': commentsCount,
        'comments': comments.map((c) => c.toJson()).toList(),
      };

  factory FeedPost.fromJson(Map<String, dynamic> json) {
    final rawComments = json['comments'] as List<dynamic>? ?? [];
    final commentsList = rawComments
        .map((c) => FeedComment.fromJson(c as Map<String, dynamic>))
        .toList();

    return FeedPost(
      id: json['id']?.toString() ?? '',
      author: json['author']?.toString() ?? '',
      authorName: json['authorName']?.toString() ?? '',
      avatarEmoji: json['avatarEmoji']?.toString() ?? '👤',
      avatarColor: Color((json['avatarColor'] as num?)?.toInt() ?? 0xFF0F172A),
      timeAgo: json['timeAgo']?.toString() ?? 'Barusan',
      content: json['content']?.toString() ?? '',
      imageTag: json['imageTag']?.toString(),
      imageTitle: json['imageTitle']?.toString(),
      imageIcon: json['imageIcon']?.toString(),
      likes: (json['likes'] as num?)?.toInt() ?? 0,
      isLiked: json['isLiked'] == true,
      proto: (json['proto']?.toString().toLowerCase() == 'udp')
          ? TransportProto.udp
          : TransportProto.tcp,
      commentsCount: (json['commentsCount'] as num?)?.toInt() ?? commentsList.length,
      comments: commentsList,
    );
  }

  static List<FeedPost> initialPosts() {
    return [
      FeedPost(
        id: 'post_1',
        author: 'kierra',
        authorName: 'Kierra Franci',
        avatarEmoji: '👩‍💻',
        avatarColor: const Color(0xFF10B981),
        timeAgo: '50 Minutes Ago',
        content:
            'Excited for the big match tonight! It\'s going to be a clash of the titans, and I can\'t wait to see how our team performs on the field. Pengujian soket TCP Port 5000 juga sudah terverifikasi di Wireshark!',
        imageTag: 'wireshark_capture',
        imageTitle: 'Wireshark 3-Way Handshake Capture',
        imageIcon: '🦈',
        likes: 7,
        isLiked: false,
        proto: TransportProto.tcp,
        comments: [
          FeedComment(
            id: 'comm_1_1',
            author: 'rudy',
            authorName: 'Rudy Hartono',
            avatarEmoji: '⚽',
            text: 'Mantap! TCP 3-way handshake SYN-ACK tercatat rapi di Wireshark port 5000.',
            timeAgo: '40m ago',
          ),
          FeedComment(
            id: 'comm_1_2',
            author: 'yusuf',
            authorName: 'Yusuf M',
            avatarEmoji: '🦈',
            text: 'Analisis seq number dan flow control TCP Port 5000 sudah sesuai.',
            timeAgo: '25m ago',
          ),
        ],
      ),
      FeedPost(
        id: 'post_2',
        author: 'lewis',
        authorName: 'Lewis Jones',
        avatarEmoji: '👨‍💼',
        avatarColor: const Color(0xFFF59E0B),
        timeAgo: '2 Hours Ago',
        content:
            'Watching the game with friends tonight is going to be epic. There\'s nothing like the thrill of live football and the camaraderie that comes with it! Streaming datagram UDP 5001 mencatat 0-RTT tanpa buffer lag.',
        imageTag: 'network_topology',
        imageTitle: 'Topologi Jaringan DualChat',
        imageIcon: '🖥️',
        likes: 12,
        isLiked: true,
        proto: TransportProto.udp,
        comments: [
          FeedComment(
            id: 'comm_2_1',
            author: 'alice',
            authorName: 'Alice',
            avatarEmoji: '👩',
            text: 'UDP 5001 sangat cepat untuk real-time datagram streaming!',
            timeAgo: '1h ago',
          ),
          FeedComment(
            id: 'comm_2_2',
            author: 'steven',
            authorName: 'Steven',
            avatarEmoji: '👨‍💻',
            text: 'Zero latency overhead pada streaming datagram.',
            timeAgo: '45m ago',
          ),
          FeedComment(
            id: 'comm_2_3',
            author: 'kierra',
            authorName: 'Kierra Franci',
            avatarEmoji: '👩‍💻',
            text: 'Bandingkan RTT vs TCP di Wireshark nanti.',
            timeAgo: '30m ago',
          ),
          FeedComment(
            id: 'comm_2_4',
            author: 'gretchen',
            authorName: 'Gretchen',
            avatarEmoji: '👩',
            text: 'Sip, tinggal verifikasi throughput.',
            timeAgo: '15m ago',
          ),
        ],
      ),
    ];
  }
}
