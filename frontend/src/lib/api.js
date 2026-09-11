/**
 * api.js — FastAPI error responses: detail is a string (HTTPException) or
 * an array of validation objects (422) — render both as readable text.
 * Was duplicated inline in App.jsx; now shared by NewProject, ProjectsList
 * and ProjectPage.
 */
export async function readError(r, fallback) {
  try {
    const d = (await r.json()).detail
    if (typeof d === 'string') return d
    if (Array.isArray(d))
      return d.map((e) => `${e.loc?.at(-1) ?? 'field'}: ${e.msg}`).join(' · ')
    return JSON.stringify(d)
  } catch { return fallback }
}
