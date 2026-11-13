import type { Money } from './api-types';

export const statusLabels: Record<string, string> = {
  unanalysed: 'Без разбора',
  stale: 'Входные данные изменились',
  needs_revalidation: 'Требуется повторная проверка',
  approved: 'Разбор подтверждён',
  rejected: 'Разбор отклонён',
  blocked_for_approval: 'Есть блокирующие замечания',
  needs_operator_review: 'Ожидает проверки оператора',
};

const labels: Record<string, string> = {
  ...statusLabels,
  alternative_acceptance: 'Согласие на альтернативу',
  payment_outcome: 'Результат платежа',
  withdrawal_timing: 'Момент отказа клиента',
  cancellation_status: 'Статус отмены',
  confirmed: 'Подтверждено',
  conditional: 'Согласие с условиями',
  not_accepted: 'Не принято',
  unconfirmed: 'Не подтверждено',
  refund_settled: 'Возврат зачислен',
  refund_pending: 'Возврат в обработке',
  authorization_release: 'Снята блокировка средств',
  before_supplier_cancel: 'До отмены поставщиком',
  after_supplier_cancel: 'После отмены поставщиком',
  alternative_offered: 'Предложена альтернатива',
  customer_replied: 'Клиент отправил ответ',
  customer_message_received: 'Получено сообщение клиента',
  alternative_accepted: 'Альтернатива принята',
  customer_withdrawal: 'Отказ клиента',
  supplier_cancelled: 'Отмена поставщиком',
  payment_captured: 'Платёж списан',
  refund_initiated: 'Возврат начат',
  authorization_released: 'Снята блокировка средств',
  sent: 'Отправлено',
  received: 'Получено',
  occurred: 'Произошло',
  ready_for_rules: 'Модель предлагает готовность данных',
  request_evidence: 'Нужны подтверждения',
  human_review: 'Нужна проверка человека',
  none: 'Следующий шаг не предложен',
  ask_user: 'Запросить у клиента',
  read_source: 'Прочитать источник',
  query_provider: 'Запросить у поставщика',
  query_payment: 'Запросить платёжные данные',
  original_customer_message: 'Оригинал сообщения клиента',
  delivery_receipt: 'Подтверждение доставки сообщения',
  quiet_room: 'Подтверждение тихого номера',
  courtyard_windows: 'Подтверждение окон во двор',
  original_acceptance_reply: 'Оригинал безусловного согласия',
  bank_settlement_document: 'Банковский документ о зачислении',
  document: 'Документ',
  provider_log: 'Журнал поставщика',
  payment_ledger: 'Платёжный журнал',
  synthetic: 'Учебные данные',
  replay: 'Записанный ответ',
  live: 'Новый вызов модели',
  model: 'Записанный ответ модели',
  operator: 'Правка оператора',
  running: 'Анализ выполняется',
  succeeded: 'Анализ завершён',
  failed: 'Анализ завершился ошибкой',
  approve: 'Подтверждено',
  reject: 'Отклонено',
  CASE_VERSION_CONFLICT:
    'Обращение изменилось в другой вкладке. Обновите данные и сравните их с правкой.',
  PROPOSAL_STALE: 'Входные данные изменились. Для них нужен новый анализ.',
  PROPOSAL_NOT_ACTIVE: 'Сначала сделайте эту версию активной.',
  VALIDATION_PROFILE_CHANGED: 'Правила проверки изменились. Выполните повторную проверку.',
  VALIDATION_BLOCKED: 'Исправьте блокирующие замечания перед подтверждением.',
  ALREADY_REVIEWED: 'По этой версии уже принято решение. Для пересмотра создайте новую версию.',
  REPLAY_INPUT_MISMATCH: 'Для текущего набора данных нет совместимой записи replay.',
  REPLAY_NOT_FOUND: 'Эта запись replay не найдена.',
  LIVE_NOT_CONFIGURED: 'Live-режим ещё не настроен. Выберите совместимую запись replay.',
  ANALYSIS_ALREADY_RUNNING: 'Анализ этого обращения уже выполняется. Проверьте историю запусков.',
  IDEMPOTENCY_CONFLICT:
    'Этот ключ уже относится к другому запросу. Проверьте сохранённый результат.',
  SOURCE_TEXT_REQUIRED: 'Сначала добавьте текст источника, затем включите его в анализ.',
  SOURCE_LOCATOR_CONFLICT: 'Источник с такой ссылкой уже зарегистрирован.',
  INPUT_LIMIT_EXCEEDED: 'Превышен лимит входных данных. Сократите текст или состав источников.',
  VALIDATION_ERROR: 'Проверьте заполнение полей и допустимые значения.',
  INVALID_REQUEST: 'Проверьте данные запроса.',
  DATABASE_UNAVAILABLE:
    'База данных пока недоступна. Проверьте состояние перед повторным действием.',
  NETWORK_ERROR: 'Нет связи с сервером.',
  INTERNAL_ERROR: 'Сервер не смог завершить запрос.',
  INVALID_RESPONSE: 'Ответ сервера не удалось прочитать. Проверьте сохранённое состояние.',
  PROCESS_INTERRUPTED: 'Процесс анализа был прерван. Новая попытка запускается явно.',
  RUN_INTERRUPTED: 'Сохранение завершения было прервано. Новая попытка запускается явно.',
  MODEL_TIMEOUT: 'Время ожидания анализа истекло.',
  PROVIDER_ERROR: 'Провайдер вернул ошибку.',
  INVALID_MODEL_OUTPUT: 'Ответ не соответствует контракту разбора.',
  MODEL_REFUSAL: 'Модель отказалась формировать разбор.',
  MODEL_OUTPUT_INCOMPLETE: 'Ответ модели не завершён.',
  CASE_NOT_FOUND: 'Обращение не найдено.',
  SOURCE_NOT_FOUND: 'Источник не найден.',
  SOURCE_VERSION_NOT_FOUND: 'Версия источника не найдена.',
  PROPOSAL_NOT_FOUND: 'Версия разбора не найдена.',
  RUN_NOT_FOUND: 'Запуск анализа не найден.',
  HTTP_404: 'Запрошенные данные не найдены.',
};

export function label(value: string): string {
  return labels[value] ?? value;
}

/** Format integer minor units; never infer a refund amount or use model text. */
export function formatMoney(money: Money | null | undefined): string {
  if (!money) return 'Не указано';
  if (!Number.isSafeInteger(money.amount_minor)) return 'Сумма вне безопасного диапазона';
  const minor = BigInt(money.amount_minor);
  const absolute = minor < 0n ? -minor : minor;
  return `${minor < 0n ? '-' : ''}${absolute / 100n}.${String(absolute % 100n).padStart(2, '0')} USD`;
}

export function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat('ru-RU', {
        day: '2-digit',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      }).format(date);
}
