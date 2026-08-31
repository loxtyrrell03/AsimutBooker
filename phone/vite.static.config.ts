import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/postcss';
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';

const projectRoot = __dirname;
const outputDirectory = resolve(projectRoot, 'dist-phone');
const version = process.env.ASIMUT_PHONE_VERSION || 'development';

function filesUnder(directory: string, prefix = ''): string[] {
  if (!existsSync(directory)) return [];
  return readdirSync(directory).flatMap((name) => {
    const absolute = resolve(directory, name);
    const relative = prefix ? `${prefix}/${name}` : name;
    return statSync(absolute).isDirectory()
      ? filesUnder(absolute, relative)
      : [`/${relative.replaceAll('\\', '/')}`];
  });
}

export default defineConfig({
  root: resolve(projectRoot, 'local'),
  publicDir: resolve(projectRoot, 'public'),
  resolve: {
    alias: { '@': projectRoot },
  },
  css: {
    postcss: { plugins: [tailwindcss()] },
  },
  plugins: [
    react(),
    {
      name: 'asimut-phone-build-info',
      closeBundle() {
        const shellFiles = [
          '/',
          '/manifest.webmanifest',
          '/favicon.svg',
          '/icon-192.png',
          '/icon-512.png',
          '/icon-maskable-512.png',
          '/apple-touch-icon.png',
          ...filesUnder(resolve(outputDirectory, 'assets'), 'assets'),
        ];
        const workerPath = resolve(outputDirectory, 'sw.js');
        const worker = readFileSync(workerPath, 'utf8')
          .replace(
            /const CACHE_VERSION = '[^']+';/,
            `const CACHE_VERSION = ${JSON.stringify(`asimut-phone-${version}`)};`,
          )
          .replace(
            /const SHELL_FILES = \[[\s\S]*?\];/,
            `const SHELL_FILES = ${JSON.stringify(shellFiles, null, 2)};`,
          );
        writeFileSync(workerPath, worker, 'utf8');
        writeFileSync(
          resolve(outputDirectory, 'build-info.json'),
          `${JSON.stringify({ version }, null, 2)}\n`,
          'utf8',
        );
      },
    },
  ],
  build: {
    outDir: outputDirectory,
    emptyOutDir: true,
    sourcemap: false,
    target: 'es2022',
  },
});
