import { useRef, useState } from 'react';
import type { DragEvent, ReactNode } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronRight, FolderClosed, GripVertical, PanelLeftClose, PanelLeftOpen, RotateCcw } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { api } from './api';
import { defaultSidebarOrder, moveSidebarItem, normalizeSidebarOrder, sortSidebarLinks } from './sidebarOrder';

export type SidebarLink = {to: string; label: string; Icon: LucideIcon; badge?: ReactNode; href?: string; active?: boolean; folder?: string};

export default function SidebarNavigation({links, order, collapsed, toggle, onSaved, saveEndpoint = '/auth/sidebar-order', resetOrder = defaultSidebarOrder}: {
  links: SidebarLink[]; order: string[]; collapsed: boolean; toggle: () => void;
  onSaved: (order: string[]) => void;
  saveEndpoint?: string; resetOrder?: readonly string[];
}) {
  const [editing, setEditing] = useState(false), [saving, setSaving] = useState(false);
  const [draft, setDraft] = useState(() => normalizeSidebarOrder(order));
  const [error, setError] = useState(''), [announcement, setAnnouncement] = useState('');
  const [dragging, setDragging] = useState<string | null>(null);
  const location = useLocation();
  const [folderOpen,setFolderOpen] = useState(!location.pathname.includes('/contractor/'));
  const [drop, setDrop] = useState<{target: string; side: 'before' | 'after'} | null>(null);
  const dragSource = useRef<string | null>(null);
  const shown = sortSidebarLinks(links, editing ? draft : order);
  const changed = draft.join('|') !== normalizeSidebarOrder(order).join('|');

  function start() {
    if (collapsed) toggle();
    setDraft(normalizeSidebarOrder(order)); setError(''); setAnnouncement(''); setEditing(true);
  }
  function move(source: string, target: string, side: 'before' | 'after') {
    if (saving) return;
    const updated = moveSidebarItem(draft, source, target, side);
    setDraft(updated); setError('');
    const visible = sortSidebarLinks(links, updated);
    const index = visible.findIndex(link => link.to === source);
    setAnnouncement(`${visible[index]?.label || ''}: позиция ${index + 1} из ${visible.length}`);
  }
  function finishDrag() { dragSource.current = null; setDragging(null); setDrop(null); }
  function over(event: DragEvent<HTMLDivElement>, target: string) {
    if (saving || !dragSource.current || dragSource.current === target) return;
    event.preventDefault(); event.dataTransfer.dropEffect = 'move';
    const bounds = event.currentTarget.getBoundingClientRect();
    setDrop({target, side: event.clientY < bounds.top + bounds.height / 2 ? 'before' : 'after'});
  }
  async function save() {
    if (saving) return;
    setSaving(true); setError('');
    try {
      const result = await api<{sidebar_order: string[]}>(saveEndpoint, 'PUT', {sidebar_order: draft});
      onSaved(result.sidebar_order); setEditing(false); setAnnouncement('Порядок меню сохранён');
    } catch (error) { setError((error as Error).message); }
    finally { setSaving(false); }
  }
  function simpleLink({to,label,Icon,badge,href,active}:SidebarLink){
    return href ? <Link key={to} to={href} title={label} aria-label={label} aria-current={active?'page':undefined} className={[active?'active':'',to==='/messages'?'operator-chats-link':''].filter(Boolean).join(' ')||undefined}><Icon size={19}/><span className="nav-item-label">{label}</span>{badge}</Link> : <NavLink key={to} to={to} title={label} aria-label={label} className={to==='/messages'?'operator-chats-link':to==='/news'?'news-notifications-link':undefined} end={to==='/' }><Icon size={19}/><span className="nav-item-label">{label}</span>{badge}</NavLink>;
  }

  return <>
    <div className="sidebar-nav-heading">
      <div className="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
      <div className="sidebar-heading-actions">
        <button type="button" className="sidebar-toggle" title="Изменить порядок меню" aria-label="Изменить порядок меню"
          aria-pressed={editing} disabled={editing} onClick={start}><ArrowUpDown size={17}/></button>
        <button type="button" className="sidebar-toggle" title={collapsed?'Развернуть панель':'Свернуть панель'}
          aria-label={collapsed?'Развернуть панель':'Свернуть панель'} aria-expanded={!collapsed}
          aria-controls="workspace-navigation" disabled={editing} onClick={toggle}>
          {collapsed?<PanelLeftOpen size={18}/>:<PanelLeftClose size={18}/>}
        </button>
      </div>
    </div>
    {editing && <div className="sidebar-order-controls">
      <p>Перетаскивайте пункты или двигайте стрелками.</p>
      <div><button type="button" className="primary" disabled={saving || !changed} onClick={save}>{saving?'Сохраняем…':'Сохранить'}</button>
        <button type="button" disabled={saving} onClick={() => {setEditing(false); setError(''); setAnnouncement('');}}>Отмена</button>
        <button type="button" className="sidebar-order-reset" disabled={saving} title="Вернуть стандартный порядок"
          aria-label="Вернуть стандартный порядок" onClick={() => {setDraft(normalizeSidebarOrder(resetOrder)); setError('');}}><RotateCcw size={15}/></button></div>
      {error && <p className="sidebar-order-error" role="alert">{error} Изменения не сохранены.</p>}
    </div>}
    <span className="sidebar-order-announcement" role="status" aria-live="polite">{announcement}</span>
    <nav id="workspace-navigation" aria-label="Основная навигация" className={editing?'sidebar-order-editing':undefined}>
      {editing ? shown.map(({to, label, Icon}, index) => <div key={to}
        className={'sidebar-sort-item' + (dragging===to?' dragging':'') + (drop?.target===to?' drop-'+drop.side:'')}
        draggable={!saving} onDragStart={event => {
          if (saving) {event.preventDefault(); return;}
          dragSource.current=to; setDragging(to); event.dataTransfer.effectAllowed='move';
          event.dataTransfer.setData('application/x-max-photo-menu',to);
          event.dataTransfer.setData('text/plain',to);
        }} onDragEnd={finishDrag} onDragOver={event => over(event,to)} onDragLeave={event => {
          if (!(event.relatedTarget instanceof Node) || !event.currentTarget.contains(event.relatedTarget)) setDrop(null);
        }} onDrop={event => {
          if (!saving && dragSource.current && dragSource.current!==to) {
            event.preventDefault();
            const bounds=event.currentTarget.getBoundingClientRect();
            move(dragSource.current,to,event.clientY<bounds.top+bounds.height/2?'before':'after');
          }
          finishDrag();
        }}>
        <GripVertical className="sidebar-drag-handle" size={14} aria-hidden="true"/>
        <Icon size={18} aria-hidden="true"/><span className="sidebar-sort-label">{label}</span>
        <div className="sidebar-sort-actions">
          <button type="button" disabled={saving || index===0} aria-label={`Поднять «${label}»`} title="Выше"
            onClick={() => move(to,shown[index-1].to,'before')}><ArrowUp size={14}/></button>
          <button type="button" disabled={saving || index===shown.length-1} aria-label={`Опустить «${label}»`} title="Ниже"
            onClick={() => move(to,shown[index+1].to,'after')}><ArrowDown size={14}/></button>
        </div>
      </div>) : <>{shown.filter(link=>!link.folder).map(simpleLink)}<div className="sidebar-folder"><button type="button" aria-expanded={folderOpen} onClick={()=>setFolderOpen(value=>!value)} title="Метка"><FolderClosed size={19}/><span className="nav-item-label">Метка</span>{folderOpen?<ChevronDown size={15}/>:<ChevronRight size={15}/>}</button>{folderOpen&&<div className="sidebar-folder-items">{shown.filter(link=>link.folder==='Метка').map(simpleLink)}</div>}</div></>}
    </nav>
  </>;
}
