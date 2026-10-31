import { createContext, lazy, Suspense, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowDownToLine, ArchiveX, Camera, ChartNoAxesCombined, CheckCheck, ChevronLeft, ChevronRight, CircleCheck, ClipboardCheck, Layers, LogOut, Map, MapPin, MessageSquare, Newspaper, RefreshCw, ShieldCheck, Users } from 'lucide-react';
import { api, dateLabel, photoLink, query, reportPhotoLink, shiftDate, statusNames } from './api';
import type { Filters, Meta, PhotoPage, Report, User } from './types';
import PhotoDetail from './PhotoDetail';
import DistrictMap from './DistrictMap';
import PhotoMap from './PhotoMap';
import Administration from './Administration';
import Learning from './Learning';
import Exchange from './Exchange';
import Settings from './Settings';
import WorkTypes from './WorkTypes';
import Contractor from './Contractor';
import News from './News';
import Avatar from './Avatar';
import OperatorChats from './OperatorChats';
import { OperatorChatBadge, OperatorChatsProvider } from './OperatorChatsProvider';
import { NewsNotificationsProvider, NewsUnreadBadge } from './NewsNotificationsProvider';
import { hasPermission } from './permissions';
import { PhotoNotificationsProvider } from './PhotoNotifications';
import FilterBar from './FilterBar';
import { photoSortForUser } from './photoSorting';
import { capturePhotoListPosition, photoListAnchor, photoListPage, restorePhotoListPosition } from './photoNavigation';
import type { PhotoListPosition } from './photoNavigation';
import { canEditPhoto, updatePhotoDefect } from './photoWork';
import type { Photo } from './types';
import PhotoTable from './PhotoTable';
import PhotoNumberSearch from './PhotoNumberSearch';
import './photo-owner.css';
import useSidebar from './useSidebar';
import SidebarNavigation from './SidebarNavigation';
import DayReviews from './DayReviews';
import ClosedDayUpdates from './ClosedDayUpdates';
import OperatorDayStatus from './OperatorDayStatus';
import WorkDistrictMatrix from './WorkDistrictMatrix';
import { dayReviewDate } from './dayReviewData';
import './organizations.css';
import { workspacePrefix } from './workspace';
import Login, { SignInRedirect } from './Login';
import { AppealNotice, useAppealNotifications } from './appeals/notifications';
const OwnerAppeals = lazy(() => import('./appeals/OwnerAppeals'));
const InspectionAdministration = lazy(() => import('./inspection/Administration'));
const DailyReports = lazy(() => import('./daily-reports/DailyReports'));

const Context = createContext<{ user: User; meta: Meta; refresh: () => Promise<void>; notify: (text: string) => void; updateUser: (user: User) => void }>(null!);
export const AppContextProvider = Context.Provider;
export const useApp = () => useContext(Context);
export const ErrorBox = ({ error }: { error: string }) => error ? <div className="error" role="alert">{error}</div> : null;
export const Empty = ({ children }: { children: ReactNode }) => <div className="empty"><Camera size={34}/><h3>Пока ничего нет</h3><p>{children}</p></div>;
export function StatusBadge({ value }: { value: string }) { return <span className={'badge status-' + value}>{statusNames[value] || value}</span>; }


export default function App() {
  const sidebar = useSidebar();
  const [user,setUser] = useState<User|null>(null), [meta,setMeta] = useState<Meta|null>(null), [loading,setLoading] = useState(true), [error,setError] = useState(''), [toast,setToast] = useState('');
  const appeals = useAppealNotifications(!!user && !!meta?.organization, user?.id);
  const navigate = useNavigate();
  async function refresh() { setMeta(await api<Meta>('/meta')); }
  useEffect(()=>{api<User>('/auth/me').then(setUser).catch(e=>{if(e.status!==401)setError(e.message);}).finally(()=>setLoading(false)); const handler=()=>{setUser(null);setMeta(null);}; window.addEventListener('session-expired',handler);return()=>window.removeEventListener('session-expired',handler);},[]);
  useEffect(()=>{if(user)refresh().catch(e=>setError(e.message));},[user]);
  useEffect(()=>{if(toast){const t=setTimeout(()=>setToast(''),5000);return()=>clearTimeout(t);}},[toast]);
  if(loading)return <div className="full-loading">Открываем рабочее пространство…</div>;
  if(!user&&workspacePrefix())return <SignInRedirect/>;
  if(!user)return <Login initialError={error} onLogin={u=>{setUser(u);setError('');}}/>;
  if(!meta)return <div className="full-loading"><ErrorBox error={error}/>{error?<button onClick={()=>refresh().then(()=>setError('')).catch(e=>setError(e.message))}>Повторить</button>:'Загружаем справочники…'}</div>;
  const admin=user.role==='ADMIN_OPERATOR';
  const links = [
    {to:'/contractor/vehicles', label:'НСИ подрядчика', Icon:Layers, allowed:true},
    {to:'/', label:'Обзор', Icon:ChartNoAxesCombined, allowed:hasPermission(user,'reports_view')},
    {to:'/news', label:'Новости', Icon:Newspaper, allowed:hasPermission(user,'news_view')},
    {to:'/photos', label:'Фотографии', Icon:Camera, allowed:hasPermission(user,'photos_view')},
    {to:'/photo-map', label:'Карта фотографий', Icon:MapPin, allowed:hasPermission(user,'photos_view')},
    {to:'/review', label:'Требуют проверки', Icon:ClipboardCheck, allowed:hasPermission(user,'photos_edit')},
    {to:'/days', label:'Проверка дней', Icon:CheckCheck, allowed:hasPermission(user,'days_review')},
    {to:'/daily-reports', label:'Ежедневные отчёты', Icon:ClipboardCheck, allowed:hasPermission(user,'daily_reports_view')},
    {to:'/spam', label:'Спам', Icon:ArchiveX, allowed:hasPermission(user,'spam_view')},
    {to:'/districts', label:'Микрорайоны', Icon:Map, allowed:hasPermission(user,'districts_view')},
    {to:'/appeals', label:admin ? 'Обращения' : 'Мои задачи по обращениям', Icon:MessageSquare, allowed:!!meta.organization},
    {to:'/inspectors', label:'Проверяющие', Icon:ShieldCheck, allowed:admin && !!meta.organization},
    {to:'/messages', label:'Чаты операторов', Icon:MessageSquare, allowed:true},
    {to:'/chats', label:'Чаты MAX', Icon:MessageSquare, allowed:hasPermission(user,'chats_manage')},
    {to:'/work-types', label:'Виды работ', Icon:Layers, allowed:admin},
    {to:'/users', label:'Операторы', Icon:Users, allowed:admin},
    {to:'/audit', label:'Журнал действий', Icon:ShieldCheck, allowed:hasPermission(user,'audit_view')},
    {to:'/jobs', label:'Обработка', Icon:RefreshCw, allowed:hasPermission(user,'jobs_manage')},
    {to:'/exchange', label:'Обмен с АРМ', Icon:ArrowDownToLine, allowed:hasPermission(user,'exchange_manage')},
    {to:'/learning', label:'Обучение нейросети', Icon:Layers, allowed:hasPermission(user,'learning_manage')},
  ].filter(link => link.allowed);
  const home = links.find(link => link.to !== '/news')?.to || '/news';
  const denied = <Navigate to={home} replace/>;
  const protectedPage = (code:string, element:ReactNode) => hasPermission(user,code) ? element : denied;
  return <Context.Provider value={{user,meta,refresh,notify:setToast,updateUser:setUser}}>
    <NewsNotificationsProvider key={user.id} enabled={hasPermission(user,'news_view')}>
    <OperatorChatsProvider key={user.id}>
    <PhotoNotificationsProvider key={user.id + ":" + meta.intake_cohort} userId={user.id} canViewPhotos={hasPermission(user,'photos_view')}>
    <div className={'app ' + (sidebar.collapsed ? 'sidebar-collapsed' : 'sidebar-expanded')}>
      {!sidebar.collapsed&&<button type="button" className="sidebar-backdrop" aria-label="Закрыть боковую панель" onClick={sidebar.toggle}/>}
      <aside className="sidebar">
        <NavLink className="brand" to={home} aria-label="Метка — главная"><span className="brand-mark"><Layers size={23}/></span><span className="brand-copy">Метка<small>Контроль городских работ</small></span></NavLink>
        <SidebarNavigation key={user.id} links={links.map(link=>({...link,badge:link.to==='/messages'?<OperatorChatBadge/>:link.to==='/news'?<NewsUnreadBadge/>:link.to==='/appeals' && appeals.count ? <span className="count-pill appeal-count" aria-label={`Новых обращений: ${appeals.count}`}>{appeals.count}</span>:undefined}))}
          order={user.sidebar_order} collapsed={sidebar.collapsed} toggle={sidebar.toggle}
          onSaved={sidebar_order=>setUser(current=>current?.id===user.id?{...current,sidebar_order}:current)}/>
        <div className="sidebar-bottom">
          <NavLink to="/settings" className="account-link" title="Настройки" aria-label="Настройки">
            <Avatar name={user.name} src={user.avatar_url}/>
            <span className="account-info"><strong>{user.name}</strong><small>{admin?'Администратор':'Оператор'}</small></span>
          </NavLink>
          <button title="Выйти" aria-label="Выйти" onClick={async()=>{try{await api('/auth/logout','POST');setUser(null);setMeta(null);navigate('/');}catch(e){setToast((e as Error).message);}}}><LogOut size={17}/></button>
        </div>
      </aside>
      <main className="workspace">
        <div className="topbar"><span className="workspace-identity"><span className="dot"/>{meta.organization?<><strong>{meta.organization.name}</strong>{user.is_platform_owner&&<a className="organization-switch" href="/">Все организации<ChevronRight size={13}/></a>}</>:<>Операторская панель</>}{meta.intake_cohort==='test'&&<span className="badge">Тестовый режим</span>}</span><span>{dateLabel(meta.today)} <span className="timezone">{meta.timezone}</span></span></div>
        <Routes>
          <Route path="/contractor/:kind/:id?" element={<Contractor/>}/>
          <Route path="/" element={protectedPage('reports_view',<Overview/>)}/>
          <Route path="/news" element={protectedPage('news_view',<News owner={!!user.is_platform_owner}/>)}/>
          <Route path="/news/:slug" element={protectedPage('news_view',<News owner={!!user.is_platform_owner}/>)}/>
          <Route path="/photos" element={protectedPage('photos_view',<PhotoList/>)}/>
          <Route path="/photo-map" element={protectedPage('photos_view',<PhotoMap/>)}/>
          <Route path="/review" element={protectedPage('photos_edit',<PhotoList review/>)}/>
          <Route path="/days" element={protectedPage('days_review',<DayReviews/>)}/>
          <Route path="/daily-reports" element={protectedPage('daily_reports_view',<Suspense fallback={<p className="loading">Открываем отчётность…</p>}><DailyReports/></Suspense>)}/>
          <Route path="/spam" element={protectedPage('spam_view',<PhotoList spam/>)}/>
          <Route path="/photos/:id" element={protectedPage('photos_view',<PhotoDetail/>)}/>
          <Route path="/districts" element={protectedPage('districts_view',<DistrictMap/>)}/>
          <Route path="/appeals" element={meta.organization ? <Suspense fallback={<p className="loading">Открываем обращения…</p>}><OwnerAppeals organization/></Suspense> : denied}/>
          <Route path="/inspectors" element={admin && meta.organization ? <Suspense fallback={<p className="loading">Открываем проверяющих…</p>}><InspectionAdministration/></Suspense> : denied}/>
          <Route path="/messages" element={<OperatorChats/>}/>
          <Route path="/chats" element={protectedPage('chats_manage',<Administration key="chats" page="chats"/>)}/>
          <Route path="/work-types" element={admin?<WorkTypes/>:denied}/>
          <Route path="/users" element={admin?<Administration key="users" page="users"/>:denied}/>
          <Route path="/audit" element={protectedPage('audit_view',<Administration key="audit" page="audit"/>)}/>
          <Route path="/jobs" element={protectedPage('jobs_manage',<Administration key="jobs" page="jobs"/>)}/>
          <Route path="/learning" element={protectedPage('learning_manage',<Learning/>)}/>
          <Route path="/exchange" element={protectedPage('exchange_manage',<Exchange/>)}/>
          <Route path="/settings" element={<Navigate to="/settings/profile" replace/>}/>
          <Route path="/settings/:section" element={<Settings/>}/>
          <Route path="/account" element={<Navigate to="/settings" replace/>}/>
          <Route path="*" element={denied}/>
        </Routes>
      </main>
    </div>
    <AppealNotice count={appeals.notice} dismiss={appeals.dismiss}/>
    {toast&&<div className="toast" role="status"><CircleCheck size={20}/>{toast}<button onClick={()=>setToast('')}>×</button></div>}
    </PhotoNotificationsProvider>
    </OperatorChatsProvider>
    </NewsNotificationsProvider>
  </Context.Provider>;
}

export function useFilters(review=false,spam=false,today=false,workOnly=false) {
  const {meta,user}=useApp(); const [params,setParams]=useSearchParams();
  const defaultDate=today?meta.today:shiftDate(meta.today,-1);
  const filters:Filters={...(workOnly?{work_only:'true'}:{}),...(!review&&!spam&&!params.has('date_from')&&!params.has('date_to')&&!params.has('all_dates')?{date_from:defaultDate,date_to:defaultDate}:{}),...Object.fromEntries(params),...(review?{status:'NEEDS_REVIEW'}:spam?{status:'SPAM'}:{})};
  Object.assign(filters,photoSortForUser(filters,user));
  delete filters.all_dates; delete filters.review; delete filters.spam; delete filters.from_map;
  delete filters.page; delete filters.anchor_id;
  const change=(values:Filters)=>{const updated={...filters,...values};if('date_from' in values||'date_to' in values)delete updated.review_day;Object.assign(updated,photoSortForUser(updated,user)); if(!updated.date_from&&!updated.date_to)updated.all_dates='1';setParams(Object.fromEntries(Object.entries(updated).filter(([,v])=>v!=='')));};
  return {filters,change};
}


function Overview() {
  const {user,meta}=useApp(); const canBrowse=hasPermission(user,'photos_view');
  const {filters:selection,change}=useFilters();const filters={...selection,work_only:'true'};const [report,setReport]=useState<Report|null>(null),[error,setError]=useState(''),[revision,setRevision]=useState(0);const navigate=useNavigate();const key=query(filters);
  useEffect(()=>{const c=new AbortController();setError('');setReport(null);api<Report>('/reports?'+key,'GET',undefined,c.signal).then(setReport).catch(e=>{if(e.name!=='AbortError')setError(e.message);});return()=>c.abort();},[key,revision]);
  function drill(values:Filters) {if(!canBrowse)return;navigate(reportPhotoLink(filters,values));}
  const counts=report?.counts||{};
  return <><header className="page-header"><div><span className="eyebrow">ЕЖЕДНЕВНЫЙ ОТЧЁТ</span><h1>Обзор работ</h1><p>Всё, что сделано в городе. В цифрах и фотографиях.</p></div><button className="icon-button" aria-label="Обновить отчёт" onClick={()=>setRevision(x=>x+1)}><RefreshCw size={18}/></button></header><FilterBar filters={filters} change={change} refreshKey={revision}/><ErrorBox error={error}/>{!report&&!error?<p className="loading">Собираем отчёт…</p>:report&&<><ClosedDayUpdates updates={report.day_updates}/><div className="stat-grid">{[['Всего в отчёте',report.total,'',Camera],['Принято системой',report.accepted,'',CircleCheck],['Требуют проверки',counts.NEEDS_REVIEW,'NEEDS_REVIEW',ClipboardCheck],['Отклонено',counts.REJECTED,'REJECTED',ShieldCheck]].map(([label,n,status,Icon],i)=>{const I=Icon as typeof Camera;return <button className={'stat-card stat-'+i} key={label as string} disabled={!canBrowse||!Number(n)} onClick={()=>drill(i===0?{}:i===1?{accepted_only:'true'}:{status:status as string})}><div><span>{label as string}</span><I size={19}/></div><strong>{Number(n).toLocaleString('ru-RU')}</strong><small>{i===0?'Без дублей и пересланных':i===1?'Включая ручную проверку':i===2?'Ожидают решения оператора':'За выбранный период'}</small></button>;})}</div><div className="minor-stats">{[['Дубли','DUPLICATE'],['Пересланные','FORWARDED'],['Ошибки обработки','ERROR'],['В обработке','PROCESSING'],['Ожидают обработки','RECEIVED']].map(([label,code])=><button key={code} disabled={!canBrowse||!counts[code]} onClick={()=>drill({status:code})}>{label}<strong>{counts[code]||0}</strong></button>)}</div><OperatorDayStatus day={dayReviewDate(selection.review_day||selection.date_to||selection.date_from,shiftDate(meta.today,-1))} refreshKey={revision} dateEditable={!selection.date_from||selection.date_from!==selection.date_to}/><WorkDistrictMatrix report={report} meta={meta} filters={filters} canBrowse={canBrowse} open={drill}/>{report.total===0?<Empty>В этой выборке нет фотографий для отчёта. Дубли и пересланные фотографии доступны по отдельным счётчикам.</Empty>:<><div className="two-columns"><section className="panel"><div className="section-title"><h2>По микрорайонам</h2><span>{report.districts.length} территорий</span></div><div className="bar-list">{report.districts.map(d=><button key={d.id} disabled={!canBrowse} onClick={()=>drill({district_id:String(d.id)})}><div><span>{d.name}</span><strong>{d.count}</strong></div><div className="bar"><i style={{width:(d.count/Math.max(...report.districts.map(x=>x.count))*100)+'%'}}/></div></button>)}</div></section><section className="panel"><div className="section-title"><h2>По чатам</h2><MessageSquare size={18}/></div>{report.chats.map(c=><button className="list-row" key={c.id} disabled={!canBrowse} onClick={()=>drill({chat_id:String(c.id)})}><span className="chat-icon"><MessageSquare size={18}/></span><span>{c.name}</span><strong>{c.count}</strong><ChevronRight size={16}/></button>)}</section></div><section className="panel"><div className="section-title"><h2>Чат × микрорайон</h2>{canBrowse&&<span>Нажмите на число, чтобы открыть фотографии</span>}</div><div className="table-scroll"><table><thead><tr><th>Чат</th>{report.districts.map(d=><th key={d.id}>{d.name}</th>)}<th>Всего</th></tr></thead><tbody>{report.chats.map(c=><tr key={c.id}><td>{c.name}</td>{report.districts.map(d=><td key={d.id}><button className="number-link" disabled={!canBrowse} onClick={()=>drill({chat_id:String(c.id),district_id:String(d.id)})}>{report.matrix.find(m=>m.chat_id===c.id&&m.district_id===d.id)?.count||0}</button></td>)}<td><strong>{c.count}</strong></td></tr>)}</tbody></table></div></section><section className="panel"><div className="section-title"><h2>Виды выполненных работ</h2><span>Фотографий</span></div>{report.work_types.map(w=><button className="list-row" key={w.id} disabled={!canBrowse} onClick={()=>drill({work_type:String(w.id)})}><span>{w.name}</span><strong>{w.count}</strong><ChevronRight size={16}/></button>)}</section></>}<p className="footnote">Если дата снимка ещё не распознана, в отчёте используется дата получения. Часовой пояс задан в настройках сервиса. Дубли, пересланные фотографии и спам не включены в отчёты о работах. Дубли и пересланные доступны по отдельным счётчикам.</p></>}</>;
}

function PhotoList({review=false,spam=false}:{review?:boolean;spam?:boolean}) {
  const {meta,user,notify}=useApp();
  const {filters,change}=useFilters(review,spam,false,true);
  const [params,setParams]=useSearchParams();
  const location=useLocation();const navigate=useNavigate();
  const position=(location.state as {photoListPosition?:PhotoListPosition}|null)?.photoListPosition;
  const requestedPage=photoListPage(params.get('page'));
  const anchorId=photoListAnchor(params.get('anchor_id'),position);
  const [data,setData]=useState<PhotoPage|null>(null),[error,setError]=useState(''),[revision,setRevision]=useState(0);
  const key=query({...filters,page:String(requestedPage),...(anchorId?{anchor_id:String(anchorId)}:{})});
  const loadedKey=useRef(''),restoredLocation=useRef('');
  const preserveRows=useRef(false);
  const [defectBusy,setDefectBusy]=useState<number|null>(null);
  const page=data?.page??requestedPage;
  useEffect(()=>{const refresh=()=>setRevision(value=>value+1);window.addEventListener('refresh-photo-list',refresh);return()=>window.removeEventListener('refresh-photo-list',refresh);},[]);
  useEffect(()=>{
    const c=new AbortController();if(!preserveRows.current)setData(null);preserveRows.current=false;setError('');
    api<PhotoPage>('/photos?'+key,'GET',undefined,c.signal).then(value=>{
      if(!c.signal.aborted){loadedKey.current=key;setData(value);}
    }).catch(e=>{if(e.name!=='AbortError')setError(e.message);});
    return()=>c.abort();
  },[key,revision]);
  useLayoutEffect(()=>{
    if(!data||loadedKey.current!==key||restoredLocation.current===location.key)return;
    restoredLocation.current=location.key;
    restorePhotoListPosition(data.focus_id??anchorId,position);
  },[data,key,location.key,anchorId,position]);
  function setPage(value:number){
    const updated=new URLSearchParams(params);updated.set('page',String(value));updated.delete('anchor_id');
    setParams(updated);
  }
  function openPhoto(id:number){
    const state={photoListPosition:capturePhotoListPosition(id)};
    // Save the position in the list's own history entry as well, for browser Back.
    const listParams=new URLSearchParams(params);listParams.set('page',String(page));listParams.set('anchor_id',String(id));
    navigate(location.pathname+'?'+listParams.toString(),{replace:true,state});
    navigate(photoLink(id,{...filters,page:String(page)},review,spam),{state});
    window.scrollTo({top:0,behavior:'instant'});
  }
  async function markDefect(photo:Photo,checked:boolean){
    if(defectBusy!==null||!canEditPhoto(user,photo)||photo.is_spam)return;
    setDefectBusy(photo.id);setError('');
    try{
      const saved=await updatePhotoDefect(photo,checked);
      setData(value=>value?{...value,items:value.items.map(item=>item.id===photo.id?{...item,...saved,operator_name:user.name}:item)}:value);
      preserveRows.current=true;setRevision(value=>value+1);
      notify(checked?'Неисправность отмечена':'Отметка о неисправности снята');
    }catch(e){setError((e as Error).message);throw e;}
    finally{setDefectBusy(null);}
  }
  const archiveTitle=filters.status==='DUPLICATE'?'Дубли':filters.status==='FORWARDED'?'Пересланные фотографии':null;
  return <><header className="page-header"><div><span className="eyebrow">{review?'РУЧНАЯ ПРОВЕРКА':spam?'СЛУЧАЙНЫЕ И НЕПОДХОДЯЩИЕ ФОТО':'АРХИВ ФОТОГРАФИЙ'}</span><h1>{review?'Требуют проверки':spam?'Спам':archiveTitle||'Фотографии'}</h1><p>{review?'Помогите системе там, где нужен взгляд человека.':spam?'Не учитываются в работах и обучении. Для возврата откройте фото и выберите вид работы.':archiveTitle?'Эти фотографии отображаются отдельно и не учитываются в отчётах о работах.':filters.work_only==='true'?'В обычном списке скрыты дубли, пересланные и спам. Для их просмотра выберите нужный статус.':'В этой выборке учитываются дубли и пересланные фотографии. Спам доступен отдельно по соответствующему статусу.'}</p></div><div className="photo-map-actions"><button onClick={()=>navigate('/photo-map?'+query({...filters,...(!filters.date_from&&!filters.date_to?{all_dates:'1'}:{})}))}><MapPin size={18}/>На карте</button><button onClick={()=>setRevision(x=>x+1)} aria-label="Обновить список"><RefreshCw size={18}/></button></div></header><PhotoNumberSearch owner={!!user.is_platform_owner} open={id=>navigate(`/photos/${id}?by_number=1&all_dates=1`)}/><FilterBar filters={filters} change={change} fixedStatus={review||spam} hideWork={spam} refreshKey={revision}/><ErrorBox error={error}/>{data?<section className="panel photo-panel"><div className="section-title"><h2>{review?'Очередь проверки':spam?'Спам':archiveTitle||'Все фотографии'} <span className="count-pill appeal-count">{data.total}</span></h2></div>{data.items.length===0?<Empty>{review?'В этой выборке нет фотографий, требующих проверки.':'Измените фильтры, чтобы найти нужные фотографии.'}</Empty>:<PhotoTable photos={data.items} openPhoto={openPhoto} focusId={data.focus_id??anchorId} defectBusy={defectBusy} onDefect={markDefect}/>}<div className="pagination"><span>Страница {page} из {Math.max(1,Math.ceil(data.total/data.page_size))}</span><button disabled={page<=1} onClick={()=>setPage(page-1)}><ChevronLeft size={16}/>Назад</button><button disabled={page*data.page_size>=data.total} onClick={()=>setPage(page+1)}>Далее<ChevronRight size={16}/></button></div></section>:!error&&<p className="loading">Загружаем фотографии…</p>}</>;
}
