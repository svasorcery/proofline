import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { CaseInput } from './CaseInput';
import type { CaseDetail } from './api-types';

const data: CaseDetail = {
  id: 'V01',
  title: 'Test',
  user_text: 'Text',
  requested_fact_keys: [],
  money: null,
  context: {},
  status: 'unanalysed',
  version: 1,
  content_revision: 1,
  current_proposal_revision_id: null,
  analysis_running: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  sources: [
    {
      case_id: 'V01',
      source_id: 's',
      kind: 'provider_log',
      locator: 'provider://test',
      title: 'Журнал',
      trust: 'synthetic',
      position: 0,
      included: false,
      current_version_id: null,
      versions: [],
    },
  ],
};

it('preserves a provider-log source kind when editing metadata', async () => {
  const write = vi.fn().mockResolvedValue(undefined);
  render(<CaseInput data={data} busy={false} write={write} onRead={vi.fn()} />);
  await userEvent.click(screen.getByText('Обращение и источники'));
  await userEvent.click(screen.getByRole('button', { name: 'Изменить источник' }));
  expect(screen.getByLabelText('Тип источника')).toHaveValue('provider_log');
  await userEvent.click(screen.getByRole('button', { name: 'Сохранить метаданные' }));
  expect(write).toHaveBeenCalledWith(
    '/cases/V01/sources/s',
    'PATCH',
    expect.objectContaining({ kind: 'provider_log' }),
  );
});
