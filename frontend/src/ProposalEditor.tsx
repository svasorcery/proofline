import { useState } from 'react';
import type {
  AssemblyOutput,
  Evidence,
  Fact,
  ProposalView,
  SnapshotSource,
  TimelineEvent,
} from './api-types';
import { label } from './labels';

type FactKey = Fact['key'];
type FactValue = Fact['value'];
type EventType = TimelineEvent['event_type'];
type TimeRole = TimelineEvent['time']['role'];
type Readiness = AssemblyOutput['readiness'];
type FollowUpAction = AssemblyOutput['follow_up']['action'];
type RequirementKey = AssemblyOutput['follow_up']['requirements'][number];

const FACT_KEYS: FactKey[] = [
  'alternative_acceptance',
  'payment_outcome',
  'withdrawal_timing',
  'cancellation_status',
];
const FACT_VALUES: Record<FactKey, FactValue[]> = {
  alternative_acceptance: ['confirmed', 'conditional', 'not_accepted', 'unconfirmed'],
  payment_outcome: ['refund_settled', 'refund_pending', 'authorization_release', 'unconfirmed'],
  withdrawal_timing: ['before_supplier_cancel', 'after_supplier_cancel', 'unconfirmed'],
  cancellation_status: ['confirmed', 'unconfirmed'],
};
const EVENT_TYPES: EventType[] = [
  'alternative_offered',
  'customer_replied',
  'customer_message_received',
  'alternative_accepted',
  'customer_withdrawal',
  'supplier_cancelled',
  'payment_captured',
  'refund_initiated',
  'refund_settled',
  'authorization_released',
];
const TIME_ROLES: TimeRole[] = ['sent', 'received', 'occurred'];
const READINESS: Readiness[] = ['ready_for_rules', 'request_evidence', 'human_review'];
const ACTIONS: FollowUpAction[] = [
  'none',
  'ask_user',
  'read_source',
  'query_provider',
  'query_payment',
];
const REQUIREMENTS: RequirementKey[] = [
  'original_customer_message',
  'delivery_receipt',
  'quiet_room',
  'courtyard_windows',
  'original_acceptance_reply',
  'bank_settlement_document',
];

function Options({ values }: { values: readonly string[] }) {
  return values.map((value) => (
    <option key={value} value={value}>
      {label(value)}
    </option>
  ));
}

function EvidenceFields({
  evidence,
  sources,
  prefix,
  onChange,
}: {
  evidence: Evidence[];
  sources: SnapshotSource[];
  prefix: string;
  onChange: (next: Evidence[]) => void;
}) {
  function change(index: number, update: (item: Evidence) => void) {
    const next = structuredClone(evidence);
    const item = next[index];
    if (item) update(item);
    onChange(next);
  }

  return (
    <div className="stack evidence-editor">
      {evidence.map((item, evidenceIndex) => {
        const name = `${prefix} · доказательство ${evidenceIndex + 1}`;
        return (
          <fieldset className="editor-section" key={evidenceIndex}>
            <legend>Доказательство {evidenceIndex + 1}</legend>
            <label>
              Источник
              <select
                aria-label={`${name} · источник`}
                value={item.source_id}
                onChange={(event) =>
                  change(evidenceIndex, (next) => {
                    next.source_id = event.target.value;
                  })
                }
              >
                <option value="">Выберите источник</option>
                {!sources.some((source) => source.source_id === item.source_id) &&
                item.source_id !== '' ? (
                  <option value={item.source_id}>Недоступный источник: {item.source_id}</option>
                ) : null}
                {sources.map((source) => (
                  <option key={source.source_id} value={source.source_id}>
                    {source.title}
                  </option>
                ))}
              </select>
            </label>
            {item.fragments.map((fragment, fragmentIndex) => (
              <div className="stack" key={fragmentIndex}>
                <label>
                  Дословный фрагмент {fragmentIndex + 1}
                  <textarea
                    aria-label={`${name} · фрагмент ${fragmentIndex + 1}`}
                    value={fragment}
                    rows={3}
                    required
                    onChange={(event) =>
                      change(evidenceIndex, (next) => {
                        next.fragments[fragmentIndex] = event.target.value;
                      })
                    }
                  />
                </label>
                <button
                  type="button"
                  className="secondary"
                  disabled={item.fragments.length === 1}
                  aria-label={`Удалить ${name.toLowerCase()} · фрагмент ${fragmentIndex + 1}`}
                  onClick={() =>
                    change(evidenceIndex, (next) => {
                      next.fragments.splice(fragmentIndex, 1);
                    })
                  }
                >
                  Удалить фрагмент
                </button>
              </div>
            ))}
            <div className="inline">
              <button
                type="button"
                className="secondary"
                onClick={() =>
                  change(evidenceIndex, (next) => {
                    next.fragments.push('');
                  })
                }
              >
                Добавить фрагмент
              </button>
              <button
                type="button"
                className="danger secondary"
                aria-label={`Удалить ${name.toLowerCase()}`}
                onClick={() => onChange(evidence.filter((_, index) => index !== evidenceIndex))}
              >
                Удалить доказательство
              </button>
            </div>
          </fieldset>
        );
      })}
      <button
        type="button"
        className="secondary"
        onClick={() =>
          onChange([...evidence, { source_id: sources[0]?.source_id ?? '', fragments: [''] }])
        }
      >
        Добавить доказательство
      </button>
    </div>
  );
}

export function ProposalEditor({
  proposal,
  busy,
  onSave,
  onCancel,
}: {
  proposal: ProposalView;
  busy: boolean;
  onSave: (body: AssemblyOutput, reason: string, expectedVersion: number) => Promise<void>;
  onCancel: () => void;
}) {
  const [body, setBody] = useState(() => structuredClone(proposal.body));
  const [expectedVersion] = useState(proposal.case_version);
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const sources = proposal.snapshot.catalog.filter((source) => source.included);

  function edit(update: (draft: AssemblyOutput) => void) {
    setBody((current) => {
      const next = structuredClone(current);
      update(next);
      return next;
    });
  }

  async function save() {
    if (submitting || busy) return;
    if (!reason.trim()) {
      setError('Укажите причину исправления.');
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      await onSave(body, reason, expectedVersion);
    } catch {
      setError(
        'Не удалось сохранить. Правки сохранены в редакторе. Проверьте сообщение об ошибке перед повтором.',
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      aria-label="Исправление разбора"
      className="stack proposal-editor"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <div>
        <h2>Исправление разбора</h2>
        <p className="subtle">
          Изменения сохранятся новой версией. Исходный ответ и его доказательства останутся в
          истории.
        </p>
        {proposal.case_version !== expectedVersion ? (
          <p role="status">
            Обращение изменилось после открытия редактора. Сохранение потребует разрешить конфликт;
            черновик не перепривязывается автоматически.
          </p>
        ) : null}
      </div>
      <fieldset disabled={busy || submitting} className="stack">
        <legend>Содержимое новой версии</legend>
        <h3>Факты</h3>
        {body.facts.map((fact, factIndex) => (
          <fieldset className="editor-section stack" key={factIndex}>
            <legend>Факт {factIndex + 1}</legend>
            <div className="field-grid">
              <label>
                Проверяемый факт
                <select
                  aria-label={`Факт ${factIndex + 1}: ключ`}
                  value={fact.key}
                  onChange={(event) =>
                    edit((draft) => {
                      const current = draft.facts[factIndex];
                      if (!current) return;
                      current.key = event.target.value as FactKey;
                      if (!FACT_VALUES[current.key].includes(current.value))
                        current.value = 'unconfirmed';
                    })
                  }
                >
                  <Options values={FACT_KEYS} />
                </select>
              </label>
              <label>
                Значение
                <select
                  aria-label={`Факт ${factIndex + 1}: значение`}
                  value={fact.value}
                  onChange={(event) =>
                    edit((draft) => {
                      const current = draft.facts[factIndex];
                      if (current) current.value = event.target.value as FactValue;
                    })
                  }
                >
                  <Options values={FACT_VALUES[fact.key]} />
                </select>
              </label>
            </div>
            <EvidenceFields
              evidence={fact.evidence}
              sources={sources}
              prefix={`Факт ${factIndex + 1}`}
              onChange={(next) =>
                edit((draft) => {
                  const current = draft.facts[factIndex];
                  if (current) current.evidence = next;
                })
              }
            />
            <button
              type="button"
              className="danger secondary"
              aria-label={`Удалить факт ${factIndex + 1}`}
              onClick={() =>
                edit((draft) => {
                  draft.facts.splice(factIndex, 1);
                })
              }
            >
              Удалить факт
            </button>
          </fieldset>
        ))}
        <button
          type="button"
          className="secondary"
          onClick={() =>
            edit((draft) => {
              draft.facts.push({
                key:
                  proposal.snapshot.requested_fact_keys.find(
                    (key) => !draft.facts.some((fact) => fact.key === key),
                  ) ?? 'alternative_acceptance',
                value: 'unconfirmed',
                evidence: [],
              });
            })
          }
        >
          Добавить факт
        </button>

        <h3>Хронология</h3>
        {body.timeline.map((event, eventIndex) => (
          <fieldset className="editor-section stack" key={eventIndex}>
            <legend>Событие {eventIndex + 1}</legend>
            <label>
              Событие
              <select
                aria-label={`Событие ${eventIndex + 1}: тип`}
                value={event.event_type}
                onChange={(input) =>
                  edit((draft) => {
                    const current = draft.timeline[eventIndex];
                    if (current) current.event_type = input.target.value as EventType;
                  })
                }
              >
                <Options values={EVENT_TYPES} />
              </select>
            </label>
            <div className="field-grid">
              <label>
                Время из источника (ISO 8601 с часовым поясом)
                <input
                  aria-label={`Событие ${eventIndex + 1}: время`}
                  value={event.time.value}
                  required
                  placeholder="2026-10-08T12:27:00+03:00"
                  onChange={(input) =>
                    edit((draft) => {
                      const current = draft.timeline[eventIndex];
                      if (current) current.time.value = input.target.value;
                    })
                  }
                />
              </label>
              <label>
                Роль времени
                <select
                  aria-label={`Событие ${eventIndex + 1}: роль времени`}
                  value={event.time.role}
                  onChange={(input) =>
                    edit((draft) => {
                      const current = draft.timeline[eventIndex];
                      if (current) current.time.role = input.target.value as TimeRole;
                    })
                  }
                >
                  <Options values={TIME_ROLES} />
                </select>
              </label>
            </div>
            <EvidenceFields
              evidence={event.evidence}
              sources={sources}
              prefix={`Событие ${eventIndex + 1}`}
              onChange={(next) =>
                edit((draft) => {
                  const current = draft.timeline[eventIndex];
                  if (current) current.evidence = next;
                })
              }
            />
            <button
              type="button"
              className="danger secondary"
              aria-label={`Удалить событие ${eventIndex + 1}`}
              onClick={() =>
                edit((draft) => {
                  draft.timeline.splice(eventIndex, 1);
                })
              }
            >
              Удалить событие
            </button>
          </fieldset>
        ))}
        <button
          type="button"
          className="secondary"
          onClick={() =>
            edit((draft) => {
              draft.timeline.push({
                event_type: 'customer_message_received',
                time: { value: '', role: 'received' },
                evidence: [],
              });
            })
          }
        >
          Добавить событие
        </button>

        <h3>Следующий шаг</h3>
        <p className="subtle">
          Это предложение оператору. Приложение не исполняет действия и не отправляет запросы.
        </p>
        <div className="field-grid">
          <label>
            Предложенная готовность данных
            <select
              value={body.readiness}
              onChange={(event) =>
                edit((draft) => {
                  draft.readiness = event.target.value as Readiness;
                })
              }
            >
              <Options values={READINESS} />
            </select>
          </label>
          <label>
            Предложенное действие
            <select
              value={body.follow_up.action}
              onChange={(event) =>
                edit((draft) => {
                  const action = event.target.value as FollowUpAction;
                  draft.follow_up.action = action;
                  if (action === 'none' || action === 'ask_user') draft.follow_up.locator = null;
                  if (action === 'none') draft.follow_up.requirements = [];
                })
              }
            >
              <Options values={ACTIONS} />
            </select>
          </label>
        </div>
        {body.follow_up.action !== 'none' && body.follow_up.action !== 'ask_user' ? (
          <label>
            Источник следующего шага
            <select
              value={body.follow_up.locator ?? ''}
              required
              onChange={(event) =>
                edit((draft) => {
                  draft.follow_up.locator = event.target.value || null;
                })
              }
            >
              <option value="">Выберите источник из каталога</option>
              {body.follow_up.locator &&
              !proposal.snapshot.catalog.some(
                (source) => source.locator === body.follow_up.locator,
              ) ? (
                <option value={body.follow_up.locator}>{body.follow_up.locator}</option>
              ) : null}
              {proposal.snapshot.catalog.map((source) => (
                <option key={source.source_id} value={source.locator}>
                  {source.title}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <fieldset className="stack">
          <legend>Запрашиваемые подтверждения</legend>
          {REQUIREMENTS.map((requirement) => (
            <label className="check" key={requirement}>
              <input
                type="checkbox"
                checked={body.follow_up.requirements.includes(requirement)}
                onChange={(event) =>
                  edit((draft) => {
                    draft.follow_up.requirements = event.target.checked
                      ? [...draft.follow_up.requirements, requirement]
                      : draft.follow_up.requirements.filter((item) => item !== requirement);
                  })
                }
              />
              {label(requirement)}
            </label>
          ))}
        </fieldset>
        <label>
          Причина исправления
          <textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            maxLength={2000}
            rows={3}
            required
          />
        </label>
        {error ? <p role="alert">{error}</p> : null}
        <div className="inline">
          <button type="submit">{submitting ? 'Сохранение…' : 'Сохранить новую версию'}</button>
          <button type="button" className="secondary" onClick={onCancel}>
            Отменить исправление
          </button>
        </div>
      </fieldset>
    </form>
  );
}
