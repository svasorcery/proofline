import { ApiError } from './api';
import { label } from './labels';

export function Status({ status }: { status: string }) {
  return <span className={`status status-${status}`}>{label(status)}</span>;
}

export function timestamp(value: string) {
  return new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(
    new Date(value),
  );
}

export function ErrorNotice({ error }: { error: unknown }) {
  const e = error instanceof ApiError ? error : null;
  return (
    <div className="notice error" role="alert">
      <strong>{e ? label(e.code) : 'Не удалось загрузить данные'}</strong>
      <p>
        {e?.code === 'CASE_VERSION_CONFLICT'
          ? 'Обращение изменилось в другой вкладке. Обновите данные и сравните изменения. Черновик сохранён; для повторной правки откройте редактор заново.'
          : e?.outcomeUnknown
            ? 'Сервер мог сохранить запрос. Проверьте его результат перед новым действием.'
            : (e?.message ?? 'Проверьте соединение и повторите загрузку.')}
      </p>
      {e ? (
        <details>
          <summary>Подробности ошибки</summary>
          <code>{e.code}</code>
          {e.requestId ? (
            <p>
              Запрос: <code>{e.requestId}</code>
            </p>
          ) : null}
          <pre>{JSON.stringify(e.details, null, 2)}</pre>
        </details>
      ) : null}
    </div>
  );
}

export function Loading() {
  return (
    <div className="loading" role="status" aria-label="Загрузка">
      <div />
      <div />
      <div />
    </div>
  );
}

export function JsonDetails({ title, value }: { title: string; value: unknown }) {
  return (
    <details className="technical">
      <summary>{title}</summary>
      <pre>{typeof value === 'string' ? value : JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
