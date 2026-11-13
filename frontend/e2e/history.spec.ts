import { expect, test } from '@playwright/test';
import type { CaseDetail, ProposalView } from '../src/api-types';
import { startReplay } from './helpers';

test('source changes make approval historical while evidence keeps its original version', async ({
  page,
  request,
}) => {
  await page.goto('/cases/V01');
  const run = await startReplay(page);
  const proposal = (await (
    await request.get(`/api/v1/proposals/${run.proposal_revision_id}`)
  ).json()) as ProposalView;
  const evidence = proposal.body.facts[0]!.evidence[0]!;
  const source = proposal.snapshot.catalog.find((s) => s.source_id === evidence.source_id)!;
  await page.getByLabel('Комментарий к решению').fill('Проверена исходная версия источника.');
  await page.getByRole('button', { name: 'Подтвердить разбор', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Решение оператора', exact: true })).toBeVisible();
  const current = (await (await request.get('/api/v1/cases/V01')).json()) as CaseDetail;
  const update = await request.post(`/api/v1/cases/V01/sources/${source.source_id}/versions`, {
    data: {
      expected_case_version: current.version,
      text: `😀 Новая версия источника.\n${source.text}`,
    },
  });
  expect(update.ok()).toBeTruthy();
  try {
    await page.getByRole('button', { name: 'Обновить данные' }).click();
    await expect(
      page.getByRole('heading', { name: 'Историческое решение', exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole('button', { name: 'Подтвердить разбор', exact: true }),
    ).toBeDisabled();
    await page.locator('button.evidence').first().click();
    await expect(page.getByTestId('source-text')).toHaveText(source.text!);
    await expect(page.getByText('Снимок из выбранного разбора', { exact: true })).toBeVisible();
  } finally {
    const latest = (await (await request.get('/api/v1/cases/V01')).json()) as CaseDetail;
    const restored = await request.patch(`/api/v1/cases/V01/sources/${source.source_id}`, {
      data: { expected_case_version: latest.version, current_version_id: source.version_id },
    });
    expect(restored.ok()).toBeTruthy();
  }
});
