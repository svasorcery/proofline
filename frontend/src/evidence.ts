import type { SpanView } from './api-types';

export interface EvidenceSource {
  source_id: string;
  version_id: string;
  text_sha256: string;
  text: string;
}

export interface HighlightSegment {
  text: string;
  highlighted: boolean;
  /** Original indices keep separate fragment identities, including overlaps. */
  spanIndexes: number[];
}

export function splitCodePoints(
  text: string,
  start: number,
  end: number,
): {
  before: string;
  fragment: string;
  after: string;
} {
  const points = Array.from(text);
  if (
    !Number.isInteger(start) ||
    !Number.isInteger(end) ||
    start < 0 ||
    end < start ||
    end > points.length
  ) {
    throw new RangeError('Invalid Unicode code-point interval');
  }
  return {
    before: points.slice(0, start).join(''),
    fragment: points.slice(start, end).join(''),
    after: points.slice(end).join(''),
  };
}

export function highlightSegments(
  source: EvidenceSource,
  spans: readonly SpanView[],
): HighlightSegment[] {
  const points = Array.from(source.text);
  const plain = (): HighlightSegment[] =>
    source.text ? [{ text: source.text, highlighted: false, spanIndexes: [] }] : [];
  const relevant = spans
    .map((span, index) => ({ span, index }))
    .filter(({ span }) => span.source_id === source.source_id);
  if (!relevant.length) return plain();
  for (const { span } of relevant) {
    if (
      span.source_version_id !== source.version_id ||
      span.source_sha256 !== source.text_sha256 ||
      !Number.isInteger(span.start_cp) ||
      !Number.isInteger(span.end_cp) ||
      span.start_cp < 0 ||
      span.end_cp <= span.start_cp ||
      span.end_cp > points.length ||
      points.slice(span.start_cp, span.end_cp).join('') !== span.fragment
    ) {
      return plain();
    }
  }
  const boundaries = new Set([0, points.length]);
  for (const { span } of relevant) {
    boundaries.add(span.start_cp);
    boundaries.add(span.end_cp);
  }
  const cuts = [...boundaries].sort((a, b) => a - b);
  return cuts.slice(0, -1).map((start, position) => {
    const end = cuts[position + 1]!;
    const spanIndexes = relevant
      .filter(({ span }) => span.start_cp <= start && span.end_cp >= end)
      .map(({ index }) => index);
    return {
      text: points.slice(start, end).join(''),
      highlighted: spanIndexes.length > 0,
      spanIndexes,
    };
  });
}
