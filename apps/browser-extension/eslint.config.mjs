import js from "@eslint/js";
import { defineConfig, globalIgnores } from "eslint/config";
import tseslint from "typescript-eslint";

/**
 * Type-aware linting for the extension. The rules that matter here are the ones
 * the repository's code style asks for: no implicit `any`, no unchecked
 * promises, explicit return types, and no unused code left behind.
 */
export default defineConfig(
  // The fixture pages are plain browser scripts served to the test page, not
  // part of the extension's TypeScript program.
  globalIgnores(["dist/**", "dist-e2e/**", "dist-e2e-browser/**", "node_modules/**", "tests/chrome/pages/**"]),
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "@typescript-eslint/explicit-function-return-type": [
        "error",
        { allowExpressions: true, allowTypedFunctionExpressions: true },
      ],
      "@typescript-eslint/no-unnecessary-condition": "off",
      "@typescript-eslint/restrict-template-expressions": [
        "error",
        { allowNumber: true, allowBoolean: false },
      ],
      "no-console": ["error", { allow: ["warn", "error"] }],
    },
  },
  {
    files: ["tests/**/*.ts"],
    rules: {
      "@typescript-eslint/no-non-null-assertion": "off",
      "no-console": "off",
    },
  },
);
