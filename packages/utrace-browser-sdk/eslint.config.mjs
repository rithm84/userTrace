import { defineConfig } from "eslint/config";
import globals from "globals";
import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default defineConfig([
  { ignores: ["dist/**", "tests/chrome/fixture/build/**", "scripts/keys/**"] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: { allowDefaultProject: ["eslint.config.mjs", "scripts/*.mjs"] },
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "@typescript-eslint/no-explicit-any": "error",
      "@typescript-eslint/explicit-module-boundary-types": "error",
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/default-param-last": "error",
      "no-restricted-syntax": [
        "error",
        {
          selector: "AssignmentPattern",
          message: "Default parameter values are not allowed; pass every parameter explicitly.",
        },
      ],
    },
  },
  {
    // The network and history hooks must capture and reinstall prototype
    // methods to wrap them. Every captured method is invoked with an explicit
    // `.call(this, ...)`, so the unbound-method warning does not apply.
    files: ["src/hooks/navigation.ts", "src/hooks/network.ts"],
    rules: { "@typescript-eslint/unbound-method": "off" },
  },
  {
    files: ["scripts/**/*.mjs"],
    ...tseslint.configs.disableTypeChecked,
    languageOptions: { globals: globals.node },
  },
]);
