import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { ProposalView } from './api-types';
import { ProposalEditor } from './ProposalEditor';

function proposal(): ProposalView {
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
    status: 'blocked_for_approval',
    latest_validation: null,
    validations: [],
    blocking_issues: [],
    decision: null,
    decision_current: false,
    case_version: 7,
    snapshot: {
      case_id: 'V04',
      user_text: 'Проверить согласие',
      requested_fact_keys: ['alternative_acceptance'],
      money: null,
      context: {},
      catalog: [
        {
          case_id: 'V04',
          source_id: 'source-1',
          kind: 'document',
          locator: 'source-1',
          title: 'Переписка',
          trust: 'synthetic',
          position: 0,
          included: true,
          version_id: 'version-1',
          text: 'Если тихо, согласен. Ответа нет.',
          text_sha256: 'hash',
        },
      ],
    },
    body: {
      case_id: 'V04',
      facts: [
        {
          key: 'alternative_acceptance',
          value: 'confirmed',
          evidence: [{ source_id: 'source-1', fragments: ['Если тихо, согласен.', 'Ответа нет.'] }],
        },
      ],
      timeline: [
        {
          event_type: 'alternative_accepted',
          time: { value: '2026-10-08T12:27:00+03:00', role: 'received' },
          evidence: [],
        },
      ],
      readiness: 'request_evidence',
      follow_up: { action: 'ask_user', locator: null, requirements: ['quiet_room'] },
    },
  };
}

describe('ProposalEditor', () => {
  it('saves a structured correction, separate fragments and an immutable revision reason', async () => {
    const user = userEvent.setup();
    const save = vi.fn().mockResolvedValue(undefined);
    render(<ProposalEditor proposal={proposal()} busy={false} onSave={save} onCancel={vi.fn()} />);

    await user.selectOptions(screen.getByLabelText('Факт 1: значение'), 'conditional');
    await user.click(screen.getByRole('button', { name: 'Удалить событие 1' }));
    await user.type(screen.getByLabelText('Причина исправления'), 'Согласие зависит от условий.');
    await user.click(screen.getByRole('button', { name: 'Сохранить новую версию' }));

    expect(save).toHaveBeenCalledOnce();
    expect(save).toHaveBeenCalledWith(
      expect.objectContaining({
        facts: [
          {
            key: 'alternative_acceptance',
            value: 'conditional',
            evidence: [
              { source_id: 'source-1', fragments: ['Если тихо, согласен.', 'Ответа нет.'] },
            ],
          },
        ],
        timeline: [],
      }),
      'Согласие зависит от условий.',
      7,
    );
  });

  it('requires a nonblank reason before creating a revision', async () => {
    const save = vi.fn();
    render(<ProposalEditor proposal={proposal()} busy={false} onSave={save} onCancel={vi.fn()} />);
    fireEvent.submit(screen.getByRole('form', { name: 'Исправление разбора' }));
    expect(save).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('Укажите причину');
  });

  it('keeps the version read when editing began, even after the parent refreshes', async () => {
    const user = userEvent.setup();
    const save = vi.fn().mockResolvedValue(undefined);
    const initial = proposal();
    const { rerender } = render(
      <ProposalEditor proposal={initial} busy={false} onSave={save} onCancel={vi.fn()} />,
    );
    await user.type(screen.getByLabelText('Причина исправления'), 'Проверено вручную');
    rerender(
      <ProposalEditor
        proposal={{ ...initial, case_version: 8 }}
        busy={false}
        onSave={save}
        onCancel={vi.fn()}
      />,
    );
    await user.click(screen.getByRole('button', { name: 'Сохранить новую версию' }));
    expect(save).toHaveBeenCalledWith(expect.anything(), 'Проверено вручную', 7);
  });

  it('retains the draft after a failed save and lets the operator cancel explicitly', async () => {
    const user = userEvent.setup();
    const save = vi.fn().mockRejectedValue(new Error('Conflict'));
    const cancel = vi.fn();
    render(<ProposalEditor proposal={proposal()} busy={false} onSave={save} onCancel={cancel} />);
    const fragment = screen.getByLabelText('Факт 1 · доказательство 1 · фрагмент 2');
    await user.clear(fragment);
    await user.type(fragment, 'Уточнённый отдельный фрагмент');
    await user.type(screen.getByLabelText('Причина исправления'), 'Уточнил доказательство');
    await user.click(screen.getByRole('button', { name: 'Сохранить новую версию' }));
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('Правки сохранены в редакторе'),
    );
    expect(fragment).toHaveValue('Уточнённый отдельный фрагмент');
    expect(screen.getByLabelText('Причина исправления')).toHaveValue('Уточнил доказательство');
    await user.click(screen.getByRole('button', { name: 'Отменить исправление' }));
    expect(cancel).toHaveBeenCalledOnce();
  });
});
