import { expect, test } from '@playwright/test';

test('replay demo explains disabled live without creating a run', async ({ page, request }) => {
  const before = await (await request.get('/api/v1/cases/V02/runs')).json();
  await page.goto('/cases/V02');
  await expect(page.getByRole('combobox', { name: 'Режим анализа' })).toHaveValue('replay');
  await expect(page.getByRole('option', { name: 'Live — новый вызов' })).toBeDisabled();
  await expect(page.getByText('Live недоступен', { exact: true })).toBeVisible();
  await expect(
    page.getByText('Live выключен на сервере. Replay доступен без API-ключа.'),
  ).toBeVisible();
  const after = await (await request.get('/api/v1/cases/V02/runs')).json();
  expect(after).toEqual(before);
});
