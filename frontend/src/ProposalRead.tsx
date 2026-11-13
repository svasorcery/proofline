import { useState } from 'react';
import type { Evidence, ProposalView, RunView, SpanView, SnapshotSource } from './api-types';
import { label } from './labels';
import { JsonDetails, Status, timestamp } from './shared';

export type OpenEvidence = (source: SnapshotSource, spans: SpanView[], focus?: SpanView) => void;

export function ProposalRead({
  proposal: p,
  onEvidence,
  originRun,
}: {
  proposal: ProposalView;
  onEvidence: OpenEvidence;
  originRun?: RunView;
}) {
  const [copyStatus, setCopyStatus] = useState('');
  return (
    <>
      <div className="section-heading">
        <div>
          <p className="eyebrow">{p.origin === 'operator' ? 'Правка оператора' : 'Ответ модели'}</p>
          <h2>Разбор обращения</h2>
        </div>
        <Status status={p.status} />
      </div>
      {originRun ? (
        <p className="subtle">
          {p.origin === 'operator' ? 'Основа правки: ' : 'Происхождение: '}
          {originRun.mode === 'live' ? 'Live · новый вызов модели' : 'Replay · сохранённая запись'}
          {' · '}
          {String(originRun.generation_profile.model ?? '')}
          {originRun.mode === 'replay' && originRun.generation_profile.recorded_on
            ? ` · ${String(originRun.generation_profile.recorded_on)}`
            : ''}
        </p>
      ) : null}
      {p.stale ? (
        <div className="notice warning">
          <strong>Исторический вход</strong>
          <p>
            Источники или обращение изменились. Этот разбор нельзя подтвердить для текущего входа.
          </p>
        </div>
      ) : null}
      {!p.active ? (
        <div className="notice">
          <strong>Открыта версия из истории</strong>
          <p>Эта версия не является активным разбором обращения.</p>
        </div>
      ) : null}
      {!p.validation_current ? (
        <div className="notice warning">Требуется повторная проверка текущим профилем.</div>
      ) : null}
      <section className="analysis-section">
        <h3>Проверяемые факты</h3>
        {p.body.facts.map((fact, i) => (
          <div className="fact-row" key={i}>
            <div className="fact-heading">
              <span>{label(fact.key)}</span>
              <strong className={`fact-value value-${fact.value}`}>{label(fact.value)}</strong>
            </div>
            <EvidenceList
              evidence={fact.evidence}
              path={['facts', i, 'evidence']}
              proposal={p}
              onEvidence={onEvidence}
            />
          </div>
        ))}
        {p.body.facts.length === 0 ? (
          <p className="subtle">Факты не выделены. Проверьте полноту вручную.</p>
        ) : null}
      </section>
      <section className="analysis-section">
        <h3>Хронология</h3>
        <ol className="timeline">
          {p.body.timeline.map((event, i) => (
            <li key={i}>
              <div className="timeline-time">
                <time>{event.time.value}</time>
                <span>{label(event.time.role)}</span>
              </div>
              <div>
                <strong>{label(event.event_type)}</strong>
                <EvidenceList
                  evidence={event.evidence}
                  path={['timeline', i, 'evidence']}
                  proposal={p}
                  onEvidence={onEvidence}
                />
              </div>
            </li>
          ))}
        </ol>
        {p.body.timeline.length === 0 ? <p className="subtle">События не выделены.</p> : null}
      </section>
      <section className="analysis-section">
        <h3>Что требуется уточнить</h3>
        <p className="subtle">
          Предложение модели о готовности: <strong>{label(p.body.readiness)}</strong>. Оно не
          заменяет решение оператора.
        </p>
        <p>{label(p.body.follow_up.action)}</p>
        {p.body.follow_up.requirements.length ? (
          <ul className="requirements">
            {p.body.follow_up.requirements.map((r) => (
              <li key={r}>{label(r)}</li>
            ))}
          </ul>
        ) : (
          <p className="subtle">Дополнительные подтверждения в ответе не указаны.</p>
        )}
        {p.body.follow_up.locator ? (
          <p className="subtle">
            Источник следующего шага: <code>{p.body.follow_up.locator}</code>
          </p>
        ) : null}
        <button
          className="secondary"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(
                p.body.follow_up.requirements.map(label).join('\n'),
              );
              setCopyStatus('Требования скопированы');
            } catch {
              setCopyStatus('Не удалось скопировать. Выделите текст требований вручную.');
            }
          }}
          disabled={!p.body.follow_up.requirements.length}
        >
          Скопировать требования
        </button>
        <p role="status" className="subtle">
          {copyStatus}
        </p>
      </section>
      <section className="checks analysis-section">
        <h3>
          Проверки кода <span className="count">{p.blocking_issues.length}</span>
        </h3>
        {p.blocking_issues.length ? (
          <ul className="issue-list">
            {p.blocking_issues.map((issue, i) => (
              <li key={i}>
                <strong>Замечание {i + 1}</strong>
                <p>{issue.message}</p>
                <details>
                  <summary>Поле и код проверки</summary>
                  <small>
                    {issue.path.join(' → ')} · {issue.code}
                  </small>
                </details>
              </li>
            ))}
          </ul>
        ) : (
          <div className="notice">
            <strong>Блокирующих ошибок нет</strong>
            <p>
              Буквальность и согласованность проверены. Полноту событий и смысл доказательств
              проверяет оператор.
            </p>
          </div>
        )}
      </section>
      {p.decision ? (
        <section className="analysis-section">
          <h3>{p.decision_current ? 'Решение оператора' : 'Историческое решение'}</h3>
          <Status status={p.decision.kind === 'approve' ? 'approved' : 'rejected'} />
          <p>{p.decision.reason}</p>
          <small className="subtle">
            {p.decision.actor} · {timestamp(p.decision.created_at)}
            {p.decision_current ? '' : ' · неприменимо к текущему состоянию'}
          </small>
        </section>
      ) : null}
      <JsonDetails
        title="Версия, снимок и проверки"
        value={{
          id: p.id,
          created_at: p.created_at,
          reason: p.reason,
          source_bindings: p.source_bindings,
          snapshot: p.snapshot,
          validations: p.validations,
          decision: p.decision,
        }}
      />
      <JsonDetails title="Исходный JSON этой версии" value={p.body} />
    </>
  );
}

function EvidenceList({
  evidence,
  path,
  proposal,
  onEvidence,
}: {
  evidence: Evidence[];
  path: (string | number)[];
  proposal: ProposalView;
  onEvidence: OpenEvidence;
}) {
  return (
    <div className="evidence-list">
      {evidence.map((e, i) => {
        const source = proposal.snapshot.catalog.find((s) => s.source_id === e.source_id);
        return (
          <div key={i}>
            {e.fragments.map((fragment, j) => {
              const fullPath = [...path, i, 'fragments', j];
              const span = proposal.latest_validation?.resolved_spans.find(
                (s) => JSON.stringify(s.path) === JSON.stringify(fullPath),
              );
              return (
                <button
                  className="evidence"
                  key={j}
                  disabled={!source?.text}
                  onClick={() => {
                    if (source)
                      onEvidence(source, proposal.latest_validation?.resolved_spans ?? [], span);
                  }}
                >
                  <span className="evidence-quote">«{fragment}»</span>
                  <span className="evidence-source">
                    ↗ {source?.title ?? e.source_id}
                    {span ? '' : ' · фрагмент не подтверждён'}
                  </span>
                </button>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}
