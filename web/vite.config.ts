import { defineConfig } from 'vitest/config';
import { readFileSync } from 'node:fs';

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8'));

export default defineConfig({
  // Relative base so the build works from any GitHub Pages sub-path
  base: './',
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  server: {
    // Fonts and images are shared with the desktop app one level up
    fs: { allow: ['..'] },
  },
  test: {
    include: ['test/**/*.test.ts'],
  },
});
