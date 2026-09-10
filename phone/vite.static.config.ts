import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/postcss';
import { existsSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';

const projectRoot = __dirname;
const outputDirectory = resolve(projectRoot, process.env.ASIMUT_PHONE_OUT_DIR || 'dist-phone');
const version = process.env.ASIMUT_PHONE_VERSION || 'development';
const configPath = resolve(projectRoot, '../data/phone_server_config.json');
const publicOrigin = process.env.ASIMUT_PHONE_ORIGIN ||
  (existsSync(configPath) ? JSON.parse(readFileSync(configPath, 'utf8')).public_origin : '');
if (typeof publicOrigin !== 'string' || !/^https:\/\/[a-z0-9-]+\.[a-z0-9-]+\.ts\.net:10443$/.test(publicOrigin)) {
  throw new Error('Set ASIMUT_PHONE_ORIGIN to the exact private HTTPS origin before building.');
}

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
  define: { 'process.env.NEXT_PUBLIC_ASIMUT_PHONE_ORIGIN': JSON.stringify(publicOrigin) },
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
          '/favicon.png',
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
          `${JSON.stringify({ version, public_origin: publicOrigin }, null, 2)}\n`,
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
