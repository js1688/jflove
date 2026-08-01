/**
 * 笔记操作 Hook
 */

import { useCallback } from 'react';
import { useNoteStore } from '../stores/note-store';

export function useNotes() {
  const store = useNoteStore();

  const handleSave = useCallback(async () => {
    await store.saveNote();
  }, [store]);

  return {
    ...store,
    handleSave,
  };
}
