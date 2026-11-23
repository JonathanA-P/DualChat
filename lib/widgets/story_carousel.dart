import 'package:flutter/material.dart';
import '../models/chat_room.dart';
import '../models/user_profile.dart';

class StoryCarousel extends StatelessWidget {
  final UserProfile? profile;
  final List<ChatRoom> rooms;
  final ValueChanged<ChatRoom> onSelectRoom;
  final VoidCallback onAddStory;

  const StoryCarousel({
    super.key,
    required this.profile,
    required this.rooms,
    required this.onSelectRoom,
    required this.onAddStory,
  });

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 98,
      child: ListView.builder(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 16),
        itemCount: 1 + rooms.length,
        itemBuilder: (context, index) {
          if (index == 0) {
            // "Add Story" / My Status
            return _buildAddStoryItem();
          }

          final room = rooms[index - 1];
          return _buildStoryItem(room);
        },
      ),
    );
  }

  Widget _buildAddStoryItem() {
    return GestureDetector(
      onTap: onAddStory,
      child: Container(
        width: 68,
        margin: const EdgeInsets.only(right: 12),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Stack(
              clipBehavior: Clip.none,
              children: [
                Container(
                  width: 58,
                  height: 58,
                  decoration: const BoxDecoration(
                    color: Color(0xFF0F172A),
                    shape: BoxShape.circle,
                  ),
                  alignment: Alignment.center,
                  child: Text(
                    profile != null
                        ? UserProfile.avatarPresets[
                            profile!.avatarIndex % UserProfile.avatarPresets.length]
                        : '👤',
                    style: const TextStyle(fontSize: 24),
                  ),
                ),
                Positioned(
                  right: 0,
                  bottom: 0,
                  child: Container(
                    width: 20,
                    height: 20,
                    decoration: BoxDecoration(
                      color: const Color(0xFF38BDF8),
                      shape: BoxShape.circle,
                      border: Border.all(color: Colors.white, width: 2),
                    ),
                    alignment: Alignment.center,
                    child: const Icon(
                      Icons.add,
                      size: 13,
                      color: Colors.white,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 6),
            const Text(
              'Status Saya',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.center,
              style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w600,
                color: Color(0xFF334155),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildStoryItem(ChatRoom room) {
    return GestureDetector(
      onTap: () => onSelectRoom(room),
      child: Container(
        width: 68,
        margin: const EdgeInsets.only(right: 12),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              padding: const EdgeInsets.all(2.5),
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                border: Border.all(
                  color: room.avatarColor,
                  width: 2,
                ),
              ),
              child: Container(
                width: 50,
                height: 50,
                decoration: BoxDecoration(
                  color: room.avatarColor.withAlpha(35),
                  shape: BoxShape.circle,
                ),
                alignment: Alignment.center,
                child: Text(
                  room.avatarEmoji,
                  style: const TextStyle(fontSize: 22),
                ),
              ),
            ),
            const SizedBox(height: 6),
            Text(
              room.name,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              textAlign: TextAlign.center,
              style: const TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.w500,
                color: Color(0xFF334155),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
