import { Component, StrictMode } from 'react';
import type { ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { CaseList } from './CaseList';
import { CaseWorkspace } from './CaseWorkspace';
import './style.css';

class ErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <main className="page">
        <h1>Не удалось отобразить страницу</h1>
        <p>Обновите страницу. Серверная история сохранена.</p>
        <a href="/">К обращениям</a>
      </main>
    ) : (
      this.props.children
    );
  }
}

function App() {
  const match = /^\/cases\/([^/]+)\/?$/.exec(window.location.pathname);
  let caseId: string | null = null;
  try {
    caseId = match?.[1] ? decodeURIComponent(match[1]) : null;
  } catch {
    /* Invalid URL displays list. */
  }
  return (
    <>
      <a className="skip-link" href="#main">
        К содержимому
      </a>
      <header className="app-header">
        <a href="/" className="wordmark" aria-label="Proofline, обращения">
          <span className="brand-mark" aria-hidden="true">
            p
          </span>
          proofline<span className="wordmark-divider">/</span>
          <span className="wordmark-caption">проверка обращений</span>
        </a>
        <div className="demo-label">
          <span className="dot" />
          Учебное демо · Replay
        </div>
      </header>
      {caseId ? <CaseWorkspace key={caseId} caseId={caseId} /> : <CaseList />}
      <footer className="app-footer">
        Синтетические данные. Решение касается разбора обращения; финансовые действия и отправка
        сообщений не выполняются.
      </footer>
    </>
  );
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
