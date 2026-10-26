import { describe, expect, it } from 'vitest';
import { safeFolderName } from '../src/core/export';

describe('safeFolderName', () => {
  it('keeps ordinary names', () => {
    expect(safeFolderName('L001')).toBe('L001');
    expect(safeFolderName('Drums A')).toBe('Drums A');
    expect(safeFolderName('Café Loop')).toBe('Café Loop');
  });

  it('cannot climb out of the export folder', () => {
    expect(safeFolderName('../../evil')).toBe('_.._evil');
    expect(safeFolderName('..')).toBe('Loop');
    expect(safeFolderName('.')).toBe('Loop');
    expect(safeFolderName('/etc/passwd')).toBe('_etc_passwd');
    expect(safeFolderName('C:\\Windows\\x')).toBe('C__Windows_x');
  });

  it('removes reserved and control characters', () => {
    expect(safeFolderName('a<b>c:d"e|f?g*h')).toBe('a_b_c_d_e_f_g_h');
    expect(safeFolderName('tab\there\nnew')).toBe('tab_here_new');
  });

  it('trims dots and spaces at the ends and falls back when empty', () => {
    expect(safeFolderName('  .hidden. ')).toBe('hidden');
    expect(safeFolderName('   ')).toBe('Loop');
    expect(safeFolderName('', 'Tape Loops')).toBe('Tape Loops');
  });

  it('limits the length', () => {
    expect(safeFolderName('x'.repeat(300))).toHaveLength(100);
  });
});
