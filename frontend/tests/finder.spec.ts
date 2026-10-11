import { expect, test, type Page } from '@playwright/test';

const makeJob = (id:number, title:string, source:string, city:string, salary:number|null) => ({id,title,source,company:'測試公司',location:city,location_city:city,url:`https://www.${source === '104' ? '104.com.tw/job' : '1111.com.tw/job'}/${id}`,skills:['Python','SQL'],salary_text:salary ? '年薪 '+salary : '薪資面議',salary_currency:'TWD',salary_period:salary ? 'year' : null,salary_min:salary,salary_max:null,annual_salary_min:salary,annual_salary_max:null,salary_estimated:false,is_active:true,bookmarked:false,stale:false,last_seen_at:'2026-10-11T00:00:00Z',updated_at:'2026-10-11T00:00:00Z'});

async function mockedApi(page:Page, empty=false, collecting=false) {
  const rows = empty ? [] : [makeJob(1,'Data Scientist','104','台北市',1350000),makeJob(2,'資料科學工程師','1111','台中市',1250000),makeJob(3,'面議職缺','104','台北市',null)];
  const saved = new Set<number>();
  await page.route('**/api/v1/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (/\/bookmarks\/\d+$/.test(path)) {
      const id = Number(path.split('/').pop());
      if (route.request().method() === 'PUT') saved.add(id); else saved.delete(id);
      await route.fulfill({status:204}); return;
    }
    if (path.endsWith('/sources')) {
      await route.fulfill({json:{items:[{source:'104',label:'104',enabled:true,status:collecting ? 'running' : 'error',last_success_at:null,last_finished_at:'2026-10-11T00:00:00Z',collected_count:0,stored_count:rows.length,keywords_completed:0,keywords_total:1,error:collecting ? null : 'HTTP 403',truncated:false}],keywords:['Data Scientist'],location:'Taiwan',max_results_per_keyword:100,schedule_timezone:'Asia/Taipei',schedule_hour:6,schedule_minute:0,worker_running:collecting}}); return;
    }
    let items = rows.map(row => ({...row,bookmarked:saved.has(row.id)}));
    if (path.endsWith('/bookmarks')) items = items.filter(row => saved.has(row.id));
    else {
      const q = url.searchParams.get('q')?.toLowerCase() || '';
      const city = url.searchParams.get('city');
      const sources = url.searchParams.getAll('sources');
      const min = Number(url.searchParams.get('min_annual_salary') || 0);
      items = items.filter(row => row.title.toLowerCase().includes(q) && (!city || row.location_city === city) && (!sources.length || sources.includes(row.source)) && (row.annual_salary_min === null ? url.searchParams.get('include_unknown_salary') !== 'false' : row.annual_salary_min >= min));
    }
    await route.fulfill({json:{items,total:items.length,page:1,page_size:20}});
  });
}

test('search, salary, city and source filters', async ({page}) => {
  await mockedApi(page); await page.goto('/');
  await expect(page.locator('.job-card')).toHaveCount(3);
  await page.getByLabel('搜尋關鍵字').fill('Data Scientist');
  await expect(page.locator('.job-card')).toHaveCount(1);
  await page.getByLabel('清除搜尋').click();
  await page.getByLabel('最低年薪',{exact:true}).selectOption('120');
  await expect(page.locator('.job-card')).toHaveCount(3);
  await page.getByLabel('同時顯示面議／無法比較年薪的職缺').uncheck();
  await expect(page.locator('.job-card')).toHaveCount(2);
  await page.getByLabel('工作地區',{exact:true}).selectOption('台中市');
  await expect(page.locator('.job-card')).toHaveCount(1);
  await page.locator('.source-choice').filter({hasText:'1111'}).locator('input').uncheck();
  await expect(page.locator('.job-card')).toHaveCount(0);
});

test('bookmarks are saved and can be removed from saved view', async ({page}) => {
  await mockedApi(page); await page.goto('/');
  await page.getByRole('button',{name:'收藏 Data Scientist',exact:true}).click();
  await expect(page.getByRole('button',{name:'取消收藏 Data Scientist',exact:true})).toHaveAttribute('aria-pressed','true');
  await page.getByRole('button',{name:'我的收藏',exact:true}).click();
  await expect(page.locator('.job-card')).toHaveCount(1);
  await page.getByRole('button',{name:'取消收藏 Data Scientist',exact:true}).click();
  await expect(page.getByRole('heading',{name:'尚未收藏職缺'})).toBeVisible();
});

test('source failures leave stored results usable and show reason', async ({page}) => {
  await mockedApi(page); await page.goto('/');
  await expect(page.locator('.job-card')).toHaveCount(3);
  await expect(page.getByText('更新失敗',{exact:true})).toBeVisible();
  await page.getByText('查看原因',{exact:true}).click();
  await expect(page.getByText('HTTP 403',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'查看資料涵蓋範圍'}).click();
  await expect(page.locator('.coverage')).toContainText('Data Scientist');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
});

test('empty database and API failure have clear messages', async ({page}) => {
  await mockedApi(page,true); await page.goto('/');
  await expect(page.getByRole('heading',{name:'目前沒有符合條件的職缺'})).toBeVisible();
  await page.route('**/api/v1/jobs?**', route => route.fulfill({status:503,json:{detail:'not ready'}}));
  await page.getByLabel('搜尋關鍵字').fill('Python');
  await expect(page.getByRole('alert').filter({hasText:'資料庫尚未就緒'})).toBeVisible();
});

test('an older delayed search cannot replace the newer result', async ({page}) => {
  await mockedApi(page);
  let started!: () => void;
  const slowStarted = new Promise<void>(resolve => {started=resolve;});
  await page.route('**/api/v1/jobs?**', async route => {
    const q = new URL(route.request().url()).searchParams.get('q');
    if (q === 'slow') {started(); await new Promise(resolve=>setTimeout(resolve,1200));}
    const items = q ? [makeJob(q === 'slow' ? 8 : 9,q === 'slow' ? '舊搜尋結果' : '最新搜尋結果','104','台北市',1200000)] : [];
    await route.fulfill({json:{items,total:items.length,page:1,page_size:20}}).catch(()=>{});
  });
  await page.goto('/');
  await page.getByLabel('搜尋關鍵字').fill('slow'); await slowStarted;
  await page.getByLabel('搜尋關鍵字').fill('fast');
  await expect(page.getByRole('link',{name:'最新搜尋結果'})).toBeVisible();
  await page.waitForTimeout(1300);
  await expect(page.getByRole('link',{name:'舊搜尋結果'})).toHaveCount(0);
});

test('first collection shows progress in an empty database', async ({page}) => {
  await mockedApi(page,true,true); await page.goto('/');
  await expect(page.getByRole('heading',{name:'正在整理第一批職缺'})).toBeVisible();
  await expect(page.getByText('收集中',{exact:true})).toBeVisible();
  await expect(page.getByText('0 筆 · 關鍵字 0/1')).toBeVisible();
});

test('batch progress and full cycle completion are distinct', async ({page}) => {
  await mockedApi(page);
  let complete = false;
  await page.route('**/api/v1/sources', route => route.fulfill({json:{items:[{
    source:'linkedin',label:'LinkedIn',enabled:true,status:complete ? 'success' : 'batch_complete',
    stored_count:50,collected_count:10,keywords_completed:complete ? 12 : 0,keywords_total:12,
    cycle_id:3,cycle_completed:complete,cycle_keywords_completed:complete ? 12 : 0,cycle_keywords_total:12,
    pending_keywords:complete ? 0 : 12,cycle_pages_collected:6,
    last_cycle_completed_at:complete ? '2026-10-11T00:00:00Z' : null,
    last_success_at:'2026-10-10T00:00:00Z',last_finished_at:'2026-10-11T00:00:00Z',
    keyword_progress:[{keyword:'Data Scientist',next_page:2,next_offset:10,collected_count:10,completed:false,truncated:false}],
    reason_code:null,next_retry_at:null,error:null,truncated:false}],
    keywords:['Data Scientist'],location:'Taiwan',max_results_per_keyword:100,batch_pages_per_run:6,
    schedule_timezone:'Asia/Taipei',schedule_hour:6,schedule_minute:0,worker_running:false}}));
  await page.goto('/');
  await expect(page.getByText('本次批次完成',{exact:true})).toBeVisible();
  await expect(page.getByText(/待完成 12/)).toBeVisible();
  await expect(page.getByText(/整輪更新 尚未成功更新/)).toBeVisible();
  await page.getByText('查看收集進度',{exact:true}).click();
  await expect(page.getByText(/待收集第 2 頁/)).toBeVisible();
  complete = true;
  await page.reload();
  await expect(page.getByText('整輪更新完成',{exact:true})).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
});

test('removing the last bookmark on page two returns to page one', async ({page}) => {
  await mockedApi(page);
  let count = 21;
  await page.route('**/api/v1/bookmarks**', async route => {
    if (route.request().method() === 'DELETE') {count=20; await route.fulfill({status:204}); return;}
    const pageNumber = Number(new URL(route.request().url()).searchParams.get('page') || 1);
    const items = Array.from({length:count}, (_,i)=>({...makeJob(i+1,`收藏職缺 ${i+1}`,'104','台北市',1200000),bookmarked:true})).slice((pageNumber-1)*20,pageNumber*20);
    await route.fulfill({json:{items,total:count,page:pageNumber,page_size:20}});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'我的收藏',exact:true}).click();
  await expect(page.locator('.job-card')).toHaveCount(20);
  await page.getByLabel('下一頁',{exact:true}).click();
  await expect(page.locator('.job-card')).toHaveCount(1);
  await page.getByRole('button',{name:'取消收藏 收藏職缺 21',exact:true}).click();
  await expect(page.locator('.job-card')).toHaveCount(20);
  await expect(page.locator('.pagination')).toHaveCount(0);
});

test('cooldown and pending detail are visible while stored jobs remain searchable', async ({page}) => {
  await mockedApi(page);
  await page.route('**/api/v1/sources', route => route.fulfill({json:{items:[{source:'linkedin',label:'LinkedIn',enabled:true,status:'partial',reason_code:'rate_limited',next_retry_at:'2099-10-11T00:00:00Z',last_data_at:'2026-10-11T00:00:00Z',last_success_at:null,last_finished_at:'2026-10-11T00:00:00Z',collected_count:1,stored_count:1,keywords_completed:1,keywords_total:1,error:'HTTP 429',truncated:false}],keywords:['Data Scientist'],location:'Taiwan',max_results_per_keyword:100,schedule_timezone:'Asia/Taipei',schedule_hour:6,schedule_minute:0,worker_running:false}}));
  await page.route('**/api/v1/jobs?**', route => route.fulfill({json:{items:[{...makeJob(1,'Data Scientist','linkedin','台北市',null),url:'https://www.linkedin.com/jobs/view/1',detail_status:'pending',detail_collected_at:null}],total:1,page:1,page_size:20}}));
  await page.goto('/');
  await expect(page.getByText('詳情尚未取得',{exact:true})).toBeVisible();
  await expect(page.getByText('來源限流',{exact:true})).toBeVisible();
  await expect(page.getByText(/冷卻中 ·/)).toBeVisible();
  await expect(page.getByText(/最後取得資料/)).toBeVisible();
  await expect(page.locator('.job-card')).toHaveCount(1);
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
});
