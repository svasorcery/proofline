import { describe, expect, it } from 'vitest';
import { formatMoney } from './labels';

describe('money from integer minor units', () => {
  it.each([
    [12000, '120.00 USD'],
    [1, '0.01 USD'],
    [0, '0.00 USD'],
    [-1, '-0.01 USD'],
  ])('formats %s minor units as %s', (amount_minor, expected) => {
    expect(formatMoney({ amount_minor, currency: 'USD', scale: 2 })).toBe(expected);
  });

  it('does not manufacture precision outside JavaScript safe integers', () => {
    expect(
      formatMoney({ amount_minor: Number.MAX_SAFE_INTEGER + 1, currency: 'USD', scale: 2 }),
    ).toBe('Сумма вне безопасного диапазона');
  });
});
