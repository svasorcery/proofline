import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ProposalView } from './api-types';
import { DecisionForm } from './DecisionForm';

function proposal(overrides: Partial<ProposalView> = {}): ProposalView {
  return {
    id: 'proposal-1',
    case_id: 'V04',
    analysis_run_id: 'run-1',
    parent_revision_id: null,
    origin: 'model',
    content_revision: 1,
    input_digest: 'digest',
    source_bindings: {},
    body_sha256: 'hash',
    reason: null,
    created_at: '2026-11-13T08:00:00Z',
    active: true,
    stale: false,
    validation_current: true,
    status: 'needs_operator_review',
    latest_validation: null,
    validations: [],
    blocking_issues: [],
    decision: null,
    decision_current: false,
    case_version: 7,
    snapshot: {
      case_id: 'V04',
      user_text: 'Обращение',
      requested_fact_keys: [],
      money: null,
      context: {},
      catalog: [],
    },
    body: {
      case_id: 'V04',
      facts: [],
      timeline: [],
      readiness: 'human_review',
      follow_up: { action: 'none', locator: null, requirements: [] },
    },
    ...overrides,
  };
}

describe('DecisionForm', () => {
  it('sends an explicit approval with comment and the version read by the operator', async () => {
    const user = userEvent.setup();
    const write = vi.fn().mockResolvedValue(undefined);
    render(<DecisionForm proposal={proposal()} disabled={false} write={write} />);
    await user.type(
      screen.getByLabelText('Комментарий к решению'),
      'Проверены смысл и доказательства',
    );
    await user.click(screen.getByRole('button', { name: 'Подтвердить разбор' }));
    expect(write).toHaveBeenCalledWith(
      '/proposals/proposal-1/decision',
      'POST',
      {
        kind: 'approve',
        reason: 'Проверены смысл и доказательства',
        expected_case_version: 7,
      },
      true,
    );
  });

  it('allows rejection with reason when blocking issues prevent approval', async () => {
    const user = userEvent.setup();
    const write = vi.fn().mockResolvedValue(undefined);
    render(
      <DecisionForm
        proposal={proposal({
          blocking_issues: [{ code: 'INVALID', path: ['facts'], message: 'Ошибка' }],
        })}
        disabled={false}
        write={write}
      />,
    );
    expect(screen.getByRole('button', { name: 'Подтвердить разбор' })).toBeDisabled();
    await user.type(screen.getByLabelText('Комментарий к решению'), 'Противоречие в согласии');
    await user.click(screen.getByRole('button', { name: 'Отклонить разбор' }));
    expect(write).toHaveBeenCalledWith(
      expect.any(String),
      'POST',
      {
        kind: 'reject',
        reason: 'Противоречие в согласии',
        expected_case_version: 7,
      },
      true,
    );
  });

  it('preserves a comment across a version change and requires explicit acknowledgement', async () => {
    const user = userEvent.setup();
    const write = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <DecisionForm proposal={proposal()} disabled={false} write={write} />,
    );
    await user.type(
      screen.getByLabelText('Комментарий к решению'),
      'Проверил первоначальную версию',
    );
    rerender(
      <DecisionForm proposal={proposal({ case_version: 8 })} disabled={false} write={write} />,
    );
    expect(screen.getByLabelText('Комментарий к решению')).toHaveValue(
      'Проверил первоначальную версию',
    );
    expect(screen.getByRole('button', { name: 'Подтвердить разбор' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Отклонить разбор' })).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('Обращение изменилось');
    expect(write).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Проверил обновлённые данные' }));
    await user.click(screen.getByRole('button', { name: 'Подтвердить разбор' }));
    expect(write).toHaveBeenCalledWith(
      expect.any(String),
      'POST',
      {
        kind: 'approve',
        reason: 'Проверил первоначальную версию',
        expected_case_version: 8,
      },
      true,
    );
  });

  it('uses the latest version when the form is still empty during refresh', async () => {
    const user = userEvent.setup();
    const write = vi.fn().mockResolvedValue(undefined);
    const { rerender } = render(
      <DecisionForm proposal={proposal()} disabled={false} write={write} />,
    );
    rerender(
      <DecisionForm proposal={proposal({ case_version: 8 })} disabled={false} write={write} />,
    );
    await user.type(screen.getByLabelText('Комментарий к решению'), 'Проверил обновлённые данные');
    await user.click(screen.getByRole('button', { name: 'Подтвердить разбор' }));
    expect(write).toHaveBeenCalledWith(
      expect.any(String),
      'POST',
      {
        kind: 'approve',
        reason: 'Проверил обновлённые данные',
        expected_case_version: 8,
      },
      true,
    );
    expect(
      screen.queryByRole('button', { name: 'Проверил обновлённые данные' }),
    ).not.toBeInTheDocument();
  });

  it('requires a comment and retains it after a failed write', async () => {
    const user = userEvent.setup();
    const write = vi.fn().mockRejectedValue(new Error('Conflict'));
    render(<DecisionForm proposal={proposal()} disabled={false} write={write} />);
    fireEvent.submit(screen.getByRole('form', { name: 'Решение по разбору' }));
    expect(write).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText('Комментарий к решению'), 'Мой комментарий');
    await user.click(screen.getByRole('button', { name: 'Подтвердить разбор' }));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Комментарий сохранён'),
    );
    expect(screen.getByLabelText('Комментарий к решению')).toHaveValue('Мой комментарий');
  });
});
