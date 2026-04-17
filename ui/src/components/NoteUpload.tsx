import React, { useState } from 'react';
import { apiUploadNote, apiListNotes, type NoteEntry } from '../api/client';

interface Props {
  customerId: string;
  onCustomerIdChange: (id: string) => void;
}

export function NoteUpload({ customerId, onCustomerIdChange }: Props) {
  const [noteName, setNoteName] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [notes, setNotes] = useState<NoteEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [listLoading, setListLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!customerId.trim() || !file) return;
    setLoading(true);
    setMessage(null);
    setError(null);
    try {
      const resp = await apiUploadNote(customerId.trim(), noteName.trim(), file);
      setMessage(`Uploaded: ${resp.note_name} → ${resp.key}`);
      setNoteName('');
      setFile(null);
      // Refresh list
      await refreshNotes();
    } catch (err: unknown) {
      const e2 = err as { detail?: string; status?: number };
      setError(`Upload failed: ${e2.detail ?? String(err)}`);
    } finally {
      setLoading(false);
    }
  }

  async function refreshNotes() {
    if (!customerId.trim()) return;
    setListLoading(true);
    try {
      const resp = await apiListNotes(customerId.trim());
      setNotes(resp.notes);
    } catch {
      setNotes([]);
    } finally {
      setListLoading(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '0.4rem', boxSizing: 'border-box', marginBottom: '0.5rem',
  };

  return (
    <div>
      <h2 style={{ fontSize: '1.1rem', marginBottom: '0.75rem' }}>Upload Meeting Notes</h2>
      <p style={{ fontSize: '0.85rem', color: '#555', marginBottom: '1rem' }}>
        Notes are stored per customer and used as context for POV and JEP generation.
        All notes for a customer are read together when generating documents.
      </p>

      <form onSubmit={handleUpload}>
        <label style={{ display: 'block', marginBottom: '0.25rem', fontWeight: 'bold', fontSize: '0.85rem' }}>
          Customer ID *
        </label>
        <input
          style={inputStyle}
          value={customerId}
          onChange={e => onCustomerIdChange(e.target.value)}
          placeholder="e.g. jane_street"
          required
        />

        <label style={{ display: 'block', marginBottom: '0.25rem', fontWeight: 'bold', fontSize: '0.85rem' }}>
          Note name (optional — defaults to filename)
        </label>
        <input
          style={inputStyle}
          value={noteName}
          onChange={e => setNoteName(e.target.value)}
          placeholder="e.g. kickoff_meeting_2025-03.md"
        />

        <label style={{ display: 'block', marginBottom: '0.25rem', fontWeight: 'bold', fontSize: '0.85rem' }}>
          Notes file * (text, markdown, or plain text)
        </label>
        <input
          type="file"
          style={{ marginBottom: '0.75rem' }}
          accept=".txt,.md,.text"
          onChange={e => setFile(e.target.files?.[0] ?? null)}
          required
        />

        <button type="submit" disabled={loading || !customerId.trim() || !file}>
          {loading ? 'Uploading…' : 'Upload Note'}
        </button>
        <button
          type="button"
          onClick={refreshNotes}
          disabled={listLoading || !customerId.trim()}
          style={{ marginLeft: '0.5rem' }}
        >
          {listLoading ? 'Loading…' : 'Refresh List'}
        </button>
      </form>

      {message && (
        <div style={{ marginTop: '0.75rem', padding: '0.5rem', background: '#f0fff0', border: '1px solid #080', borderRadius: '4px', fontSize: '0.85rem' }}>
          {message}
        </div>
      )}
      {error && (
        <div style={{ marginTop: '0.75rem', padding: '0.5rem', background: '#fff0f0', border: '1px solid #c00', borderRadius: '4px', fontSize: '0.85rem' }}>
          {error}
        </div>
      )}

      {notes.length > 0 && (
        <div style={{ marginTop: '1rem' }}>
          <strong style={{ fontSize: '0.9rem' }}>Notes for {customerId}:</strong>
          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: '0.5rem', fontSize: '0.82rem' }}>
            <thead>
              <tr style={{ borderBottom: '2px solid #ddd' }}>
                <th style={{ textAlign: 'left', padding: '0.3rem 0.5rem' }}>Name</th>
                <th style={{ textAlign: 'left', padding: '0.3rem 0.5rem' }}>Uploaded</th>
              </tr>
            </thead>
            <tbody>
              {notes.map(n => (
                <tr key={n.key} style={{ borderBottom: '1px solid #eee' }}>
                  <td style={{ padding: '0.3rem 0.5rem' }}>{n.name}</td>
                  <td style={{ padding: '0.3rem 0.5rem', color: '#666' }}>
                    {new Date(n.timestamp).toLocaleString()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
