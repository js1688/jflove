/**
 * 设置状态管理
 */

import { create } from 'zustand';
import {
  getLocalSessionMaxSeconds, setLocalSessionMaxSeconds,
  getNotesDiskId, setNotesDiskId,
  getNotesPath, setNotesPath,
} from '../utils/session';

interface SettingsState {
  localSessionMaxSeconds: number;
  notesDiskId: number | null;
  notesPath: string;

  setLocalSessionMaxSeconds: (seconds: number) => void;
  setNotesDiskId: (diskId: number | null) => void;
  setNotesPath: (path: string) => void;
}

export const useSettingsStore = create<SettingsState>((set) => ({
  localSessionMaxSeconds: getLocalSessionMaxSeconds(),
  notesDiskId: getNotesDiskId(),
  notesPath: getNotesPath(),

  setLocalSessionMaxSeconds: (seconds) => {
    setLocalSessionMaxSeconds(seconds);
    set({ localSessionMaxSeconds: seconds });
  },

  setNotesDiskId: (diskId) => {
    setNotesDiskId(diskId);
    set({ notesDiskId: diskId });
  },

  setNotesPath: (path) => {
    setNotesPath(path);
    set({ notesPath: path });
  },
}));
