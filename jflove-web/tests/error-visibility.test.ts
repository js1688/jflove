/**
 * 反馈修复回归：接口报错必须让用户看到（v1.5.0）
 *
 * 用户反馈：「web 端隐藏了不少接口的响应信息，比如添加相同用户应该是报错的，
 * 安卓端有提示，web 端没有」。
 *
 * 实测根因：管理员页面里多处 handler 是**裸 await** —— 接口抛错后异常被吞掉，
 * 弹窗照样关闭、界面毫无反应。安卓端有提示，所以用户感知到差异。
 *
 * 这里做**静态扫描**而不是逐个断言组件行为：这类回归是"新写的页面又忘了
 * try/catch"，只有扫全量源码才拦得住。
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';

const SRC = join(__dirname, '..', 'src');

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

/** 收集 handler 内「裸 await 服务调用」（所在 block 里没有 try/catch） */
function findUnprotectedServiceCalls(): string[] {
  const offenders: string[] = [];

  for (const file of walk(SRC).filter((f) => f.endsWith('.tsx'))) {
    const lines = readFileSync(file, 'utf8').split(/\r?\n/);
    let handler: string | null = null;
    let braceDepth = 0;
    let guarded = false;

    lines.forEach((line, index) => {
      if (/const\s+(handle\w+|on\w+)\s*=\s*async/.exec(line)) {
        handler = /const\s+(\w+)/.exec(line)?.[1] ?? 'handler';
        braceDepth = 0;
        guarded = false;
      }
      if (!handler) return;

      braceDepth += (line.match(/\{/g) ?? []).length - (line.match(/\}/g) ?? []).length;
      if (/\btry\s*\{/.test(line) || /\bcatch\s*\(/.test(line)) guarded = true;

      const isBareCall =
        /await\s+\w+[Ss]ervice\./.test(line) && !/catch/.test(line) && !/\.catch\(/.test(line);
      if (isBareCall && !guarded) {
        offenders.push(`${relative(SRC, file)}:${index + 1} ${line.trim()}`);
      }

      if (braceDepth <= -1) handler = null;
    });
  }

  return offenders;
}

describe('Web 端接口错误必须可见', () => {
  it('handler 内不得出现「裸 await 服务调用」（缺 try/catch）', () => {
    const offenders = findUnprotectedServiceCalls();
    expect(
      offenders,
      '以下位置接口报错会被静默吞掉，用户看不到任何提示；'
        + '请用 try/catch 包住并用 toast.error 展示 e.message：\n'
        + offenders.join('\n'),
    ).toEqual([]);
  });

  it('管理员三个页面的写操作都有错误提示（抽查，防止扫描漏掉）', () => {
    for (const rel of [
      'pages/admin/AdminUsersPage.tsx',
      'pages/admin/AdminDisksPage.tsx',
      'pages/admin/AdminPermissionsPage.tsx',
    ]) {
      const text = readFileSync(join(SRC, rel), 'utf8');
      expect(text, `${rel} 缺少 toast.error 错误提示`).toContain('toast.error');
    }
  });
});
