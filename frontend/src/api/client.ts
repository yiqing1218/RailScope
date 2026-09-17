const BASE_URL = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8000/api/v1';
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, { headers: {'Content-Type': 'application/json'}, ...init });
  if (!response.ok) throw new Error((await response.text()) || response.statusText);
  return response.json() as Promise<T>;
}
export const post = <T>(path: string, payload?: unknown) => api<T>(path, {method:'POST', body: payload === undefined ? undefined : JSON.stringify(payload)});
