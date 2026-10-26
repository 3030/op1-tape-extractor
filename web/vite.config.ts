import { defineConfig, Plugin } from 'vitest/config';
import { readFileSync } from 'node:fs';

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf-8'));

// The app is fully self-contained: only its own scripts, styles, fonts and images.
// The page shares an origin with other tools, so this limits what any injected
// markup could ever do. Build-only, because the dev server injects inline styles.
const CONTENT_SECURITY_POLICY = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self'",
  "media-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
].join('; ');

function contentSecurityPolicy(): Plugin {
  return {
    name: 'content-security-policy',
    apply: 'build',
    transformIndexHtml: () => [
      { tag: 'meta', attrs: { 'http-equiv': 'Content-Security-Policy', content: CONTENT_SECURITY_POLICY }, injectTo: 'head-prepend' },
      { tag: 'meta', attrs: { name: 'referrer', content: 'no-referrer' }, injectTo: 'head-prepend' },
    ],
  };
}

export default defineConfig({
  plugins: [contentSecurityPolicy()],
  // Relative base so the build works from any sub-path (tools.3030.ca/op1loop/)
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
