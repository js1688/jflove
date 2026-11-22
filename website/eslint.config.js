import js from '@eslint/js';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';

/**
 * 官网 ESLint 配置（扁平配置）。
 *
 * 只检查我们自己的源码与测试，忽略构建产物。
 * 规则取向：抓真问题（未使用变量、hooks 依赖、any 滥用），不搞格式警察 ——
 * 格式交给 prettier / 编辑器，lint 不应该因为空格报错。
 */
export default tseslint.config(
  {
    ignores: ['dist/**', 'node_modules/**', 'content/**', 'public/**', 'design/**'],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}', 'tests/**/*.{ts,tsx}'],
    plugins: { 'react-hooks': reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
      '@typescript-eslint/no-explicit-any': 'error',
    },
  },
);
