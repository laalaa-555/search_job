import { useEffect, useRef, useState } from 'react';
import { Bookmark, BriefcaseBusiness, Check, ChevronDown, ChevronLeft, ChevronRight, CircleAlert, ExternalLink, LoaderCircle, RefreshCw, Search, X } from 'lucide-react';
import { fetchBookmarks, fetchJobs, fetchSources, setBookmark } from './api';
import { allSources, sourceNames } from './types';
import type { Job, JobPage, SourceReport } from './types';

const cities = ['台北市','新北市','桃園市','台中市','台南市','高雄市','新竹市','新竹縣','基隆市','宜蘭縣','苗栗縣','彰化縣','南投縣','雲林縣','嘉義市','嘉義縣','屏東縣','花蓮縣','台東縣','澎湖縣','金門縣','連江縣','遠端','其他'];
const statusNames: Record<string,string> = {idle:'等待收集',running:'收集中',success:'整輪更新完成',batch_complete:'本次批次完成',partial:'部分更新',error:'更新失敗',disabled:'已停用',interrupted:'收集中斷'};
const reasonNames: Record<string,string> = {rate_limited:'來源限流',access_denied:'來源拒絕存取',verification_required:'驗證受阻',loading_timeout:'載入失敗',format_changed:'網頁結構改版',pagination_repeated:'來源分頁重複'};

function coolingDown(value: string | null) {
  return Boolean(value && new Date(/Z$|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`).getTime() > Date.now());
}

function timeLabel(value: string | null) {
  if (!value) return '尚未成功更新';
  // SQLite serializes UTC datetimes without a suffix.
  const date = new Date(/Z$|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);
  return new Intl.DateTimeFormat('zh-TW', {timeZone:'Asia/Taipei',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(date);
}
function salaryLabel(job: Job) {
  if (job.annual_salary_min !== null) {
    const min = new Intl.NumberFormat('zh-TW', {maximumFractionDigits:1}).format(job.annual_salary_min / 10000);
    const max = job.annual_salary_max ? new Intl.NumberFormat('zh-TW', {maximumFractionDigits:1}).format(job.annual_salary_max / 10000) : null;
    return `${job.salary_estimated ? '估算年薪' : '年薪'} ${min}${max && max !== min ? `–${max}` : ''} 萬`;
  }
  return job.salary_text;
}

export default function App() {
  const [query, setQuery] = useState('');
  const [debounced, setDebounced] = useState('');
  const [salary, setSalary] = useState('');
  const [city, setCity] = useState('');
  const [selected, setSelected] = useState(allSources);
  const [unknown, setUnknown] = useState(true);
  const [view, setView] = useState<'search'|'saved'>('search');
  const [page, setPage] = useState(1);
  const [results, setResults] = useState<JobPage | null>(null);
  const [report, setReport] = useState<SourceReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [statusError, setStatusError] = useState('');
  const [busy, setBusy] = useState<number[]>([]);
  const [revision, setRevision] = useState(0);
  const [showCoverage, setShowCoverage] = useState(false);
  const generation = useRef(0);
  const lastFinished = useRef('');

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(query.trim()), 300);
    return () => clearTimeout(timer);
  }, [query]);
  useEffect(() => {setPage(1);}, [debounced, salary, city, selected, unknown, view]);

  useEffect(() => {
    const controller = new AbortController();
    const id = ++generation.current;
    setLoading(true); setError('');
    let pending: Promise<JobPage>;
    if (view === 'saved') pending = fetchBookmarks(page, controller.signal);
    else if (!selected.length) pending = Promise.resolve({items:[],total:0,page:1,page_size:20});
    else {
      const params = new URLSearchParams({q:debounced,city,page:String(page),include_unknown_salary:String(unknown)});
      if (salary) params.set('min_annual_salary', String(Number(salary) * 10000));
      selected.forEach(source => params.append('sources', source));
      pending = fetchJobs(params, controller.signal);
    }
    pending.then(value => {
      if (id !== generation.current) return;
      const lastPage = Math.max(1, Math.ceil(value.total / value.page_size));
      if (page > lastPage) setPage(lastPage);
      else setResults(value);
    })
      .catch(reason => {if (!controller.signal.aborted && id === generation.current) setError(reason.message);})
      .finally(() => {if (id === generation.current) setLoading(false);});
    return () => controller.abort();
  }, [debounced, salary, city, selected, unknown, view, page, revision]);

  useEffect(() => {
    const controller = new AbortController();
    async function poll() {
      try {
        const value = await fetchSources(controller.signal);
        if (controller.signal.aborted) return;
        setReport(value); setStatusError('');
        const stamp = value.items.map(item => `${item.source}:${item.last_finished_at}:${item.collected_count}`).join('|');
        if (lastFinished.current && lastFinished.current !== stamp) setRevision(number => number + 1);
        lastFinished.current = stamp;
      } catch (reason) {
        if (!controller.signal.aborted) setStatusError((reason as Error).message);
      }
    }
    void poll();
    const timer = setInterval(() => {void poll();}, 5000);
    return () => {controller.abort(); clearInterval(timer);};
  }, []);

  async function toggleSaved(job: Job) {
    if (busy.includes(job.id)) return;
    setBusy(ids => [...ids, job.id]);
    try {
      await setBookmark(job.id, !job.bookmarked);
      if (view === 'saved') setRevision(value => value + 1);
      else setResults(current => current && ({...current,items:current.items.map(item => item.id === job.id ? {...item,bookmarked:!job.bookmarked} : item)}));
    } catch (reason) {setError((reason as Error).message);}
    finally {setBusy(ids => ids.filter(id => id !== job.id));}
  }

  const running = report?.worker_running;
  const hasFailures = report?.items.some(item => ['error','partial','interrupted'].includes(item.status));
  const totalPages = Math.max(1, Math.ceil((results?.total || 0) / 20));
  return <main className="page">
    <section className="finder" aria-label="職缺搜尋">
      <header className="header">
        <div className="brand"><BriefcaseBusiness size={20}/><h1>My Job Finder</h1></div>
        <span className="local-badge"><span/>本機資料庫</span>
      </header>
      <nav className="tabs" aria-label="搜尋與收藏">
        <button className={view === 'search' ? 'tab active' : 'tab'} onClick={() => setView('search')}><Search size={15}/>找職缺</button>
        <button className={view === 'saved' ? 'tab active' : 'tab'} onClick={() => setView('saved')}><Bookmark size={15}/>我的收藏</button>
      </nav>
      {view === 'search' && <div className="filters">
        <div className="search-input"><input aria-label="搜尋關鍵字" placeholder="搜尋職稱、公司或技能，例如 Data Scientist" value={query} onChange={event => setQuery(event.target.value)}/>{query && <button aria-label="清除搜尋" onClick={() => setQuery('')}><X size={17}/></button>}</div>
        <div className="filter-grid">
          <label>最低年薪（萬元）<div className="select-wrap"><select aria-label="最低年薪" value={salary} onChange={event => setSalary(event.target.value)}><option value="">不限年薪</option>{[60,80,100,120,150,180,200,250,300].map(value => <option key={value} value={value}>{value} 萬</option>)}</select><ChevronDown size={17}/></div></label>
          <label>工作地區<div className="select-wrap"><select aria-label="工作地區" value={city} onChange={event => setCity(event.target.value)}><option value="">全部地區</option>{cities.map(value => <option key={value}>{value}</option>)}</select><ChevronDown size={17}/></div></label>
        </div>
        <fieldset className="sources"><legend>資料來源</legend><div className="source-options">{allSources.map(source => <label className="source-choice" key={source}><input type="checkbox" checked={selected.includes(source)} onChange={() => setSelected(values => values.includes(source) ? values.filter(value => value !== source) : [...values,source])}/><span className="check-circle"><Check size={14} strokeWidth={3}/></span>{sourceNames[source]}</label>)}</div></fieldset>
        <label className="unknown-choice"><input type="checkbox" checked={unknown} onChange={event => setUnknown(event.target.checked)}/>同時顯示面議／無法比較年薪的職缺</label>
      </div>}

      <div className="results-heading"><h2>{view === 'saved' ? '我的收藏' : '符合條件'}：<span>{loading && !results ? '…' : results?.total.toLocaleString() || 0}</span> 筆</h2><span className="result-note">搜尋已儲存的真實職缺</span></div>
      {error && <div className="error-banner" role="alert"><CircleAlert size={17}/><span>{error}</span><button onClick={() => setRevision(value => value + 1)}>重試</button></div>}
      {loading && <div className="loading" role="status"><LoaderCircle className="spin" size={16}/>正在查詢資料庫…</div>}
      {!loading && !error && !results?.items.length && <div className="empty"><BriefcaseBusiness size={32}/><h3>{view === 'saved' ? '尚未收藏職缺' : !selected.length ? '請選擇資料來源' : running ? '正在整理第一批職缺' : '目前沒有符合條件的職缺'}</h3><p>{view === 'saved' ? '點選職缺右上角的書籤，保存感興趣的工作。' : running ? 'Worker 正在更新資料，成功取得的職缺會陸續出現。' : '試試調整關鍵字或篩選條件，也可查看下方資料更新狀態。'}</p></div>}
      <div className="job-list" aria-busy={loading}>{results?.items.map(job => <article className="job-card" key={job.id}>
        <button className={`save-button ${job.bookmarked ? 'saved' : ''}`} aria-label={`${job.bookmarked ? '取消收藏' : '收藏'} ${job.title}`} aria-pressed={job.bookmarked} disabled={busy.includes(job.id)} onClick={() => {void toggleSaved(job);}}>{busy.includes(job.id) ? <LoaderCircle className="spin" size={18}/> : <Bookmark size={18} fill={job.bookmarked ? 'currentColor' : 'none'}/>}</button>
        <h3><a href={job.url} target="_blank" rel="noopener noreferrer">{job.title}<ExternalLink size={13}/></a></h3>
        <p className="company">{job.company}<span> · </span>{job.location || '地區未提供'}</p>
        <div className="job-meta"><span className={job.annual_salary_min !== null ? 'salary' : 'salary unknown'} title={job.salary_text}>{salaryLabel(job)}</span><span className="tag source-tag">{sourceNames[job.source]}</span>{job.skills.slice(0,5).map(skill => <span className="tag" key={skill}>{skill}</span>)}</div>
        {job.salary_estimated && <p className="salary-note">依月薪 × 12 換算，不含未明載獎金</p>}
        {job.annual_salary_min === null && !/面議|未提供|negotiable/i.test(job.salary_text) && <p className="salary-note">此薪資未換算為新台幣年薪</p>}
        {job.stale && <p className="stale-note">資料較舊 · 最後收集 {timeLabel(job.last_seen_at)}</p>}
        {job.detail_status === 'pending' && <p className="salary-note">詳情尚未取得</p>}
        {!job.is_active && <p className="stale-note">來源已標示此職缺下架</p>}
      </article>)}</div>
      {totalPages > 1 && <nav className="pagination" aria-label="分頁"><button aria-label="上一頁" disabled={page <= 1 || loading} onClick={() => setPage(value => value - 1)}><ChevronLeft size={17}/></button><span>第 {page} / {totalPages} 頁</span><button aria-label="下一頁" disabled={page >= totalPages || loading} onClick={() => setPage(value => value + 1)}><ChevronRight size={17}/></button></nav>}

      <section className="updates" aria-label="資料更新狀態">
        <div className="updates-heading"><h2><RefreshCw size={14} className={running ? 'spin-slow' : ''}/>資料更新</h2><span>{report ? `每日 ${String(report.schedule_hour).padStart(2,'0')}:${String(report.schedule_minute).padStart(2,'0')} · 台北時間` : '啟動 Worker 後更新'}</span></div>
        {statusError && <p className="status-error" role="alert">{statusError}</p>}
        <div className="status-grid">{report?.items.map(source => <div className={`source-status ${source.status}`} key={source.source}><div><strong>{source.label}</strong><span className="status-label"><i/>{source.status === 'success' && !source.cycle_completed ? '本次批次完成' : statusNames[source.status] || source.status}</span></div><p>{source.status === 'running' ? `${source.collected_count} 筆 · 關鍵字 ${source.keywords_completed}/${source.keywords_total}` : `${source.stored_count} 筆 · 整輪更新 ${timeLabel(source.last_cycle_completed_at)}`}</p>{source.cycle_id && <small>週期 #{source.cycle_id} · 關鍵字 {source.cycle_keywords_completed}/{source.cycle_keywords_total} · 待完成 {source.pending_keywords} · 已保存 {source.cycle_pages_collected} 頁</small>}{source.last_data_at && <small>最後取得資料 {timeLabel(source.last_data_at)}</small>}{source.reason_code && <small>{reasonNames[source.reason_code] || '來源收集異常'}</small>}{coolingDown(source.next_retry_at) && <small>冷卻中 · {timeLabel(source.next_retry_at)} 後可再嘗試</small>}{source.error && <details><summary>查看原因</summary><p>{source.error}</p></details>}{source.keyword_progress?.length > 0 && <details><summary>查看收集進度</summary>{source.keyword_progress.map(item => <p key={item.keyword}>{item.keyword}：{item.collected_count} 筆 · {item.completed ? item.truncated ? '達範圍上限' : '已完成' : `待收集第 ${item.next_page} 頁`}</p>)}</details>}{source.truncated && <small>已達設定範圍上限，非來源全部職缺</small>}</div>)}</div>
        {hasFailures && <p className="update-note">部分來源尚未完成更新，已儲存的職缺仍可搜尋。</p>}
        <button className="coverage-button" aria-expanded={showCoverage} onClick={() => setShowCoverage(value => !value)}>{showCoverage ? '收起' : '查看'}資料涵蓋範圍<ChevronDown size={13}/></button>
        {showCoverage && <div className="coverage"><p>每次啟動 Worker 立即收集，並依每日排程更新。搜尋範圍為已存入資料庫的職缺，每個來源／關鍵字最多 {report?.max_results_per_keyword || 100} 筆。</p><p>LinkedIn、Cake 每次最多收集 {report?.batch_pages_per_run || 6} 頁列表，輪替關鍵字；未完成進度留到下次排程接續，整輪可能需要數日或數週。LinkedIn 整輪列表完成後才補充詳情。</p><p>地區：{report?.location || 'Taiwan'}。關鍵字：{report?.keywords.join('、') || '載入中'}。</p><p>本機關機期間不更新，下次啟動接續；冷卻到期後等下次啟動或每日排程，不另加每小時更新。來源排序會變動，分批資料不代表同一時刻完整的前 100 筆。</p></div>}
      </section>
    </section>
    <footer className="footer">把職缺整理好，把時間留給下一步。</footer>
  </main>;
}
