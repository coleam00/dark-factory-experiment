// Vitest setup — runs before each test file.
//
// Adds `@testing-library/jest-dom` matchers (`toBeInTheDocument`, etc.)
// to Vitest's `expect`. See CLAUDE.md §Testing for frontend test conventions.

import { Blob as NodeBlob } from 'node:buffer';
import '@testing-library/jest-dom/vitest';

// jsdom ships a Blob stub without text()/arrayBuffer(); swap in node:buffer's
// Blob so tests can read content back.
globalThis.Blob = NodeBlob as unknown as typeof Blob;
