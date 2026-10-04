import { defineConfig } from 'eslint/config';
import tseslint from 'typescript-eslint';

export default defineConfig(
  { ignores: ['dist/**', 'node_modules/**', 'tests/apps/**'] },
  ...tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        // The manifest toolchain ships as plain ESM so a client can run it
        // straight from an install, with no build step and no TypeScript.
        projectService: {
          allowDefaultProject: [
            'eslint.config.js',
            'scripts/dev-generate-key.mjs',
            'scripts/dev-sign-manifest.mjs',
          ],
        },
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      '@typescript-eslint/explicit-function-return-type': ['error', { allowExpressions: false }],
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/consistent-type-imports': 'error',
      '@typescript-eslint/default-param-last': 'error',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
  {
    files: ['tests/**/*.ts'],
    rules: {
      '@typescript-eslint/no-non-null-assertion': 'off',
      '@typescript-eslint/explicit-function-return-type': 'off',
    },
  },
  {
    // The manifest toolchain ships as plain ESM so a client can run it straight
    // from an install, with no build step and no TypeScript. Type-aware rules
    // have no types to work from there.
    files: ['scripts/**/*.mjs'],
    ...tseslint.configs.disableTypeChecked,
  },
);
