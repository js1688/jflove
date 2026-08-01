import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { PageHeader } from '../../src/components/PageHeader';

describe('PageHeader', () => {
  it('渲染标题', () => {
    render(
      <MemoryRouter>
        <PageHeader title="测试标题" />
      </MemoryRouter>,
    );
    expect(screen.getByText('测试标题')).toBeInTheDocument();
  });

  it('渲染返回按钮', () => {
    const onBack = () => {};
    render(
      <MemoryRouter>
        <PageHeader title="测试" onBack={onBack} />
      </MemoryRouter>,
    );
    expect(screen.getByLabelText('返回')).toBeInTheDocument();
  });

  it('渲染操作按钮', () => {
    render(
      <MemoryRouter>
        <PageHeader title="测试" actions={<button>操作</button>} />
      </MemoryRouter>,
    );
    expect(screen.getByText('操作')).toBeInTheDocument();
  });
});
