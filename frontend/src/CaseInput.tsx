import { useState } from 'react';
import type { CaseDetail, SourceView, FactKey } from './api-types';
import { label } from './labels';
import { JsonDetails } from './shared';

export type Write = (
  path: string,
  method: 'POST' | 'PATCH',
  body: unknown,
  idempotent?: boolean,
) => Promise<void>;
const factKeys: FactKey[] = [
  'alternative_acceptance',
  'payment_outcome',
  'withdrawal_timing',
  'cancellation_status',
];

export function CaseInput({
  data,
  busy,
  write,
  onRead,
}: {
  data: CaseDetail;
  busy: boolean;
  write: Write;
  onRead: (source: SourceView) => Promise<void>;
}) {
  const [edit, setEdit] = useState(false);
  const [add, setAdd] = useState(false);
  const [sourceEdit, setSourceEdit] = useState<string | null>(null);
  const [readError, setReadError] = useState('');
  const [selection, setSelection] = useState<{
    id: string;
    value: boolean;
    baseVersion: number;
  } | null>(null);
  return (
    <details className="input-section">
      <summary>
        Обращение и источники{' '}
        <span className="subtle">
          {data.sources.filter((s) => s.included).length} из {data.sources.length} выбрано
        </span>
      </summary>
      <div className="input-content stack">
        <div className="section-heading">
          <h2>Исходное обращение</h2>
          <button className="secondary" disabled={busy} onClick={() => setEdit(!edit)}>
            {edit ? 'Закрыть редактор обращения' : 'Изменить обращение'}
          </button>
        </div>
        <p className="user-text">{data.user_text}</p>
        <div className="tag-list">
          {data.requested_fact_keys.map((f) => (
            <span className="tag" key={f}>
              {label(f)}
            </span>
          ))}
        </div>
        {edit ? (
          <CaseForm
            key={data.id}
            data={data}
            busy={busy}
            write={write}
            done={() => setEdit(false)}
          />
        ) : null}
        <JsonDetails
          title="Справочный контекст обращения"
          value={{ money: data.money, context: data.context }}
        />
        <div className="section-heading">
          <h2>Каталог источников</h2>
          <button className="secondary" disabled={busy} onClick={() => setAdd(!add)}>
            {add ? 'Закрыть форму источника' : '+ Добавить источник'}
          </button>
        </div>
        <p className="subtle">
          Изменение выбранного текста или состава источников делает прежний разбор устаревшим.
        </p>
        {add ? (
          <SourceForm data={data} busy={busy} write={write} done={() => setAdd(false)} />
        ) : null}
        <ul className="source-list">
          {data.sources.map((s) => (
            <li key={s.source_id}>
              <div className="source-row">
                <label className="checkbox">
                  <input
                    aria-label={`Включить ${s.title}`}
                    type="checkbox"
                    checked={
                      selection?.id === s.source_id && selection.baseVersion === data.version
                        ? selection.value
                        : s.included
                    }
                    disabled={busy || !s.current_version_id}
                    onChange={(e) => {
                      const included = e.target.checked;
                      setSelection({ id: s.source_id, value: included, baseVersion: data.version });
                      void write(
                        `/cases/${encodeURIComponent(data.id)}/sources/${encodeURIComponent(s.source_id)}`,
                        'PATCH',
                        { expected_case_version: data.version, included },
                      ).catch(() => setSelection(null));
                    }}
                  />
                  <span>
                    <strong>{s.title}</strong>
                    <small>
                      {label(s.kind)} ·{' '}
                      {s.versions.length ? `${s.versions.length} верс.` : 'Текст ожидается'}
                    </small>
                  </span>
                </label>
                <div className="inline">
                  <button
                    className="text-button"
                    disabled={busy || !s.current_version_id}
                    onClick={async () => {
                      setReadError('');
                      try {
                        await onRead(s);
                      } catch {
                        setReadError('Не удалось открыть текст источника. Повторите открытие.');
                      }
                    }}
                  >
                    Открыть текст
                  </button>
                  <button
                    className="text-button"
                    disabled={busy}
                    onClick={() => setSourceEdit(sourceEdit === s.source_id ? null : s.source_id)}
                  >
                    Изменить источник
                  </button>
                </div>
              </div>
              {sourceEdit === s.source_id ? (
                <SourceForm
                  key={s.source_id}
                  data={data}
                  source={s}
                  busy={busy}
                  write={write}
                  done={() => setSourceEdit(null)}
                />
              ) : null}
            </li>
          ))}
        </ul>
        {readError ? <p role="alert">{readError}</p> : null}
      </div>
    </details>
  );
}

function CaseForm({
  data,
  busy,
  write,
  done,
}: {
  data: CaseDetail;
  busy: boolean;
  write: Write;
  done: () => void;
}) {
  const [baseVersion] = useState(data.version);
  const [facts, setFacts] = useState(data.requested_fact_keys);
  const [error, setError] = useState('');
  return (
    <form
      className="stack form-surface"
      onSubmit={async (e) => {
        e.preventDefault();
        const f = new FormData(e.currentTarget);
        try {
          const context: unknown = JSON.parse(String(f.get('context')));
          await write(`/cases/${encodeURIComponent(data.id)}`, 'PATCH', {
            expected_case_version: baseVersion,
            title: f.get('title'),
            user_text: f.get('user_text'),
            requested_fact_keys: facts,
            context,
          });
          done();
        } catch {
          setError('Изменения не подтверждены. Проверьте ошибку и сохранённый черновик.');
        }
      }}
    >
      <label>
        Название
        <input name="title" defaultValue={data.title} maxLength={200} required />
      </label>
      <label>
        Текст обращения
        <textarea name="user_text" defaultValue={data.user_text} required rows={4} />
      </label>
      <fieldset>
        <legend>Проверяемые факты</legend>
        {factKeys.map((f) => (
          <label className="checkbox" key={f}>
            <input
              type="checkbox"
              checked={facts.includes(f)}
              onChange={(e) =>
                setFacts(e.target.checked ? [...facts, f] : facts.filter((v) => v !== f))
              }
            />
            {label(f)}
          </label>
        ))}
      </fieldset>
      <details>
        <summary>Справочный контекст (JSON)</summary>
        <label>
          Контекст
          <textarea name="context" defaultValue={JSON.stringify(data.context, null, 2)} rows={4} />
        </label>
      </details>
      {error ? <p role="alert">{error}</p> : null}
      <button className="button" disabled={busy}>
        Сохранить обращение
      </button>
    </form>
  );
}

function SourceForm({
  data,
  source,
  busy,
  write,
  done,
}: {
  data: CaseDetail;
  source?: SourceView;
  busy: boolean;
  write: Write;
  done: () => void;
}) {
  const [baseVersion] = useState(data.version);
  const [error, setError] = useState('');
  const path = `/cases/${encodeURIComponent(data.id)}/sources${source ? `/${encodeURIComponent(source.source_id)}` : ''}`;
  return (
    <div className="form-surface stack">
      <form
        className="stack"
        onSubmit={async (e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          const body: Record<string, unknown> = {
            expected_case_version: baseVersion,
            title: f.get('title'),
            kind: f.get('kind'),
            locator: f.get('locator'),
            trust: f.get('trust'),
          };
          if (!source && f.get('text')) body.text = f.get('text');
          if (source) {
            body.position = Number(f.get('position'));
            if (f.get('version')) body.current_version_id = f.get('version');
          }
          try {
            await write(path, source ? 'PATCH' : 'POST', body);
            done();
          } catch {
            setError('Источник не сохранён. Черновик остаётся в форме.');
          }
        }}
      >
        <div className="field-grid">
          <label>
            Название источника
            <input name="title" defaultValue={source?.title} maxLength={200} required />
          </label>
          <label>
            Тип источника
            <select name="kind" defaultValue={source?.kind ?? 'document'}>
              {['document', 'provider_log', 'payment_ledger'].map((k) => (
                <option key={k} value={k}>
                  {label(k)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Расположение (locator)
            <input name="locator" defaultValue={source?.locator} maxLength={1000} required />
          </label>
          <label>
            Метка доверия
            <input
              name="trust"
              defaultValue={source?.trust ?? 'synthetic'}
              maxLength={100}
              required
            />
          </label>
        </div>
        {source ? (
          <div className="field-grid">
            <label>
              Позиция в каталоге (с 0)
              <input
                type="number"
                min={0}
                max={data.sources.length - 1}
                name="position"
                defaultValue={source.position}
                required
              />
            </label>
            <label>
              Выбранная версия
              <select name="version" defaultValue={source.current_version_id ?? ''}>
                <option value="">Текст ожидается</option>
                {source.versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    Версия {v.sequence}
                  </option>
                ))}
              </select>
            </label>
          </div>
        ) : (
          <label>
            Текст источника (можно добавить позже)
            <textarea name="text" rows={4} />
          </label>
        )}
        <button className="button" disabled={busy}>
          {source ? 'Сохранить метаданные' : 'Добавить источник'}
        </button>
      </form>
      {source ? (
        <form
          className="stack"
          onSubmit={async (e) => {
            e.preventDefault();
            const f = new FormData(e.currentTarget);
            try {
              await write(`${path}/versions`, 'POST', {
                expected_case_version: baseVersion,
                text: f.get('text'),
              });
              done();
            } catch {
              setError('Версия не подтверждена. Проверьте историю перед новым сохранением.');
            }
          }}
        >
          <label>
            Текст новой версии
            <textarea name="text" required rows={5} />
          </label>
          <p className="subtle">Прежний текст и связанные с ним доказательства сохранятся.</p>
          <button className="secondary" disabled={busy}>
            Создать версию текста
          </button>
        </form>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
    </div>
  );
}
