/**
 * Getting tape folders into the app (drag and drop, folder picker) and
 * writing exports out (straight into a folder where supported, otherwise a ZIP).
 */

import { ExportedLoop, safeFolderName } from '../core/export';
import { makeZip } from '../core/zip';

/** A tape folder: its name and its files keyed by lowercase file name */
export interface TapeFolder {
  name: string;
  files: Map<string, File>;
}

type DirectoryPickerWindow = Window & {
  showDirectoryPicker?: (options?: { mode?: 'read' | 'readwrite'; id?: string }) => Promise<FileSystemDirectoryHandle>;
};

const pickerWindow = window as DirectoryPickerWindow;

export function canWriteFolders(): boolean {
  return typeof pickerWindow.showDirectoryPicker === 'function';
}

function readAllEntries(dir: FileSystemDirectoryEntry): Promise<FileSystemEntry[]> {
  const reader = dir.createReader();
  const all: FileSystemEntry[] = [];
  return new Promise((resolve, reject) => {
    const next = () => reader.readEntries((batch) => {
      if (batch.length === 0) resolve(all);
      else {
        all.push(...batch);
        next();
      }
    }, reject);
    next();
  });
}

function entryFile(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

async function folderFromEntry(dir: FileSystemDirectoryEntry): Promise<TapeFolder> {
  const files = new Map<string, File>();
  for (const entry of await readAllEntries(dir)) {
    if (entry.isFile) {
      const file = await entryFile(entry as FileSystemFileEntry);
      files.set(file.name.toLowerCase(), file);
    }
  }
  return { name: dir.name, files };
}

/** Read a drop: a folder, or the files of a tape folder dropped together */
export async function folderFromDrop(dataTransfer: DataTransfer): Promise<TapeFolder | null> {
  const entries = [...dataTransfer.items]
    .map((item) => item.webkitGetAsEntry?.())
    .filter((e): e is FileSystemEntry => !!e);

  const dir = entries.find((e) => e.isDirectory) as FileSystemDirectoryEntry | undefined;
  if (dir) return folderFromEntry(dir);

  const files = [...dataTransfer.files];
  if (files.length === 0) return null;
  return { name: 'Tape', files: new Map(files.map((f) => [f.name.toLowerCase(), f])) };
}

/** Let the user choose a tape folder */
export async function pickFolder(): Promise<TapeFolder | null> {
  if (pickerWindow.showDirectoryPicker) {
    let handle: FileSystemDirectoryHandle;
    try {
      handle = await pickerWindow.showDirectoryPicker({ mode: 'read', id: 'tape' });
    } catch {
      return null; // cancelled
    }
    const files = new Map<string, File>();
    for await (const entry of (handle as unknown as AsyncIterable<FileSystemHandle>)) {
      if (entry.kind === 'file') {
        const file = await (entry as FileSystemFileHandle).getFile();
        files.set(file.name.toLowerCase(), file);
      }
    }
    return { name: handle.name, files };
  }

  // Fallback: a directory file input (Safari, Firefox)
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.webkitdirectory = true;
    input.addEventListener('change', () => {
      const all = [...(input.files ?? [])];
      if (all.length === 0) return resolve(null);
      // Only files directly inside the chosen folder
      const top = all.filter((f) => f.webkitRelativePath.split('/').length === 2);
      const name = all[0].webkitRelativePath.split('/')[0] || 'Tape';
      resolve({ name, files: new Map(top.map((f) => [f.name.toLowerCase(), f])) });
    });
    input.addEventListener('cancel', () => resolve(null));
    input.click();
  });
}

export interface ExportTarget {
  /** Short description for the status bar */
  label: string;
  writeLoop(loop: ExportedLoop): Promise<void>;
  finish(): Promise<void>;
}

/** Ask where to export. Returns null when the user cancels. */
export async function chooseExportTarget(zipName: string): Promise<ExportTarget | null> {
  if (pickerWindow.showDirectoryPicker) {
    let root: FileSystemDirectoryHandle;
    try {
      root = await pickerWindow.showDirectoryPicker({ mode: 'readwrite', id: 'export' });
    } catch {
      return null;
    }
    return {
      label: root.name || 'the chosen folder',
      async writeLoop(loop) {
        const dir = await root.getDirectoryHandle(safeFolderName(loop.folderName), { create: true });
        for (const file of loop.files) {
          const handle = await dir.getFileHandle(file.name, { create: true });
          const writable = await handle.createWritable();
          await writable.write(file.data);
          await writable.close();
        }
      },
      async finish() {},
    };
  }

  // ZIP download fallback
  const entries: { path: string; data: ArrayBuffer | string }[] = [];
  zipName = safeFolderName(zipName, 'Tape Loops');
  return {
    label: `${zipName}.zip`,
    async writeLoop(loop) {
      const folder = safeFolderName(loop.folderName);
      for (const file of loop.files) entries.push({ path: `${folder}/${file.name}`, data: file.data });
    },
    async finish() {
      const url = URL.createObjectURL(makeZip(entries));
      const a = document.createElement('a');
      a.href = url;
      a.download = `${zipName}.zip`;
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60_000);
    },
  };
}
