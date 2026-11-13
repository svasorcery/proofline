import { describe, expect, it } from 'vitest';
import { highlightSegments, splitCodePoints } from './evidence';
import type { SpanView } from './api-types';

const source = {
  source_id: 'S1',
  version_id: 'v1',
  text_sha256: 'sha1',
  text: '😀Да, но не сейчас. <b>Текст</b>',
};
function span(start: number, end: number, fragment: string): SpanView {
  return {
    source_id: 'S1',
    source_version_id: 'v1',
    source_sha256: 'sha1',
    start_cp: start,
    end_cp: end,
    fragment,
    path: ['facts', 0, 'evidence', 0, 'fragments', 0],
  };
}

describe('server evidence intervals', () => {
  it('uses Unicode code points after emoji and preserves unquoted negation', () => {
    const result = highlightSegments(source, [span(1, 3, 'Да'), span(11, 17, 'сейчас')]);
    expect(result.filter((s) => s.highlighted).map((s) => s.text)).toEqual(['Да', 'сейчас']);
    expect(
      result
        .filter((s) => !s.highlighted)
        .map((s) => s.text)
        .join(''),
    ).toContain(', но не ');
    expect(result.map((s) => s.text).join('')).toBe(source.text);
  });

  it('keeps adjoining fragments separate and retains overlap membership', () => {
    const doc = { ...source, text: 'abcdef' };
    const result = highlightSegments(doc, [span(0, 2, 'ab'), span(2, 4, 'cd'), span(3, 6, 'def')]);
    expect(result).toEqual([
      { text: 'ab', highlighted: true, spanIndexes: [0] },
      { text: 'c', highlighted: true, spanIndexes: [1] },
      { text: 'd', highlighted: true, spanIndexes: [1, 2] },
      { text: 'ef', highlighted: true, spanIndexes: [2] },
    ]);
  });

  it.each([
    { ...span(1, 3, 'Да'), source_version_id: 'v2' },
    { ...span(1, 3, 'Да'), source_sha256: 'other' },
    span(1, 3, 'да'),
    span(-1, 3, 'Да'),
    span(1, 1000, 'Да'),
    span(1.5, 3, 'Да'),
  ])('does not trust a mismatched binding or interval: %j', (invalid) => {
    expect(highlightSegments(source, [invalid])).toEqual([
      { text: source.text, highlighted: false, spanIndexes: [] },
    ]);
  });

  it('renders HTML-like text as a string and ignores evidence belonging to other sources', () => {
    expect(highlightSegments(source, [{ ...span(1, 3, 'Да'), source_id: 'other' }])).toEqual([
      { text: source.text, highlighted: false, spanIndexes: [] },
    ]);
  });

  it('extracts a focused fragment without UTF-16 offset drift', () => {
    expect(splitCodePoints('😀Подтверждаю', 1, 12)).toEqual({
      before: '😀',
      fragment: 'Подтверждаю',
      after: '',
    });
    expect(() => splitCodePoints('text', -1, 2)).toThrow(RangeError);
  });
});
