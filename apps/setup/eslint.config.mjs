// The desktop app's rules (apps/desktop/eslint.config.mjs) without React's: typescript-eslint's
// type-checked rules on top of ESLint's recommended ones.
import js from '@eslint/js'
import perfectionist from 'eslint-plugin-perfectionist'
import unusedImports from 'eslint-plugin-unused-imports'
import globals from 'globals'
import tseslint from 'typescript-eslint'

const NATURAL = { order: 'asc', type: 'natural' }
const IMPORT_BLOCKS = ['side-effect', 'builtin', 'external', 'internal', 'parent', 'sibling', 'index']

export default tseslint.config(
  { ignores: ['node_modules/**', 'out/**', 'dist/**'] },
  js.configs.recommended,
  {
    extends: [...tseslint.configs.strictTypeChecked],
    files: ['**/*.ts'],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
      parserOptions: { project: ['./tsconfig.json'], tsconfigRootDir: import.meta.dirname }
    },
    plugins: { 'unused-imports': unusedImports, perfectionist },
    rules: {
      curly: ['error', 'all'],
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
      '@typescript-eslint/consistent-type-imports': ['error', { prefer: 'type-imports' }],
      'unused-imports/no-unused-imports': 'error',
      'perfectionist/sort-imports': ['error', { ...NATURAL, groups: IMPORT_BLOCKS }],
      'perfectionist/sort-named-imports': ['error', NATURAL]
    }
  },
  {
    files: ['**/*.{js,mjs,cjs}'],
    languageOptions: { globals: { ...globals.node } }
  }
)
