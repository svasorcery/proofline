import { useEffect, useState } from 'react';
import { api, command } from './api';
import type { CasePage, CaseDetail, Capabilities, FactKey } from './api-types';
import { label } from './labels';
import { ErrorNotice, Loading, Status, timestamp } from './shared';

const statuses = [
  'unanalysed',
  'needs_operator_review',
  'blocked_for_approval',
  'approved',
  'rejected',
  'stale',
  'needs_revalidation',
];

export function CaseList() {
  const [status, setStatus] = useState('');
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<CasePage | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [refresh, setRefresh] = useState(0);
  const [create, setCreate] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api<CasePage>(`/cases?limit=20&offset=${offset}${status ? `&status=${status}` : ''}`, {
      signal: controller.signal,
    })
      .then((data) => {
        if (!controller.signal.aborted) {
          setPage(data);
          setError(null);
        }
      })
      .catch((e: unknown) => {
        if (!controller.signal.aborted) setError(e);
      });
    return () => controller.abort();
  }, [status, offset, refresh]);
  return (
    <main id="main" className="page list-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Рабочая очередь</p>
          <h1>Обращения</h1>
          <p className="subtle">Проверьте факты, откройте источники и зафиксируйте решение.</p>
        </div>
        <button onClick={() => setCreate(!create)} className="button">
          {create ? 'Закрыть форму' : '+ Новое обращение'}
        </button>
      </div>
      {create ? <CreateCase /> : null}
      <div className="list-toolbar">
        <label className="filter-label">
          Статус
          <select
            value={status}
            onChange={(e) => {
              setStatus(e.target.value);
              setOffset(0);
              setPage(null);
            }}
          >
            <option value="">Все обращения</option>
            {statuses.map((s) => (
              <option key={s} value={s}>
                {label(s)}
              </option>
            ))}
          </select>
        </label>
        <button className="secondary" onClick={() => setRefresh((n) => n + 1)}>
          Обновить
        </button>
        <span className="subtle">{page ? `${page.total} обращений` : 'Загрузка очереди'}</span>
      </div>
      {error ? <ErrorNotice error={error} /> : null}
      {!page && !error ? <Loading /> : null}
      {page ? (
        <>
          <div className="case-table" role="table" aria-label="Обращения">
            <div className="case-row table-heading" role="row">
              <span role="columnheader">Обращение</span>
              <span role="columnheader">Статус разбора</span>
              <span role="columnheader">Изменено</span>
              <span />
            </div>
            {page.items.map((c) => (
              <a
                href={`/cases/${encodeURIComponent(c.id)}`}
                className="case-row"
                role="row"
                key={c.id}
              >
                <div role="cell">
                  <span className="case-id">{c.id.startsWith('V0') ? c.id : 'Учебное'}</span>
                  <strong>{c.title}</strong>
                </div>
                <div role="cell">
                  <Status status={c.status} />
                  {c.analysis_running ? <span className="running">Анализ выполняется</span> : null}
                </div>
                <time role="cell" dateTime={c.updated_at}>
                  {timestamp(c.updated_at)}
                </time>
                <span aria-hidden="true" className="row-arrow">
                  ↗
                </span>
              </a>
            ))}
          </div>
          {page.items.length === 0 ? (
            <div className="empty">
              <h2>Здесь пока нет обращений</h2>
              <p>Выберите другой статус или создайте учебное обращение.</p>
            </div>
          ) : null}
          <nav className="pagination" aria-label="Страницы">
            <button
              className="secondary"
              disabled={offset === 0}
              onClick={() => {
                setOffset(Math.max(0, offset - 20));
                setPage(null);
              }}
            >
              Назад
            </button>
            <span>
              {offset + 1}–{offset + page.items.length} из {page.total}
            </span>
            <button
              className="secondary"
              disabled={offset + 20 >= page.total}
              onClick={() => {
                setOffset(offset + 20);
                setPage(null);
              }}
            >
              Далее
            </button>
          </nav>
        </>
      ) : null}
    </main>
  );
}

function CreateCase() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [facts, setFacts] = useState<FactKey[]>([]);
  useEffect(() => {
    const ac = new AbortController();
    api<Capabilities>('/capabilities', { signal: ac.signal })
      .then(setCaps)
      .catch((e: unknown) => {
        if (!ac.signal.aborted) setError(e);
      });
    return () => ac.abort();
  }, []);
  return (
    <form
      className="form-surface stack"
      onSubmit={async (e) => {
        e.preventDefault();
        if (busy) return;
        setBusy(true);
        const data = new FormData(e.currentTarget);
        try {
          const c = await command<CaseDetail>('/cases', 'POST', {
            title: data.get('title'),
            user_text: data.get('user_text'),
            requested_fact_keys: facts,
          });
          window.location.assign(`/cases/${encodeURIComponent(c.id)}`);
        } catch (err) {
          setError(err);
          setBusy(false);
        }
      }}
    >
      <h2>Новое учебное обращение</h2>
      <p className="subtle">Для произвольного текста может не быть совместимой записи Replay.</p>
      <label>
        Название
        <input name="title" required maxLength={200} />
      </label>
      <label>
        Текст обращения
        <textarea
          name="user_text"
          required
          maxLength={caps?.limits.user_characters ?? 8000}
          rows={4}
        />
      </label>
      <fieldset>
        <legend>Проверяемые факты</legend>
        {caps?.fact_keys.map((key) => (
          <label className="checkbox" key={key}>
            <input
              type="checkbox"
              checked={facts.includes(key)}
              onChange={(e) =>
                setFacts(e.target.checked ? [...facts, key] : facts.filter((f) => f !== key))
              }
            />
            {label(key)}
          </label>
        ))}
      </fieldset>
      {error ? <ErrorNotice error={error} /> : null}
      <button className="button" disabled={busy || !caps}>
        {busy ? 'Создание…' : 'Создать обращение'}
      </button>
    </form>
  );
}
