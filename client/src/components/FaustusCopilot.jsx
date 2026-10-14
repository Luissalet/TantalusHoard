import React, { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { editorContext } from '../copilot-context.js';
import faustusMark from '../assets/faustus-mark.svg';
import '../copilot.css';

/** Domain context only; chat, drafts, retries and tools execution live in Faustus. */
export default function FaustusCopilot({ route, lang, onChanged }) {
  const [open, setOpen] = useState(false);
  const [error, setError] = useState('');
  const container = useRef(null);
  const current = useRef({ route, lang, onChanged });
  current.current = { route, lang, onChanged };
  const reference = route.page === 'watchers' || route.page === 'target' ? route.page + ':' + route.param : null;

  useEffect(() => {
    if (!open) return;
    let disposed = false;
    let panel;
    async function mount() {
      try {
        const response = await fetch('/api/copilot/info');
        if (!response.ok) throw new Error(await response.text());
        const info = await response.json();
        await import(/* @vite-ignore */ info.panel_url);
        if (disposed) return;
        panel = document.createElement('faustus-chat');
        panel.server = window.location.origin;
        panel.locale = current.current.lang;
        panel.theme = 'dark';
        panel.onOpenSettings = () => window.open(info.settings_url, '_blank', 'noopener');
        panel.fetch = async (input, init = {}) => {
          const request = new Request(input, init);
          const url = new URL(request.url);
          const envelope = { path: url.pathname + url.search, method: request.method, headers: {} };
          for (const name of ['X-Faustus-Client-Version', 'X-Odysseus-Run-Id']) {
            const value = request.headers.get(name);
            if (value) envelope.headers[name] = value;
          }
          if (request.method !== 'GET') {
            const bytes = new Uint8Array(await request.arrayBuffer());
            const type = request.headers.get('Content-Type') || '';
            if (type.startsWith('application/json')) envelope.body = JSON.parse(new TextDecoder().decode(bytes));
            else { envelope.content_type = type; envelope.raw_body = btoa(Array.from(bytes, byte => String.fromCharCode(byte)).join('')); }
          }
          return fetch('/api/copilot/proxy', { method: 'POST', headers: {'Content-Type':'application/json'}, body:JSON.stringify(envelope), signal:request.signal });
        };
        panel.contextProvider = async () => {
          const { route: view, lang: locale } = current.current;
          const origin = { app_id:info.app_id, app_name:info.app_name, data_space_id:info.data_space_id, surface:'editor', tool_server_id:info.tool_server_id };
          let watcher, target;
          if (view.page === 'watchers' && view.param && view.param !== 'new') watcher = await api.call('watcher_get', {watcher_id:view.param});
          if (view.page === 'target' && view.param) {
            const result = await api.call('target_get', {target_id:view.param});
            target = result.target;
            if (target?.watcher_id) watcher = await api.call('watcher_get', {watcher_id:target.watcher_id});
          }
          return { origin, ...(watcher ? {project:{external_project_id:watcher.id,title:watcher.name}} : {}),
            active_objects: target ? [{kind:'target',object_id:target.id,title:target.label,url:target.url}] : watcher ? [{kind:'watcher',object_id:watcher.id,title:watcher.name}] : [],
            selection:window.getSelection()?.toString() ? {kind:'text',text:window.getSelection().toString()} : null,
            view:{page:view.page,object_id:view.param,locale,editor:editorContext(view.param)} };
        };
        const key = 'tantalus-faustus-' + info.data_space_id + '-' + (reference || 'app');
        panel.session = localStorage.getItem(key);
        panel.addEventListener('faustus:session-change', event => {
          if (event.detail.sessionId) localStorage.setItem(key,event.detail.sessionId);
          else localStorage.removeItem(key);
        });
        panel.addEventListener('faustus:turn-end', () => {
          current.current.onChanged?.();
          window.dispatchEvent(new Event('tantalus:copilot-updated'));
        });
        container.current.replaceChildren(panel);
        await panel.loadHistory();
        setError('');
      } catch (problem) { if (!disposed) setError(((current.current.lang) === 'es' ? 'Faustus no está disponible ahora mismo. Tantalus sigue funcionando y tu borrador se conserva; cierra y vuelve a abrir el panel cuando Faustus esté en marcha.' : 'Faustus is not available right now. Tantalus keeps working and your draft is kept; close and reopen the panel once Faustus is running.') + ' (' + String(problem.message || problem) + ')'); }
    }
    void mount();
    return () => { disposed = true; panel?.remove(); };
  }, [open, reference]);
  useEffect(() => {
    const panel = container.current?.querySelector('faustus-chat');
    if (panel) panel.locale = lang;
  }, [lang]);
  return <>
    <button type="button" className="tantalus-copilot-toggle" aria-label="Faustus" aria-expanded={open} onClick={() => setOpen(!open)}><img src={faustusMark} alt="" aria-hidden="true" /></button>
    {open && <aside className="tantalus-copilot" aria-label="Faustus">
      <header><strong>Faustus · Tantalus</strong><button type="button" className="btn btn-sm" onClick={() => setOpen(false)} aria-label={lang === 'es' ? 'Cerrar Faustus' : 'Close Faustus'}>×</button></header>
      {error && <p role="alert">{error}</p>}
      <div ref={container} className="tantalus-copilot-content" />
    </aside>}
  </>;
}
