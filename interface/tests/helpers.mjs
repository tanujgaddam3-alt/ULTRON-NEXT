import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

export const SCRIPT_FILES = [
  'contracts/contracts.js',
  'services/bridge.js',
  'services/providers/mockProvider.js',
  'services/providers/websocketProvider.js'
];

/**
 * Load the classic-script ULTRON stack into a fresh VM context.
 * A window shim (window === globalThis) reproduces the browser's
 * classic-script environment so the same files run verbatim under node:test.
 */
export function loadStack(options = {}) {
  const { files = SCRIPT_FILES } = options;
  const context = {
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    Date,
    JSON,
    Math,
    Promise,
    Error,
    TypeError,
    RangeError,
    String,
    Number,
    Boolean,
    Object,
    Array,
    isFinite,
    parseInt,
    parseFloat
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);

  for (const file of files) {
    const code = readFileSync(resolve(ROOT, file), 'utf8');
    vm.runInContext(code, context, { filename: file });
  }
  return context;
}

export const delay = (ms) => new Promise((resolveDelay) => setTimeout(resolveDelay, ms));