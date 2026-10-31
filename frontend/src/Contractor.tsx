import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { api } from './api';
import { ErrorBox, useApp } from './App';
import './contractor.css';

type Field = {key:string; type:string; required:boolean; readonly:boolean; catalogue:string|null};
type Catalogue = {key:string; name:string; fields:Field[]};
type RecordData = {id:string; name:string; status:string; version:number; [key:string]:unknown};
type Detail = {record:RecordData; links:{kind:string; name:string; items:RecordData[]}[]; history:RecordData[]};
const labels:Record<string,string> = {id:'Постоянный ID',name:'Название / рабочее имя',status:'Состояние',source:'Источник',details:'Реквизиты и дополнительные сведения',organization_id:'Organization MAX',inn:'ИНН',legal_form:'Форма лица',number:'Номер договора',contract_id:'Договор',revision:'Номер редакции',signed_on:'Дата подписания',effective_from:'Действует с',effective_to:'Действует до',basis:'Документ-основание',party_snapshots:'Снимки сторон и договорные роли',code:'Код работы',parent_id:'Родительская работа',unit_id:'Единица измерения',photo_work_type_id:'Сопоставление с фототегом MAX',area_id:'Территория АРМ',territory_id:'Договорная территория',replaced_by_id:'Заменена клеткой',cell_id:'Клетка АРМ',resource_id:'Wialon resource_id',zone_id:'Wialon zone_id',district_id:'Геозона MAX',plate:'Государственный номер',vehicle_type:'Тип техники',model:'Марка / модель',calibration_m3:'Тарировка, м³',calibration_on:'Дата подтверждения тарировки',vehicle_id:'Машина',counterparty_id:'Контрагент',role:'Роль связи',valid_from:'Начало действия',valid_to:'Окончание действия',evidence_id:'Подтверждающий документ',object_id:'Договорный объект DO',site_id:'Площадка PL',sha256:'SHA-256',location:'Расположение оригинала',revision_id:'Редакция договора',kind:'Тип приложения',document_id:'Документ',work_id:'Договорная работа',stage:'Этап',period_from:'Начало периода',period_to:'Конец периода',source_row:'Исходная строка приложения'};
Object.assign(labels,{indicator:'Физический показатель',quantity_text:'Объём по источнику',rate_text:'Цена единицы по источнику',total_text:'Сумма по источнику'});
Object.assign(labels,{legal_profile_id:'Юридический профиль нашей организации',snapshot:'Юридический снимок стороны'});
const statuses:Record<string,string> = {REVIEW:'Требует проверки',INCOMPLETE:'Требует заполнения',CONFIRMED:'Подтверждено',INACTIVE:'Неактивно'};

function StructuredValue({value}:{value:unknown}) {
  if(value===null||value===undefined||value==='')return <>—</>;
  if(Array.isArray(value))return <ol>{value.map((v,i)=><li key={i}><StructuredValue value={v}/></li>)}</ol>;
  if(typeof value==='object')return <dl className="nsi-properties">{Object.entries(value).map(([k,v])=><div key={k}><dt>{labels[k]??k}</dt><dd><StructuredValue value={v}/></dd></div>)}</dl>;
  return <>{String(value)}</>;
}

function DetailsEditor({value,onChange,legal,section}:{value:string;onChange:(value:string)=>void;legal:boolean;section:string}){
  let data:Record<string,unknown>={};try{data=JSON.parse(value||'{}');}catch{return <p>Не удалось прочитать дополнительные сведения.</p>;}
  const fields=legal?['Организационно-правовая форма','Полное наименование юридического лица','Краткое наименование','ИНН','КПП (для юрлица, если есть)','ОГРН / ОГРНИП','Юридический / регистрационный адрес','Почтовый адрес','Телефон','Электронная почта','ФИО подписанта','Должность подписанта','Основание полномочий','Наименование банка','БИК','Расчётный счёт','Корреспондентский счёт']:[];
  const listKey=section==='bank'?'Банковские счета':'Представители';
  const listFields=section==='bank'?['Банк','БИК','Расчётный счёт','Корреспондентский счёт']:['ФИО','Должность','Основание полномочий','Телефон','Электронная почта'];
  const list=Array.isArray(data[listKey])?data[listKey] as Record<string,string>[]:[];
  return <div className="nsi-properties">{[...new Set([...fields,...Object.keys(data)])].filter(k=>!['Банковские счета','Представители'].includes(k)&&(!legal||legalSection(k)===section)).map(k=><label key={k}>{labels[k]??k}{typeof data[k]==='object'&&data[k]!==null?<StructuredValue value={data[k]}/>:<input value={String(data[k]??'')} onChange={e=>onChange(JSON.stringify({...data,[k]:e.target.value}))}/>}</label>)}
    {legal&&['bank','contacts'].includes(section)&&<section><h4>{listKey}</h4>{list.map((row,index)=><fieldset key={index}><legend>{index+1}</legend>{listFields.map(key=><label key={key}>{key}<input value={row[key]??''} onChange={e=>onChange(JSON.stringify({...data,[listKey]:list.map((r,i)=>i===index?{...r,[key]:e.target.value}:r)}))}/></label>)}</fieldset>)}<button type="button" onClick={()=>onChange(JSON.stringify({...data,[listKey]:[...list,{}]}))}>Добавить {section==='bank'?'счёт':'представителя'}</button></section>}
  </div>;
}

function legalSection(key:string){
  if(key==='Представители')return 'contacts';
  if(/банк|бик|счёт|счет/i.test(key))return 'bank';
  if(/телефон|почта|подписант|должность|полномочи|контакт/i.test(key))return 'contacts';
  return 'requisites';
}

function PartiesEditor({value,onChange}:{value:string;onChange:(value:string)=>void}){
  let data:Record<string,string>[]=[];try{data=JSON.parse(value||'[]');}catch{return <p>Не удалось прочитать стороны.</p>;}
  const save=(next:Record<string,string>[])=>onChange(JSON.stringify(next));
  return <div>{data.map((party,i)=><fieldset key={i}><legend>Сторона {i+1}</legend>{['name','role','inn','address','signer','authority'].map(k=><label key={k}>{({name:'Наименование',role:'Договорная роль',inn:'ИНН',address:'Адрес',signer:'Подписант',authority:'Основание полномочий'} as Record<string,string>)[k]}<input value={party[k]??''} onChange={e=>save(data.map((p,j)=>i===j?{...p,[k]:e.target.value}:p))}/></label>)}</fieldset>)}<button type="button" onClick={()=>save([...data,{name:'',role:''}])}>Добавить сторону</button></div>;
}

export default function Contractor() {
  const {user} = useApp(), admin = user.role === 'ADMIN_OPERATOR';
  const {kind='vehicles',id} = useParams(), navigate = useNavigate();
  const [catalogues,setCatalogues] = useState<Catalogue[]>([]), [rows,setRows] = useState<RecordData[]>([]), [total,setTotal] = useState(0);
  const [detail,setDetail] = useState<Detail|null>(null), [values,setValues] = useState<Record<string,string>>({});
  const [q,setQ] = useState(''), [status,setStatus] = useState(''), [offset,setOffset] = useState(0), [error,setError] = useState('');
  const [editing,setEditing] = useState(false), [busy,setBusy] = useState(false), [loaded,setLoaded] = useState(false);
  const [choices,setChoices] = useState<Record<string,RecordData[]>>({});
  const [section,setSection]=useState('requisites');
  const legal=['organization','counterparties'].includes(kind);
  const catalogue = catalogues.find(c=>c.key===kind);
  useEffect(()=>{api<Catalogue[]>('/contractor/catalogues').then(setCatalogues).catch(e=>setError(e.message));},[]);
  useEffect(()=>{setOffset(0);setEditing(false);setError('');setSection('requisites');},[kind,id,q,status]);
  useEffect(()=>{
    let active=true;setLoaded(false);setDetail(null);setRows([]);setError('');
    const request=id && id!=='new' ? api<Detail>(`/contractor/${kind}/${encodeURIComponent(id)}`).then(d=>{if(active){setDetail(d);setValues(Object.fromEntries(Object.entries(d.record).map(([k,v])=>[k,v===null?'':typeof v==='object'?JSON.stringify(v,null,2):String(v)])));}}) : id==='new' ? Promise.resolve().then(()=>{if(active){setValues({status:'REVIEW'});setEditing(true);}}) : api<{items:RecordData[];total:number}>(`/contractor/${kind}?${new URLSearchParams({q,status,offset:String(offset)})}`).then(d=>{if(active){setRows(d.items);setTotal(d.total);}});
    request.catch(e=>{if(active)setError(e.message);}).finally(()=>{if(active)setLoaded(true);});
    return()=>{active=false;};
  },[kind,id,q,status,offset]);
  useEffect(()=>{
    if(!editing||!catalogue)return;
    let active=true;
    const targets=[...new Set(catalogue.fields.map(f=>f.catalogue).filter((v):v is string=>!!v))];
    Promise.all(targets.map(async key=>{
      const all:RecordData[]=[];
      for(let start=0;;start+=500){const page=await api<{items:RecordData[];total:number}>(`/contractor/${key}?limit=500&offset=${start}`);all.push(...page.items);if(all.length>=page.total)break;}
      return [key,all] as const;
    })).then(data=>{if(active)setChoices(Object.fromEntries(data));}).catch(e=>{if(active)setError(e.message);});
    return()=>{active=false;};
  },[editing,catalogue]);
  async function save(){
    if(!catalogue)return;setBusy(true);setError('');
    try {
      const payload:Record<string,unknown>={};
      for(const field of catalogue.fields){
        if(field.readonly || (id!=='new' && field.key==='id'))continue;
        const value=values[field.key]??'';
        if(!value){if(id==='new')continue;payload[field.key]=field.type==='json'?{}:null;}
        else payload[field.key]=field.type==='json'?JSON.parse(value):field.type==='number'?Number(value):value;
      }
      const result=await api<RecordData>(`/contractor/${kind}${id==='new'?'':'/'+encodeURIComponent(id!)}`,id==='new'?'POST':'PATCH',{version:detail?.record.version??0,values:payload});
      setEditing(false);setDetail(await api<Detail>(`/contractor/${kind}/${encodeURIComponent(result.id)}`));navigate(`/contractor/${kind}/${encodeURIComponent(result.id)}`);
    }catch(e){setError(e instanceof Error?e.message:'Не удалось сохранить');}finally{setBusy(false);}
  }
  async function approve(){setBusy(true);try{await api(`/contractor/vehicle-relations/${encodeURIComponent(id!)}/approve`,'POST');setDetail(await api<Detail>(`/contractor/${kind}/${encodeURIComponent(id!)}`));}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
  return <section className="contractor"><h1>НСИ подрядчика</h1>
    <nav aria-label="Справочники НСИ" className="nsi-nav">{catalogues.map(c=><Link key={c.key} className={kind===c.key?'selected':''} to={`/contractor/${c.key}`}>{c.name}</Link>)}</nav>
    <ErrorBox error={error}/><h2>{catalogue?.name}</h2>
    {!loaded?<p role="status">Загрузка…</p>:!id?<>
      <div className="nsi-toolbar"><input aria-label="Поиск по имени или ID" placeholder="Поиск по имени или ID" value={q} onChange={e=>setQ(e.target.value)}/><select aria-label="Состояние" value={status} onChange={e=>setStatus(e.target.value)}><option value="">Все состояния</option>{Object.entries(statuses).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>{admin&&<Link className="button" to={`/contractor/${kind}/new`}>Создать</Link>}</div>
      <p>Найдено: {total}</p><table><thead><tr><th>Название</th><th>Постоянный ID</th><th>Состояние</th></tr></thead><tbody>{rows.map(row=><tr key={row.id}><td><Link to={`/contractor/${kind}/${encodeURIComponent(row.id)}`}>{row.name}</Link></td><td>{row.id}</td><td>{statuses[row.status]??row.status}</td></tr>)}</tbody></table>{!rows.length&&<p>Записей пока нет.</p>}
      <div className="nsi-toolbar"><button disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-100))}>Назад</button><button disabled={offset+100>=total} onClick={()=>setOffset(offset+100)}>Далее</button></div>
    </>:<>
      <Link to={`/contractor/${kind}`}>← К списку</Link>
      {detail&&<><h3>{detail.record.name}</h3><p>{statuses[detail.record.status]} · версия {detail.record.version}</p>{kind==='documents'&&<a className="button" target="_blank" rel="noreferrer" href={/^https:\/\/(drive|docs)\.google\.com\//.test(String(detail.record.location))?String(detail.record.location):`${location.pathname.startsWith('/org/')?'/org/'+location.pathname.split('/')[2]:''}/api/contractor/documents/${encodeURIComponent(detail.record.id)}/content`}>Открыть оригинал для сверки</a>}</>}
      {legal&&<div role="tablist" className="nsi-toolbar">{[['requisites','Реквизиты'],['contacts','Контакты и полномочия'],['bank','Банковские счета'],['documents','Документы'],...(kind==='counterparties'?[['vehicles','Техника']]:[])].map(([key,label])=><button role="tab" aria-selected={section===key} type="button" key={key} onClick={()=>setSection(key)}>{label}</button>)}</div>}
      {admin&&!editing&&detail&&<button onClick={()=>setEditing(true)}>Редактировать</button>}
      {admin&&kind==='vehicle-relations'&&detail&&['CANDIDATE','OPERATIONAL'].includes(String(detail.record.role))&&detail.record.status!=='CONFIRMED'&&<button disabled={busy} onClick={approve}>Утвердить операционного владельца с сегодняшней даты</button>}
      <form className="nsi-card" onSubmit={e=>{e.preventDefault();void save();}}>
        {catalogue?.fields.filter(f=>!f.readonly&&(!legal||(f.key==='details'&&['requisites','contacts','bank'].includes(section))||section==='requisites')).map(field=>{
          const value=values[field.key]??''; const readonly=!editing || (field.key==='id'&&id!=='new');
          return <div className="nsi-field" key={field.key}><span>{labels[field.key]??field.key}{field.required?' *':''}</span>
            {readonly?<div className="nsi-value">{field.catalogue&&value?<Link to={`/contractor/${field.catalogue}/${encodeURIComponent(value)}`}>{value}</Link>:field.key==='status'?statuses[value]??value:field.type==='json'?<StructuredValue value={legal&&field.key==='details'?Object.fromEntries(Object.entries((detail?.record.details??{}) as Record<string,unknown>).filter(([k])=>legalSection(k)===section)):detail?.record[field.key]}/>:value||'—'}</div>:field.key==='status'?<select aria-label={labels[field.key]} value={value} onChange={e=>setValues({...values,[field.key]:e.target.value})}>{Object.entries(statuses).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>:field.catalogue?<select aria-label={labels[field.key]} value={value} onChange={e=>setValues({...values,[field.key]:e.target.value})}><option value="">Не задано</option>{choices[field.catalogue]?.map(r=><option key={r.id} value={r.id}>{r.name} · {r.id}</option>)}</select>:field.key==='details'?<DetailsEditor value={value} legal={legal} section={section} onChange={v=>setValues({...values,[field.key]:v})}/>:field.key==='party_snapshots'?<PartiesEditor value={value} onChange={v=>setValues({...values,[field.key]:v})}/>:field.type==='json'?<StructuredValue value={detail?.record[field.key]}/>:<input aria-label={labels[field.key]??field.key} type={field.type==='date'?'date':field.type==='number'?'number':'text'} step="any" value={value} required={field.required&&field.key!=='id'} onChange={e=>setValues({...values,[field.key]:e.target.value})}/>}
          </div>;
        })}
        {editing&&admin&&<div className="nsi-toolbar"><button disabled={busy} type="submit">{busy?'Сохранение…':'Сохранить'}</button><button type="button" onClick={()=>{if(id==='new')navigate(`/contractor/${kind}`);else{setEditing(false);setValues(Object.fromEntries(Object.entries(detail?.record??{}).map(([k,v])=>[k,v===null?'':typeof v==='object'?JSON.stringify(v,null,2):String(v)])));}}}>Отмена</button></div>}
      </form>
      {detail?.links.filter(group=>!legal||(section==='documents'&&group.kind==='documents')||(section==='vehicles'&&group.kind==='vehicle-relations')||(section==='requisites'&&!['documents','vehicle-relations'].includes(group.kind))).map(group=><section key={group.kind}><h3>{group.name}</h3><ul>{group.items.map(row=><li key={row.id}><Link to={`/contractor/${group.kind}/${encodeURIComponent(row.id)}`}>{row.name}</Link> — {statuses[row.status]??row.status}</li>)}</ul></section>)}
      {!!detail?.history.length&&<details><summary>История изменений ({detail.history.length})</summary>{detail.history.map(h=><p key={String(h.id)}>{String(h.created_at)} · пользователь {String(h.actor_id??'импорт')} · версия {String((h.after as RecordData)?.version)}</p>)}</details>}
    </>}
  </section>;
}
