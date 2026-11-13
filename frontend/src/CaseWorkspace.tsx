import { useEffect, useRef, useState } from 'react';
import { api, ApiError, command, newKey } from './api';
import type {
  CaseDetail,
  ProposalView,
  ReplayOption,
  RunView,
  SnapshotSource,
  SpanView,
  SourceView,
  SourceVersionView,
} from './api-types';
import { CaseInput } from './CaseInput';
import { DecisionForm } from './DecisionForm';
import { ProposalEditor } from './ProposalEditor';
import { ProposalRead } from './ProposalRead';
import { SourceReader } from './SourceReader';
import type { ReaderDocument } from './SourceReader';
import { formatMoney, label } from './labels';
import { ErrorNotice, JsonDetails, Loading, Status, timestamp } from './shared';

interface Workspace {
  data: CaseDetail;
  options: ReplayOption[];
  proposals: ProposalView[];
  runs: RunView[];
}
interface PendingWrite {
  path: string;
  method: 'POST' | 'PATCH';
  body: unknown;
  key: string;
}

async function readWorkspace(base: string, signal: AbortSignal): Promise<Workspace> {
  const get = <T,>(path: string) => api<T>(path, { signal });
  const [data, options, proposals, runs] = await Promise.all([
    get<CaseDetail>(base),
    get<ReplayOption[]>(`${base}/replay-options`),
    get<ProposalView[]>(`${base}/proposals`),
    get<RunView[]>(`${base}/runs`),
  ]);
  return { data, options, proposals, runs };
}

export function CaseWorkspace({ caseId }: { caseId: string }) {
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [pending, setPending] = useState<PendingWrite | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const refreshAcknowledged = useRef(false);
  const workspaceRequest = useRef<AbortController | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [replayId, setReplayId] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);
  const editor = editingId !== null;
  const readerRequest = useRef<AbortController | null>(null);
  const [doc, setDoc] = useState<ReaderDocument | null>(null);
  const [panel, setPanel] = useState('analysis');
  const [notice, setNotice] = useState('');
  const base = `/cases/${encodeURIComponent(caseId)}`;

  useEffect(() => () => readerRequest.current?.abort(), []);

  useEffect(() => {
    const ac = new AbortController();
    workspaceRequest.current = ac;
    readWorkspace(base, ac.signal)
      .then((loaded) => {
        if (!ac.signal.aborted) {
          setWorkspace(loaded);
          if (refreshAcknowledged.current) {
            setUncertain(false);
            refreshAcknowledged.current = false;
          }
        }
      })
      .catch((e: unknown) => {
        if (!ac.signal.aborted) setError(e);
      });
    return () => ac.abort();
  }, [base, refresh]);

  useEffect(() => {
    if (busy || !workspace?.data.analysis_running) return;
    const timer = window.setTimeout(() => setRefresh((n) => n + 1), 1500);
    return () => window.clearTimeout(timer);
  }, [workspace, busy]);

  async function perform(request: PendingWrite | (Omit<PendingWrite, 'key'> & { key?: string })) {
    if (busyRef.current) throw new Error('A command is already in progress');
    workspaceRequest.current?.abort();
    busyRef.current = true;
    setBusy(true);
    setError(null);
    setNotice('');
    try {
      await command(request.path, request.method, request.body, request.key);
      setPending(null);
      if (!editingId) setSelected(null);
      workspaceRequest.current?.abort();
      const ac = new AbortController();
      workspaceRequest.current = ac;
      try {
        const loaded = await readWorkspace(base, ac.signal);
        if (!ac.signal.aborted) setWorkspace(loaded);
      } catch (e) {
        setUncertain(true);
        refreshAcknowledged.current = false;
        throw e;
      }
      setNotice('Запрос сохранён. История обращения обновлена.');
    } catch (e) {
      setError(e);
      if (e instanceof ApiError && e.outcomeUnknown && !request.key) {
        refreshAcknowledged.current = false;
        setUncertain(true);
      }
      if (e instanceof ApiError && e.outcomeUnknown && request.key)
        setPending({ ...request, key: request.key });
      else if (e instanceof ApiError && !e.outcomeUnknown) setPending(null);
      throw e;
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  async function write(path: string, method: 'POST' | 'PATCH', body: unknown, idempotent = false) {
    if (pending || uncertain) throw new Error('Resolve the pending command first');
    await perform({ path, method, body, ...(idempotent ? { key: newKey() } : {}) });
  }

  function openEvidence(source: SnapshotSource, spans: SpanView[], focus?: SpanView) {
    readerRequest.current?.abort();
    if (source.text === null || source.version_id === null || source.text_sha256 === null) return;
    setDoc({
      source_id: source.source_id,
      version_id: source.version_id,
      text_sha256: source.text_sha256,
      text: source.text,
      title: source.title,
      origin: 'snapshot',
      spans,
      focus,
    });
    setPanel('source');
  }

  async function openCurrent(source: SourceView) {
    readerRequest.current?.abort();
    const ac = new AbortController();
    readerRequest.current = ac;
    let version: SourceVersionView;
    try {
      version = await api<SourceVersionView>(
        `${base}/sources/${encodeURIComponent(source.source_id)}/versions/${encodeURIComponent(source.current_version_id!)}`,
        { signal: ac.signal },
      );
    } catch (e) {
      if (ac.signal.aborted) return;
      throw e;
    }
    if (ac.signal.aborted) return;
    setDoc({
      source_id: source.source_id,
      version_id: version.id,
      text_sha256: version.text_sha256,
      text: version.text,
      title: source.title,
      origin: 'current',
      spans: [],
    });
    setPanel('source');
  }

  if (!workspace)
    return (
      <main className="page" id="main">
        <a href="/" className="back-link">
          ← Обращения
        </a>
        {error ? (
          <>
            <ErrorNotice error={error} />
            <button
              className="secondary"
              onClick={() => {
                setError(null);
                setRefresh((n) => n + 1);
              }}
            >
              Повторить загрузку
            </button>
          </>
        ) : (
          <Loading />
        )}
      </main>
    );
  const { data, options, proposals, runs } = workspace;
  const proposal = proposals.find(
    (p) => p.id === (editingId ?? selected ?? data.current_proposal_revision_id),
  );
  const chosenReplay = options.find((o) => o.replay_id === replayId) ?? options[0];
  const disabled = busy || pending !== null || uncertain;
  const latestRun = runs.at(-1);

  return (
    <main className="page workspace-page" id="main">
      <a href="/" className="back-link">
        ← Все обращения
      </a>
      <div className="page-heading">
        <div>
          <p className="eyebrow">
            {caseId.startsWith('V0') ? `Учебный кейс ${caseId}` : 'Учебное обращение'}
          </p>
          <h1>{data.title}</h1>
          <div className="inline">
            <Status status={data.status} />
            {data.analysis_running ? <span className="running">Анализ выполняется</span> : null}
            <span className="subtle">Обновлено {timestamp(data.updated_at)}</span>
          </div>
        </div>
        <button
          className="secondary"
          disabled={busy}
          onClick={() => {
            setError(null);
            refreshAcknowledged.current = uncertain;
            setRefresh((n) => n + 1);
          }}
        >
          Обновить данные
        </button>
      </div>
      {data.money ? (
        <p className="money-context">
          Справочный контекст: <strong>{formatMoney(data.money)}</strong>
          <span> Не является одобренной суммой возврата.</span>
        </p>
      ) : null}
      {error ? <ErrorNotice error={error} /> : null}
      {uncertain ? (
        <div className="notice warning">
          <strong>Проверьте историю перед новым действием</strong>
          <p>
            Ответ на сохранение потерян. Нажмите «Обновить данные» и сравните сохранённую версию с
            черновиком. Повторное сохранение может создать ещё одну версию.
          </p>
        </div>
      ) : null}
      {pending ? (
        <div className="notice warning">
          <strong>Исход запроса неизвестен</strong>
          <p>
            Проверка использует прежний ключ и исходное тело запроса. Новый анализ или решение не
            создаются.
          </p>
          <button
            className="secondary"
            disabled={busy}
            onClick={() => {
              void perform(pending).catch(() => {});
            }}
          >
            Проверить результат запроса
          </button>
        </div>
      ) : null}
      <p className="sr-only" role="status">
        {notice}
      </p>
      <CaseInput data={data} busy={disabled} write={write} onRead={openCurrent} />
      <section className="run-toolbar" aria-label="Запуск анализа">
        <div className="run-mode">
          <span className="mode-tag">Replay</span>
          <strong>Сохранённая запись</strong>
          <small>Новых вызовов модели нет</small>
        </div>
        <label className="replay-select">
          Запись для текущего входа
          <select
            aria-label="Запись Replay"
            value={chosenReplay?.replay_id ?? ''}
            disabled={disabled || !options.length}
            onChange={(e) => setReplayId(e.target.value)}
          >
            {options.length ? (
              options.map((o) => (
                <option key={o.replay_id} value={o.replay_id}>
                  {o.condition} · {o.recorded_on} · {o.model}
                </option>
              ))
            ) : (
              <option value="">Совместимых записей нет</option>
            )}
          </select>
        </label>
        <button
          className="button"
          disabled={disabled || data.analysis_running || !chosenReplay}
          onClick={() => {
            if (chosenReplay)
              void write(
                `${base}/runs`,
                'POST',
                {
                  mode: 'replay',
                  replay_id: chosenReplay.replay_id,
                  expected_case_version: data.version,
                },
                true,
              ).catch(() => {});
          }}
        >
          {busy ? 'Сохранение…' : 'Запустить Replay'}
        </button>
        <span className="subtle live-unavailable">Live недоступен</span>
      </section>
      {!options.length ? (
        <div className="notice warning">
          <strong>Для этого входа нет записи Replay</strong>
          <p>
            Текст, набор фактов или источники отличаются от сохранённого условия. Выберите исходные
            версии либо продолжите подготовку данных. Live будет подключён отдельным этапом.
          </p>
        </div>
      ) : null}
      {latestRun?.status === 'failed' ? (
        <div className="notice error" role="alert">
          <strong>Анализ завершился ошибкой</strong>
          <p>{label(String(latestRun.error?.code ?? 'PROVIDER_ERROR'))}</p>
          <JsonDetails title="Диагностика запуска" value={latestRun.error} />
        </div>
      ) : null}
      <div className="workspace-toolbar">
        <label>
          Версия разбора
          <select
            aria-label="Версия разбора"
            value={proposal?.id ?? ''}
            disabled={editor || !proposals.length}
            onChange={(e) => setSelected(e.target.value)}
          >
            {proposals.length ? (
              proposals.map((p, i) => (
                <option key={p.id} value={p.id}>
                  {i + 1}. {p.origin === 'operator' ? 'Правка оператора' : 'Ответ модели'}
                  {p.active ? ' · активная' : ''} · {timestamp(p.created_at)}
                </option>
              ))
            ) : (
              <option value="">Разбор ещё не создан</option>
            )}
          </select>
        </label>
        <div className="mobile-panels" aria-label="Рабочая панель">
          <button
            className={panel === 'analysis' ? 'selected' : 'secondary'}
            aria-pressed={panel === 'analysis'}
            onClick={() => setPanel('analysis')}
          >
            Разбор
          </button>
          <button
            className={panel === 'source' ? 'selected' : 'secondary'}
            aria-pressed={panel === 'source'}
            onClick={() => setPanel('source')}
          >
            Источник
          </button>
        </div>
      </div>
      <div className={`workspace-grid show-${panel}`}>
        <article className="analysis-pane" aria-label="Разбор">
          {proposal ? (
            <>
              {editor ? (
                <ProposalEditor
                  key={proposal.id}
                  proposal={proposal}
                  busy={disabled}
                  onSave={async (body, reason, expectedVersion) => {
                    await write(`/proposals/${encodeURIComponent(proposal.id)}/revisions`, 'POST', {
                      body,
                      reason,
                      expected_case_version: expectedVersion,
                    });
                    setEditingId(null);
                    setSelected(null);
                  }}
                  onCancel={() => setEditingId(null)}
                />
              ) : (
                <>
                  <ProposalRead proposal={proposal} onEvidence={openEvidence} />
                  <div className="revision-actions">
                    <button
                      className="secondary"
                      disabled={disabled || proposal.stale}
                      onClick={() => setEditingId(proposal.id)}
                    >
                      Исправить разбор
                    </button>
                    <button
                      className="secondary"
                      disabled={disabled || proposal.stale}
                      onClick={() => {
                        void write(
                          `/proposals/${encodeURIComponent(proposal.id)}/validate`,
                          'POST',
                          { expected_case_version: data.version },
                        ).catch(() => {});
                      }}
                    >
                      Повторить проверку
                    </button>
                    {!proposal.active ? (
                      <button
                        className="secondary"
                        disabled={disabled || proposal.stale}
                        onClick={() => {
                          void write(`${base}/active-proposal`, 'POST', {
                            proposal_revision_id: proposal.id,
                            expected_case_version: data.version,
                          }).catch(() => {});
                        }}
                      >
                        Сделать активной
                      </button>
                    ) : null}
                  </div>
                  <DecisionForm
                    key={proposal.id}
                    proposal={proposal}
                    disabled={disabled}
                    write={write}
                  />
                </>
              )}
            </>
          ) : (
            <div className="empty">
              <p className="eyebrow">Первый шаг</p>
              <h2>Разбор ещё не создан</h2>
              <p>
                Выберите совместимую запись и запустите Replay. Источники можно изучить до запуска.
              </p>
            </div>
          )}
        </article>
        <SourceReader document={doc} />
      </div>
      <details className="run-history">
        <summary>
          История запусков <span className="count">{runs.length}</span>
        </summary>
        {runs.map((run) => (
          <div key={run.id} className="run-history-item">
            <strong>
              {label(run.status)} · {run.mode === 'replay' ? 'Replay, сохранённый ответ' : 'Live'}
            </strong>
            <time>{timestamp(run.started_at)}</time>
            {run.error ? <p>{label(String(run.error.code))}</p> : null}
            <JsonDetails
              title="Происхождение и исходный ответ"
              value={{
                id: run.id,
                generation_profile: run.generation_profile,
                usage: run.usage,
                error: run.error,
                raw_output: run.raw_output,
              }}
            />
          </div>
        ))}
      </details>
    </main>
  );
}
