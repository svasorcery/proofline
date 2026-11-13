import { expect, test } from '@playwright/test';
import type { CaseDetail, RunView } from '../src/api-types';
import { startReplay } from './helpers';

test('lost run response is reconciled with the same key and creates one run', async ({
  page,
  request,
}) => {
  const before = ((await (await request.get('/api/v1/cases/V02/runs')).json()) as RunView[]).length;
  const keys: (string | undefined)[] = [];
  let lost = false;
  await page.route('**/api/v1/cases/V02/runs', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    keys.push(route.request().headers()['idempotency-key']);
    const response = await route.fetch();
    expect(response.ok()).toBeTruthy();
    if (!lost) {
      lost = true;
      await route.abort('failed');
    } else await route.fulfill({ response });
  });
  await page.goto('/cases/V02');
  await page.getByRole('button', { name: 'Запустить Replay' }).click();
  await expect(page.getByText('Исход запроса неизвестен', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Проверить результат запроса' }).click();
  await expect(page.getByText('Исход запроса неизвестен', { exact: true })).toHaveCount(0);
  const after = (await (await request.get('/api/v1/cases/V02/runs')).json()) as RunView[];
  expect(after).toHaveLength(before + 1);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
  expect(after.at(-1)?.generation_profile.new_model_call).toBe(false);
});

test('refresh and a new active replay preserve the proposal draft', async ({ page }) => {
  await page.goto('/cases/V03');
  await startReplay(page);
  await page.getByRole('button', { name: 'Исправить разбор', exact: true }).click();
  await page.getByLabel('Причина исправления', { exact: true }).fill('Черновик с ручной проверкой');
  const runDone = page.waitForResponse(
    (r) => r.url().endsWith('/cases/V03/runs') && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Запустить Replay' }).click();
  await runDone;
  await page.getByRole('button', { name: 'Обновить данные' }).click();
  await expect(page.getByRole('textbox', { name: 'Причина исправления', exact: true })).toHaveValue(
    'Черновик с ручной проверкой',
  );
});

test('a late current-source response cannot replace selected historical evidence', async ({
  page,
}) => {
  await page.goto('/cases/V01');
  await startReplay(page);
  await expect(page.locator('button.evidence').first()).toBeVisible();
  let release = () => {};
  const released = new Promise<void>((resolve) => {
    release = resolve;
  });
  let started = () => {};
  const start = new Promise<void>((resolve) => {
    started = resolve;
  });
  let finished = () => {};
  const finish = new Promise<void>((resolve) => {
    finished = resolve;
  });
  await page.route('**/api/v1/cases/V01/sources/*/versions/*', async (route) => {
    const response = await route.fetch();
    started();
    await released;
    try {
      await route.fulfill({ response });
    } finally {
      finished();
    }
  });
  await page.getByText('Обращение и источники', { exact: false }).click();
  await page.getByRole('button', { name: 'Открыть текст', exact: true }).first().click();
  await start;
  await page.locator('button.evidence').first().click();
  release();
  await finish;
  await expect(page.getByText('Снимок из выбранного разбора', { exact: true })).toBeVisible();
});

test('another tab causes a visible conflict and preserves the operator comment', async ({
  page,
  request,
}) => {
  await page.goto('/cases/V05');
  await startReplay(page);
  await page.getByLabel('Комментарий к решению').fill('Комментарий до параллельного изменения');
  const original = (await (await request.get('/api/v1/cases/V05')).json()) as CaseDetail;
  const update = await request.patch('/api/v1/cases/V05', {
    data: {
      expected_case_version: original.version,
      title: `${original.title} (проверка конфликта)`,
    },
  });
  expect(update.ok()).toBeTruthy();
  try {
    await page.getByRole('button', { name: 'Подтвердить разбор', exact: true }).click();
    await expect(page.getByRole('alert').filter({ hasText: 'Обращение изменилось' })).toBeVisible();
    await expect(page.getByLabel('Комментарий к решению')).toHaveValue(
      'Комментарий до параллельного изменения',
    );
    await page.getByRole('button', { name: 'Обновить данные' }).click();
    await expect(page.getByRole('button', { name: 'Проверил обновлённые данные' })).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Подтвердить разбор', exact: true }),
    ).toBeDisabled();
  } finally {
    const current = (await (await request.get('/api/v1/cases/V05')).json()) as CaseDetail;
    await request.patch('/api/v1/cases/V05', {
      data: { expected_case_version: current.version, title: original.title },
    });
  }
});
