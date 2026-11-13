import { useEffect, useRef } from 'react';
import type { SpanView } from './api-types';
import { highlightSegments } from './evidence';
import { JsonDetails } from './shared';

export interface ReaderDocument {
  source_id: string;
  version_id: string;
  text_sha256: string;
  text: string;
  title: string;
  origin: 'snapshot' | 'current';
  spans: SpanView[];
  focus?: SpanView;
}

export function SourceReader({ document: doc }: { document: ReaderDocument | null }) {
  const pane = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const mark = pane.current?.querySelector('[data-focused="true"]');
    mark?.scrollIntoView?.({ block: 'center', behavior: 'instant' });
  }, [doc]);
  if (!doc)
    return (
      <aside className="reader empty-reader" aria-label="Источник">
        <span className="document-symbol" aria-hidden="true">
          ≡
        </span>
        <h2>Доказательство рядом</h2>
        <p>
          Откройте цитату в разборе. Здесь появится исходная версия документа с отдельной подсветкой
          каждого фрагмента.
        </p>
        <small>Тексты отображаются без исполнения HTML.</small>
      </aside>
    );
  const spans = doc.spans.filter(
    (s) => s.source_id === doc.source_id && s.source_version_id === doc.version_id,
  );
  const segments = highlightSegments(doc, spans);
  return (
    <aside className="reader" aria-label="Источник">
      <div className="reader-heading">
        <p className="eyebrow">
          {doc.origin === 'snapshot'
            ? 'Снимок из выбранного разбора'
            : 'Версия из текущего каталога'}
        </p>
        <h2>{doc.title}</h2>
        <p className="subtle">
          {doc.origin === 'snapshot'
            ? 'Тот текст, на котором основано доказательство.'
            : 'Метаданные текущего каталога; выбранная версия текста.'}
        </p>
      </div>
      <div className="source-text" ref={pane} data-testid="source-text">
        {segments.map((segment, i) =>
          segment.highlighted ? (
            <mark key={i} data-focused={segment.spanIndexes.some((n) => spans[n] === doc.focus)}>
              {segment.text}
            </mark>
          ) : (
            <span key={i}>{segment.text}</span>
          ),
        )}
      </div>
      <JsonDetails
        title="Версия источника и SHA256"
        value={{
          source_id: doc.source_id,
          version_id: doc.version_id,
          text_sha256: doc.text_sha256,
        }}
      />
    </aside>
  );
}
