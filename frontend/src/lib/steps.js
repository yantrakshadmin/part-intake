/**
 * currentStep — pure function of App's intake state, so the three-step
 * sequence (part -> confirm orientation -> packaging fit) has one place
 * that decides what's on screen instead of scattered truthy checks in JSX.
 * See UI-4: results must be unreachable until the part is actually saved
 * (the solve needs a saved part id), and the confirm step only exists for
 * STEP uploads that produced pose candidates.
 */
export function currentStep({ mode, result, savedPart }) {
  if (savedPart) return 'results'
  if (mode === 'stp' && result) return 'confirm'
  return 'intake'
}
