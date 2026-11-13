import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, command } from './api';
import type { CaseDetail, ProposalView, RunView } from './api-types';
import { CaseWorkspace } from './CaseWorkspace';

vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  api: vi.fn(),
  command: vi.fn(),
}));

const data: CaseDetail = {
  id: 'V01',
  title: 'Test case',
  user_text: 'Synthetic text',
  requested_fact_keys: [],
  context: {},
  money: null,
  version: 1,
  content_revision: 1,
  status: 'unanalysed',
  analysis_running: false,
  current_proposal_revision_id: null,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  sources: [
    {
      case_id: 'V01',
      source_id: 's',
      title: 'Source',
      kind: 'document',
      locator: 'source://s',
      trust: 'synthetic',
      included: true,
      position: 0,
      current_version_id: 'v1',
      versions: [
        { id: 'v1', sequence: 1, text_sha256: 'hash', created_at: '2026-01-01T00:00:00Z' },
      ],
    },
  ],
};

function run(id: string, status: 'failed' | 'succeeded', startedAt: string): RunView {
  return {
    id,
    case_id: data.id,
    status,
    mode: 'replay',
    started_at: startedAt,
    finished_at: startedAt,
    base_case_version: 1,
    content_revision: 1,
    input_digest: 'digest',
    proposal_revision_id: null,
    raw_output: null,
    generation_profile: {},
    usage: null,
    error: status === 'failed' ? { code: 'PROCESS_INTERRUPTED' } : null,
    snapshot: {
      case_id: data.id,
      user_text: data.user_text,
      requested_fact_keys: [],
      money: null,
      context: {},
      catalog: [],
    },
  };
}

beforeEach(() => vi.clearAllMocks());

describe('CaseWorkspace', () => {
  it.each(['model', 'operator'] as const)(
    'shows Live provenance for a selected %s revision while Replay is selected',
    async (origin) => {
      const sourceRun: RunView = {
        ...run('live-source', 'succeeded', '2026-01-01T00:00:00Z'),
        mode: 'live',
        proposal_revision_id: 'model-result',
        generation_profile: { model: 'actual-live-model' },
      };
      const root: ProposalView = {
        id: 'model-result',
        case_id: 'V01',
        analysis_run_id: sourceRun.id,
        parent_revision_id: null,
        origin: 'model',
        content_revision: 1,
        input_digest: 'digest',
        snapshot: sourceRun.snapshot,
        source_bindings: {},
        body_sha256: 'hash',
        body: {
          case_id: 'V01',
          facts: [],
          timeline: [],
          readiness: 'human_review',
          follow_up: { action: 'none', locator: null, requirements: [] },
        },
        reason: null,
        created_at: sourceRun.started_at,
        active: true,
        stale: false,
        validation_current: true,
        status: 'needs_operator_review',
        latest_validation: null,
        validations: [],
        blocking_issues: [],
        decision: null,
        decision_current: false,
        case_version: 2,
      };
      const child: ProposalView = {
        ...root,
        id: 'operator-result',
        origin: 'operator',
        parent_revision_id: root.id,
        analysis_run_id: null,
      };
      const current = origin === 'operator' ? child : root;
      vi.mocked(api).mockImplementation(async (path) =>
        path === '/capabilities'
          ? { live_available: false, live_reason: 'LIVE_DISABLED', live_model: null }
          : path === '/cases/V01'
            ? { ...data, current_proposal_revision_id: current.id }
            : path.endsWith('/proposals')
              ? [root, child]
              : path.endsWith('/runs')
                ? [sourceRun]
                : [],
      );
      render(<CaseWorkspace caseId="V01" />);
      const text =
        origin === 'operator'
          ? /Основа правки: Live · новый вызов модели · actual-live-model/
          : /Происхождение: Live · новый вызов модели · actual-live-model/;
      expect(await screen.findByText(text)).toBeVisible();
      expect(screen.getByRole('combobox', { name: 'Режим анализа' })).toHaveValue('replay');
      expect(command).not.toHaveBeenCalled();
    },
  );

  it('defaults to replay and requires an explicit selection before a single live request', async () => {
    const user = userEvent.setup();
    const caps = { live_available: true, live_model: 'configured-test-model', live_reason: null };
    vi.mocked(api).mockImplementation(async (path) =>
      path === '/capabilities' ? caps : path === '/cases/V01' ? data : [],
    );
    vi.mocked(command).mockResolvedValue(undefined);
    render(<CaseWorkspace caseId="V01" />);
    const mode = await screen.findByRole('combobox', { name: 'Режим анализа' });
    expect(mode).toHaveValue('replay');
    expect(screen.getByRole('button', { name: 'Запустить Replay' })).toBeDisabled();
    await user.selectOptions(mode, 'live');
    expect(screen.getByText('configured-test-model')).toBeVisible();
    expect(screen.getByText(/Доступ и совместимость модели ещё не проверены/)).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Запустить Live' }));
    await waitFor(() => expect(command).toHaveBeenCalledTimes(1));
    expect(command).toHaveBeenCalledWith(
      '/cases/V01/runs',
      'POST',
      { mode: 'live', expected_case_version: 1 },
      expect.any(String),
    );
  });

  it('explains missing live access and never enables the mode', async () => {
    vi.mocked(api).mockImplementation(async (path) =>
      path === '/capabilities'
        ? { live_available: false, live_model: null, live_reason: 'LIVE_KEY_MISSING' }
        : path === '/cases/V01'
          ? data
          : [],
    );
    render(<CaseWorkspace caseId="V01" />);
    await screen.findByRole('heading', { name: 'Test case' });
    expect(screen.getByRole('option', { name: 'Live — новый вызов' })).toBeDisabled();
    expect(screen.getByText(/На сервере не задан API-ключ/)).toBeVisible();
    expect(command).not.toHaveBeenCalled();
  });

  it('keeps live selected after a provider failure without starting replay', async () => {
    const user = userEvent.setup();
    let failed = false;
    const failedRun = {
      ...run('live-failure', 'failed', '2026-01-02T00:00:00Z'),
      mode: 'live',
      error: { code: 'PROVIDER_RATE_LIMITED' },
      generation_profile: { model: 'actual-test-model' },
    };
    vi.mocked(api).mockImplementation(async (path) =>
      path === '/capabilities'
        ? { live_available: true, live_model: 'configured-test-model', live_reason: null }
        : path === '/cases/V01'
          ? data
          : path.endsWith('/runs') && failed
            ? [failedRun]
            : [],
    );
    vi.mocked(command).mockImplementation(async () => {
      failed = true;
    });
    render(<CaseWorkspace caseId="V01" />);
    const mode = await screen.findByRole('combobox', { name: 'Режим анализа' });
    await user.selectOptions(mode, 'live');
    await user.click(screen.getByRole('button', { name: 'Запустить Live' }));
    await screen.findByRole('alert');
    expect(mode).toHaveValue('live');
    expect(command).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('alert')).toHaveTextContent('лимит');
  });

  it('does not poll while a write or its post-save reload is busy', async () => {
    vi.useFakeTimers();
    try {
      const running = { ...data, analysis_running: true };
      let finishWrite: (() => void) | undefined;
      let reload = false;
      const delayed: (() => void)[] = [];
      vi.mocked(api).mockImplementation(async (path) => {
        const response = path === '/cases/V01' ? running : [];
        if (reload) return new Promise((resolve) => delayed.push(() => resolve(response)));
        return response;
      });
      vi.mocked(command).mockImplementation(
        () =>
          new Promise((resolve) => {
            finishWrite = () => resolve(undefined);
          }),
      );
      await act(async () => render(<CaseWorkspace caseId="V01" />));
      expect(api).toHaveBeenCalledTimes(5);
      fireEvent.click(screen.getByText('Обращение и источники'));
      await act(async () =>
        fireEvent.click(screen.getByRole('checkbox', { name: 'Включить Source' })),
      );
      expect(command).toHaveBeenCalledTimes(1);

      await act(async () => vi.advanceTimersByTimeAsync(2000));
      expect(api).toHaveBeenCalledTimes(5);

      reload = true;
      await act(async () => finishWrite?.());
      expect(delayed).toHaveLength(5);
      expect(api).toHaveBeenCalledTimes(10);
      await act(async () => vi.advanceTimersByTimeAsync(2000));
      expect(api).toHaveBeenCalledTimes(10);
      expect(screen.getByRole('checkbox', { name: 'Включить Source' })).toBeDisabled();

      await act(async () => delayed.forEach((resolve) => resolve()));
      expect(screen.getByRole('checkbox', { name: 'Включить Source' })).toBeEnabled();
      reload = false;
      await act(async () => vi.advanceTimersByTimeAsync(1500));
      expect(api).toHaveBeenCalledTimes(15);
      expect(command).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps controls locked after saving until the new source version is loaded', async () => {
    const user = userEvent.setup();
    const updated: CaseDetail = {
      ...data,
      version: 2,
      sources: data.sources.map((source) => ({
        ...source,
        current_version_id: 'v2',
        versions: [
          ...source.versions,
          { id: 'v2', sequence: 2, text_sha256: 'new-hash', created_at: '2026-01-02T00:00:00Z' },
        ],
      })),
    };
    let saved = false;
    const delayed: (() => void)[] = [];
    vi.mocked(api).mockImplementation(async (path) => {
      if (path.endsWith('/versions/v2')) {
        return {
          id: 'v2',
          case_id: 'V01',
          source_id: 's',
          sequence: 2,
          text: 'New source text',
          text_sha256: 'new-hash',
          created_at: '2026-01-02T00:00:00Z',
        };
      }
      const response = path === '/cases/V01' ? (saved ? updated : data) : [];
      if (saved) return new Promise((resolve) => delayed.push(() => resolve(response)));
      return response;
    });
    vi.mocked(command).mockImplementation(async () => {
      saved = true;
    });
    render(<CaseWorkspace caseId="V01" />);
    await screen.findByRole('heading', { name: 'Test case' });
    await user.click(screen.getByText('Обращение и источники'));
    await user.click(screen.getByRole('button', { name: 'Изменить источник' }));
    await user.type(screen.getByRole('textbox', { name: 'Текст новой версии' }), 'New source text');
    await user.click(screen.getByRole('button', { name: 'Создать версию текста' }));
    await waitFor(() => expect(delayed).toHaveLength(5));
    expect(command).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('checkbox', { name: 'Включить Source' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Создать версию текста' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Открыть текст' })).toBeDisabled();

    await act(async () => delayed.forEach((resolve) => resolve()));
    await waitFor(() =>
      expect(screen.queryByRole('textbox', { name: 'Текст новой версии' })).not.toBeInTheDocument(),
    );
    await user.click(screen.getByRole('button', { name: 'Открыть текст' }));
    expect(await screen.findByTestId('source-text')).toHaveTextContent('New source text');
    expect(api).toHaveBeenCalledWith('/cases/V01/sources/s/versions/v2', expect.any(Object));
    expect(command).toHaveBeenCalledTimes(1);
  });

  it('keeps an uncertain write blocked when a refresh started before that write finishes late', async () => {
    const user = userEvent.setup();
    let deferReads = false;
    const delayed: (() => void)[] = [];
    vi.mocked(api).mockImplementation(async (path) => {
      const response = path === '/cases/V01' ? data : [];
      if (deferReads) return new Promise((resolve) => delayed.push(() => resolve(response)));
      return response;
    });
    vi.mocked(command).mockRejectedValue(
      new ApiError('NETWORK_ERROR', 'Response lost', { outcomeUnknown: true }),
    );
    render(<CaseWorkspace caseId="V01" />);
    await screen.findByRole('heading', { name: 'Test case' });

    deferReads = true;
    await user.click(screen.getByRole('button', { name: 'Обновить данные' }));
    await waitFor(() => expect(delayed).toHaveLength(5));
    await user.click(screen.getByText('Обращение и источники'));
    const included = screen.getByRole('checkbox', { name: 'Включить Source' });
    await user.click(included);
    await screen.findByText('Проверьте историю перед новым действием');
    expect(included).toBeDisabled();

    await act(async () => delayed.forEach((resolve) => resolve()));
    expect(screen.getByText('Проверьте историю перед новым действием')).toBeVisible();
    expect(included).toBeDisabled();
    expect(command).toHaveBeenCalledTimes(1);

    // Only a new, explicit read after the unknown outcome acknowledges it.
    deferReads = false;
    await user.click(screen.getByRole('button', { name: 'Обновить данные' }));
    await waitFor(() => expect(included).toBeEnabled());
    expect(screen.queryByText('Проверьте историю перед новым действием')).not.toBeInTheDocument();
    expect(command).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['failed', 'succeeded', false],
    ['succeeded', 'failed', true],
  ] as const)(
    'uses the newest run for the error banner: %s then %s',
    async (first, last, showError) => {
      const runs = [
        run('older', first, '2026-01-01T00:00:00Z'),
        run('newer', last, '2026-01-02T00:00:00Z'),
      ];
      vi.mocked(api).mockImplementation(async (path) =>
        path === '/cases/V01' ? data : path.endsWith('/runs') ? runs : [],
      );
      render(<CaseWorkspace caseId="V01" />);
      await screen.findByRole('heading', { name: 'Test case' });
      if (showError)
        expect(screen.getByRole('alert')).toHaveTextContent('Анализ завершился ошибкой');
      else expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    },
  );
});
