'use client';

import Link from 'next/link';
import { useState } from 'react';
import type { Run } from '../lib/releases';
import { RunTime, Status } from './components';

const filters = [
  ['all', 'All runs'], ['block', 'Blocked'], ['pass', 'Passed'], ['pending', 'Pending'], ['inconclusive', 'Inconclusive'],
] as const;

export function RunHistory({ runs }: { runs: Run[] }) {
  const [filter, setFilter] = useState<string>('all');
  const [search, setSearch] = useState('');
  const term = search.trim().toLowerCase();
  const visible = runs.filter(run =>
    (filter === 'all' || (run.verdict ?? 'pending') === filter) &&
    (run.commit_sha.toLowerCase().includes(term) || run.id.toLowerCase().includes(term)),
  );

  return (
    <section className="panel history-panel" aria-labelledby="history-heading">
      <div className="history-heading">
        <div><h2 id="history-heading">Evaluation runs</h2><p>Open a run to review its decision and evidence.</p></div>
        <label className="search-field">
          <span>Find a run</span>
          <input type="search" placeholder="Commit or run ID" value={search} onChange={event => setSearch(event.target.value)} />
        </label>
      </div>
      <div className="filters" role="group" aria-label="Filter evaluations on this page">
        {filters.map(([value, label]) => (
          <button className="filter-button" type="button" key={value} aria-pressed={filter === value}
            onClick={() => setFilter(value)}>{label}</button>
        ))}
      </div>
      <p className="result-count" aria-live="polite">{visible.length} of {runs.length} runs on this page</p>
      {visible.length ? (
        <ul className="run-list">
          {visible.map(run => (
            <li key={run.id}>
              <Link className="run-row" href={`/runs/${encodeURIComponent(run.id)}`}>
                <div><strong>{run.evaluation_mode === 'build_endpoints' ? 'Application comparison' : 'Simulated evaluation'}</strong>
                  <span className="run-meta"><code>{run.commit_sha.slice(0, 12)}</code> <RunTime value={run.created_at} /></span></div>
                <Status value={run.verdict} />
                <span className="run-arrow">Review <span aria-hidden="true">→</span></span>
              </Link>
            </li>
          ))}
        </ul>
      ) : <p className="empty-state">{runs.length ? 'No runs match these filters.' : 'New evaluations will appear here when they are queued.'}</p>}
    </section>
  );
}
