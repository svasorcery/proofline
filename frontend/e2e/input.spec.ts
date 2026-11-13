import { expect, test } from '@playwright/test';

test('create a case and version a source as safe plain text without a replay fallback', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByRole('button', { name: '+ Новое обращение' }).click();
  await page
    .getByRole('textbox', { name: 'Название', exact: true })
    .fill('Учебная проверка источника');
  await page.getByLabel('Текст обращения', { exact: true }).fill('Проверьте исходный документ.');
  await page.getByRole('button', { name: 'Создать обращение', exact: true }).click();
  await expect(page).toHaveURL(/\/cases\/[^/]+$/);
  await page.getByText('Обращение и источники', { exact: false }).click();
  await page.getByRole('button', { name: '+ Добавить источник' }).click();
  await page.getByLabel('Название источника', { exact: true }).fill('Документ с Unicode');
  await page
    .getByRole('combobox', { name: 'Тип источника', exact: true })
    .selectOption('provider_log');
  await page.getByLabel('Расположение (locator)', { exact: true }).fill('demo://source');
  await page
    .getByLabel('Текст источника (можно добавить позже)', { exact: true })
    .fill('😀 До цитаты. <img src=x onerror=alert(1)> Исходный текст.');
  await page.getByRole('button', { name: 'Добавить источник', exact: true }).click();
  await page.getByRole('button', { name: 'Открыть текст', exact: true }).click();
  await expect(page.getByTestId('source-text')).toContainText('<img src=x onerror=alert(1)>');
  await expect(page.getByTestId('source-text').locator('img')).toHaveCount(0);
  await page.getByRole('button', { name: 'Изменить источник' }).click();
  await expect(page.getByRole('combobox', { name: 'Тип источника', exact: true })).toHaveValue(
    'provider_log',
  );
  await page.getByLabel('Текст новой версии', { exact: true }).fill('Новая версия текста.');
  await page.getByRole('button', { name: 'Создать версию текста', exact: true }).click();
  await page.getByRole('button', { name: 'Открыть текст', exact: true }).click();
  await expect(page.getByTestId('source-text')).toHaveText('Новая версия текста.');
  await expect(page.getByRole('button', { name: 'Запустить Replay' })).toBeDisabled();
  await expect(page.getByText('Для этого входа нет записи Replay', { exact: true })).toBeVisible();
});
