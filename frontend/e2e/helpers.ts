import { expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import type { RunView } from '../src/api-types';

export async function startReplay(page: Page) {
  const response = page.waitForResponse(
    (r) => /\/cases\/[^/]+\/runs$/.test(r.url()) && r.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Запустить Replay' }).click();
  const result = await response;
  expect(result.ok()).toBeTruthy();
  const run = (await result.json()) as RunView;
  await expect(page.getByLabel('Версия разбора')).toHaveValue(run.proposal_revision_id!);
  await expect(page.getByText(/Происхождение: Replay · сохранённая запись/)).toBeVisible();
  return run;
}
