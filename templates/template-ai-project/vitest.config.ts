// GREP_SUMMARY: vitest config project tests include node
// STRUCTURE: ▶ defineConfig → ◇ include tests/**/*.test.ts → ⎋ config
// Канон монорепо (packages/*/vitest.config.ts): тесты проекта — tests/ (симуляции сценариев,
// бриф §8). Директория tests/ создаётся проектом; отсутствие — валидное пустое состояние.
import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['tests/**/*.test.ts'],
  },
});
