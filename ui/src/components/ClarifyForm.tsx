import React, { useState } from 'react';
import type { GenerateResponse } from '../api/client';

interface Props {
  result: GenerateResponse;
  onSubmit: (answers: string) => void;
  loading: boolean;
}

export function ClarifyForm({ result, onSubmit, loading }: Props) {
  const [answers, setAnswers] = useState('');

  if (result.status !== 'need_clarification') return null;

  return (
    <div data-testid="clarify-form">
      <h3>Clarification Needed</h3>
      <ul>
        {(result.questions ?? []).map((q) => (
          <li key={q.id}>
            <strong>{q.id}</strong>: {q.question}
            {q.blocking && (
              <em style={{ color: '#c00', marginLeft: '0.5rem' }}>(required)</em>
            )}
          </li>
        ))}
      </ul>
      <label htmlFor="clarify-answers">
        <strong>Your answers:</strong>
      </label>
      <br />
      <textarea
        id="clarify-answers"
        data-testid="clarify-answers"
        value={answers}
        onChange={(e) => setAnswers(e.target.value)}
        rows={5}
        style={{ width: '100%', marginTop: '0.5rem' }}
        placeholder="Answer each question above…"
      />
      <br />
      <button
        data-testid="clarify-submit"
        onClick={() => onSubmit(answers)}
        disabled={loading || !answers.trim()}
        style={{ marginTop: '0.5rem' }}
      >
        {loading ? 'Submitting…' : 'Submit Answers'}
      </button>
      {loading && (
        <p data-testid="clarify-loading" style={{
          marginTop: '0.75rem',
          color: '#e8571a',
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: '0.82rem',
        }}>
          ⏳ Generating diagram… this may take up to 90 seconds.
        </p>
      )}
    </div>
  );
}
