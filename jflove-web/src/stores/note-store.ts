/**
 * 笔记状态管理
 */

import { create } from 'zustand';
import { noteService } from '../services/note-service';
import type { Note } from '../types/models';

interface NoteState {
  notes: Note[];
  notesLoading: boolean;
  searchQuery: string;

  // 当前编辑
  currentFilename: string | null;
  currentContent: string;
  originalContent: string;
  isModified: boolean;
  isLoading: boolean;

  // 列表操作
  loadNotes: () => Promise<void>;
  setSearchQuery: (query: string) => void;
  getFilteredNotes: () => Note[];

  // 编辑操作
  loadNote: (filename: string) => Promise<void>;
  setContent: (content: string) => void;
  saveNote: () => Promise<void>;
  createNote: (filename: string) => Promise<void>;
  renameNote: (oldFilename: string, newFilename: string) => Promise<void>;
  deleteNote: (filename: string) => Promise<void>;
  discardChanges: () => void;
}

export const useNoteStore = create<NoteState>((set, get) => ({
  notes: [],
  notesLoading: false,
  searchQuery: '',
  currentFilename: null,
  currentContent: '',
  originalContent: '',
  isModified: false,
  isLoading: false,

  loadNotes: async () => {
    set({ notesLoading: true });
    try {
      const notes = await noteService.listNotes();
      set({ notes, notesLoading: false });
    } catch {
      set({ notesLoading: false });
      throw new Error('加载笔记列表失败');
    }
  },

  setSearchQuery: (query) => set({ searchQuery: query }),

  getFilteredNotes: () => {
    const { notes, searchQuery } = get();
    if (!searchQuery.trim()) return notes;
    const q = searchQuery.toLowerCase();
    return notes.filter(n => n.filename.toLowerCase().includes(q));
  },

  loadNote: async (filename) => {
    set({ isLoading: true, currentFilename: filename });
    try {
      const content = await noteService.getNote(filename);
      set({
        currentContent: content,
        originalContent: content,
        isModified: false,
        isLoading: false,
      });
    } catch {
      set({ isLoading: false });
      throw new Error('加载笔记失败');
    }
  },

  setContent: (content) => {
    const { originalContent } = get();
    set({
      currentContent: content,
      isModified: content !== originalContent,
    });
  },

  saveNote: async () => {
    const { currentFilename, currentContent, originalContent } = get();
    if (!currentFilename || currentContent === originalContent) return;

    await noteService.saveNote(currentFilename, currentContent);
    set({ originalContent: currentContent, isModified: false });
  },

  createNote: async (filename) => {
    await noteService.createNote(filename);
    await get().loadNotes();
  },

  renameNote: async (oldFilename, newFilename) => {
    await noteService.renameNote(oldFilename, newFilename);
    await get().loadNotes();
  },

  deleteNote: async (filename) => {
    await noteService.deleteNote(filename);
    await get().loadNotes();
  },

  discardChanges: () => {
    const { originalContent } = get();
    set({ currentContent: originalContent, isModified: false });
  },
}));
