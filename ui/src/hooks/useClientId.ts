/**
 * hooks/useClientId.ts
 * --------------------
 * Generates a stable UUID client_id stored in localStorage.
 * Key: 'oci_drawing_client_id'
 */
import { useState } from 'react';
import { v4 as uuidv4 } from 'uuid';

const STORAGE_KEY = 'oci_drawing_client_id';

function getOrCreateClientId(): string {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) return stored;
    const fresh = uuidv4();
    localStorage.setItem(STORAGE_KEY, fresh);
    return fresh;
  } catch {
    // localStorage unavailable (e.g. SSR, private mode)
    return uuidv4();
  }
}

export function useClientId(): string {
  // Initialised once per session; stable across re-renders
  const [clientId] = useState<string>(() => getOrCreateClientId());
  return clientId;
}

export const DIAGRAM_NAME_KEY = 'oci_drawing_last_diagram_name';

export function getLastDiagramName(): string {
  try {
    return localStorage.getItem(DIAGRAM_NAME_KEY) ?? 'oci_architecture';
  } catch {
    return 'oci_architecture';
  }
}

export function saveLastDiagramName(name: string): void {
  try {
    localStorage.setItem(DIAGRAM_NAME_KEY, name);
  } catch {
    // ignore
  }
}
