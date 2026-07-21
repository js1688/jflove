import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/note.dart';
import '../services/note_service.dart';
import 'session_provider.dart';

/// 笔记服务
final noteServiceProvider = Provider<NoteService>((ref) {
  return NoteService(ref.watch(httpServiceProvider));
});

/// 笔记列表
final noteListProvider = FutureProvider<List<Note>>((ref) async {
  return ref.watch(noteServiceProvider).listNotes();
});

/// 笔记内容（按文件名）
final noteContentProvider = FutureProvider.family<String?, String>((
  ref,
  filename,
) async {
  final note = await ref.watch(noteServiceProvider).getNote(filename);
  return note.content;
});
