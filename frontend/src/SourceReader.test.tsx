import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { SourceReader } from './SourceReader';
import type { ReaderDocument } from './SourceReader';

it('renders source text safely and keeps gaps between code-point spans after emoji', () => {
  const text = '😀 да НЕ тогда <img src=x onerror=alert(1)> потом';
  const points = Array.from(text);
  const first = points.indexOf('д');
  const last = points.length - 5;
  const doc: ReaderDocument = {
    source_id: 's',
    version_id: 'v',
    text_sha256: 'h',
    text,
    title: 'Источник',
    origin: 'snapshot',
    spans: [
      {
        source_id: 's',
        source_version_id: 'v',
        source_sha256: 'h',
        start_cp: first,
        end_cp: first + 2,
        fragment: 'да',
        path: [],
      },
      {
        source_id: 's',
        source_version_id: 'v',
        source_sha256: 'h',
        start_cp: last,
        end_cp: points.length,
        fragment: 'потом',
        path: [],
      },
    ],
  };
  const { container } = render(<SourceReader document={doc} />);
  expect([...container.querySelectorAll('mark')].map((e) => e.textContent)).toEqual([
    'да',
    'потом',
  ]);
  expect(screen.getByTestId('source-text')).toHaveTextContent(text);
  expect(container.querySelector('img')).toBeNull();
  expect(container.querySelector('mark')?.textContent).not.toContain('НЕ');
});
