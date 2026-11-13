import { expect, test } from '@playwright/test';
import { startReplay } from './helpers';
import type { CaseDetail } from '../src/api-types';

test('withheld source yields an explicit missing-confirmation request', async ({
  page,
  request,
}) => {
  const original = (await (await request.get('/api/v1/cases/V01')).json()) as CaseDetail;
  const source = original.sources.find((s) => s.source_id === 'f9a88d5b726a4d4b')!;
  await page.goto('/cases/V01');
  await page.getByText('Обращение и источники', { exact: false }).click();
  const changed = page.waitForResponse(
    (r) => r.url().endsWith(`/sources/${source.source_id}`) && r.request().method() === 'PATCH',
  );
  await page.getByLabel(`Включить ${source.title}`, { exact: true }).uncheck();
  await changed;
  try {
    await expect(page.getByLabel('Запись Replay')).toContainText('ablation');
    await startReplay(page);
    await expect(page.getByText('Не подтверждено', { exact: true })).toBeVisible();
    await expect(page.getByText('Оригинал безусловного согласия', { exact: true })).toBeVisible();
    await expect(page.getByText('Предложение модели о готовности:')).toContainText(
      'Нужны подтверждения',
    );
  } finally {
    const current = (await (await request.get('/api/v1/cases/V01')).json()) as CaseDetail;
    const restore = await request.patch(`/api/v1/cases/V01/sources/${source.source_id}`, {
      data: { expected_case_version: current.version, included: source.included },
    });
    expect(restore.ok()).toBeTruthy();
  }
});

test('correct replay opens evidence and records a human decision', async ({ page }) => {
  await page.goto('/cases/V01');
  await startReplay(page);
  await expect(page.getByRole('heading', { name: 'Разбор обращения', exact: true })).toBeVisible();
  await page.locator('button.evidence').first().click();
  await expect(page.getByTestId('source-text').locator('mark').first()).toBeVisible();
  await page
    .getByLabel('Комментарий к решению')
    .fill('Проверил факты и исходную переписку в учебном примере.');
  await page.getByRole('button', { name: 'Подтвердить разбор', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Решение оператора', exact: true })).toBeVisible();
  await expect(
    page
      .getByRole('paragraph')
      .filter({ hasText: /^Проверил факты и исходную переписку в учебном примере\.$/ }),
  ).toBeVisible();
});

test('missing confirmation remains explicit and a blocked model response is corrected as a new revision', async ({
  page,
}) => {
  await page.goto('/cases/V04');
  const originalRun = await startReplay(page);
  await expect(
    page.getByRole('button', { name: 'Подтвердить разбор', exact: true }),
  ).toBeDisabled();
  await page.getByRole('button', { name: 'Исправить разбор', exact: true }).click();
  await page.getByLabel('Факт 1: значение', { exact: true }).selectOption('conditional');
  // The recorded V04 invents an unconditional acceptance event. Remove only that event.
  const accepts = page.locator('select[aria-label$=": тип"]');
  let removed = false;
  for (let i = 0; i < (await accepts.count()); i++) {
    if ((await accepts.nth(i).inputValue()) === 'alternative_accepted') {
      await page.getByRole('button', { name: `Удалить событие ${i + 1}`, exact: true }).click();
      removed = true;
      break;
    }
  }
  expect(removed).toBeTruthy();
  await page
    .getByRole('combobox', { name: 'Предложенное действие', exact: true })
    .selectOption('ask_user');
  await page
    .getByLabel('Причина исправления', { exact: true })
    .fill('Согласие условное. Требуется получить подтверждения вместо повторного чтения снимка.');
  await page.getByRole('button', { name: 'Сохранить новую версию', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Подтвердить разбор', exact: true })).toBeEnabled();
  await page
    .getByLabel('Комментарий к решению')
    .fill('Подтверждаю исправленный разбор с запросом недостающих доказательств.');
  await page.getByRole('button', { name: 'Подтвердить разбор', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Решение оператора', exact: true })).toBeVisible();
  await page.getByLabel('Версия разбора').selectOption(originalRun.proposal_revision_id!);
  await expect(page.getByText('Открыта версия из истории')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Подтвердить разбор', exact: true }),
  ).toBeDisabled();
});

test('operator can reject with a reason and mobile retains selected evidence', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/cases/V06');
  await startReplay(page);
  await page.locator('button.evidence').first().click();
  await expect(page.getByTestId('source-text')).toBeVisible();
  await page.getByRole('button', { name: 'Разбор', exact: true }).click();
  await page
    .getByLabel('Комментарий к решению')
    .fill('Недостаточно подтверждений для содержательного принятия разбора.');
  await page.getByRole('button', { name: 'Отклонить разбор', exact: true }).click();
  await expect(
    page
      .getByRole('paragraph')
      .filter({ hasText: /^Недостаточно подтверждений для содержательного принятия разбора\.$/ }),
  ).toBeVisible();
  await expect(page.locator('body')).toHaveJSProperty('scrollWidth', 390);
});
