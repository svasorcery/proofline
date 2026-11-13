import { useRef, useState } from 'react';
import type { ProposalView } from './api-types';
import type { Write } from './CaseInput';

type CommentDraft = { reason: string; baseVersion: number };

export function DecisionForm({
  proposal,
  disabled,
  write,
}: {
  proposal: ProposalView;
  disabled: boolean;
  write: Write;
}) {
  const [draft, setDraft] = useState<CommentDraft | null>(null);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const reason = draft?.reason ?? '';
  const baseVersion = draft?.baseVersion ?? proposal.case_version;
  const changedWhileEditing = draft !== null && baseVersion !== proposal.case_version;
  const eligible = proposal.active && !proposal.stale && !proposal.decision;
  const canApprove =
    eligible && proposal.validation_current && proposal.blocking_issues.length === 0;
  const busy = disabled || submitting;

  return (
    <section className="decision-panel">
      <h3>Зафиксировать решение</h3>
      <p className="subtle">
        Проверьте смысл, полноту событий и достаточность доказательств. Подтверждение относится к
        разбору.
      </p>
      {proposal.decision ? (
        <p className="subtle">
          По этой версии уже есть решение. Для пересмотра создайте исправленную версию.
        </p>
      ) : null}
      <form
        aria-label="Решение по разбору"
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy || submittingRef.current || !eligible || changedWhileEditing) return;
          if (!reason.trim()) {
            setError('Укажите комментарий к решению.');
            return;
          }
          const submitter = (event.nativeEvent as SubmitEvent).submitter;
          const kind = submitter instanceof HTMLButtonElement ? submitter.value : null;
          if (kind !== 'approve' && kind !== 'reject') return;
          if (kind === 'approve' && !canApprove) return;
          submittingRef.current = true;
          setSubmitting(true);
          setError('');
          try {
            await write(
              `/proposals/${encodeURIComponent(proposal.id)}/decision`,
              'POST',
              {
                kind,
                reason,
                expected_case_version: baseVersion,
              },
              true,
            );
          } catch {
            setError(
              'Решение не подтверждено. Комментарий сохранён. Проверьте сообщение об ошибке и исход запроса.',
            );
          } finally {
            submittingRef.current = false;
            setSubmitting(false);
          }
        }}
      >
        <label>
          Комментарий к решению
          <textarea
            value={reason}
            onChange={(event) => {
              const nextReason = event.target.value;
              setDraft((current) =>
                nextReason.length
                  ? {
                      reason: nextReason,
                      baseVersion: current?.baseVersion ?? proposal.case_version,
                    }
                  : null,
              );
            }}
            required
            maxLength={2000}
            rows={3}
            disabled={busy || !eligible}
          />
        </label>
        {changedWhileEditing ? (
          <div className="stack">
            <p role="status">
              Обращение изменилось после начала комментария. Сравните обновлённые данные перед
              решением. Комментарий сохранён.
            </p>
            <button
              type="button"
              className="secondary"
              disabled={busy || !eligible}
              onClick={() => {
                setDraft((current) =>
                  current ? { ...current, baseVersion: proposal.case_version } : null,
                );
                setError('');
              }}
            >
              Проверил обновлённые данные
            </button>
          </div>
        ) : null}
        <div className="decision-buttons">
          <button
            type="submit"
            className="button"
            value="approve"
            disabled={busy || !canApprove || changedWhileEditing}
          >
            Подтвердить разбор
          </button>
          <button
            type="submit"
            className="danger"
            value="reject"
            disabled={busy || !eligible || changedWhileEditing}
          >
            Отклонить разбор
          </button>
        </div>
        {!canApprove && !proposal.decision ? (
          <p className="subtle">
            Подтверждение доступно для активной актуальной версии после проверки без блокирующих
            замечаний.
          </p>
        ) : null}
        {error ? <p role="alert">{error}</p> : null}
      </form>
    </section>
  );
}
