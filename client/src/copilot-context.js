// Apply only manual changes to the current native object, preserving agent edits.
export function changedFields(initial, draft) {
  return Object.fromEntries(Object.entries(draft).filter(([key, value]) =>
    JSON.stringify(value) !== JSON.stringify(initial[key])));
}

export function configPatch(initial, draft) {
  const result = {};
  for (const key of new Set([...Object.keys(initial), ...Object.keys(draft)])) {
    if (JSON.stringify(initial[key]) === JSON.stringify(draft[key])) continue;
    if (!(key in draft)) result[key] = null;
    else if (draft[key] && initial[key] && !Array.isArray(draft[key]) &&
             typeof draft[key] === 'object' && typeof initial[key] === 'object') {
      result[key] = configPatch(initial[key], draft[key]);
    } else result[key] = draft[key];
  }
  return result;
}

export function editorContext(objectId) {
  let snapshot;
  window.dispatchEvent(new CustomEvent('tantalus:context-request', {
    detail: { objectId, provide: value => { snapshot = value; } },
  }));
  return snapshot;
}
