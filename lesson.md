# 錯誤與修正紀錄

## 2026-10-11：Cake 搜尋參數未生效

- 原因：將 `query=` 當成搜尋參數，實際回傳一般列表，可能混入無關職缺。
- 修正：使用實測有效的 `/jobs/{keyword}` 路徑，僅解析指定分頁的 pageMap ID。
- 預防：資料連接器需驗證關鍵字及分頁，不以 HTTP 200 或 HTML 存在職缺視為成功。

## 2026-10-11：前端樣式遺留空白 import

- 原因：CSS 遺留空白 `@import`，且通用錯誤樣式與來源錯誤狀態共用類名。
- 修正：移除無效 import，錯誤通知使用独立 error-banner 類名。
- 預防：檢查 build 警告，瀏覽器驗證來源失敗時的實際版面。

## 2026-10-11：Windows 終端編碼造成診斷腳本中斷

- 原因：PowerShell 下 Python 預設使用 cp950；Cake 的真實資料含無法編碼的 Unicode 字元。
- 修正：診斷腳本明確將 stdout 設成 UTF-8；容器與開發啟動統一使用 UTF-8。
- 預防：跨平台輸出需明確指定編碼，不能假設來源職缺文字都符合本機代碼頁。

## 2026-10-11：Docker Engine 沙箱存取限制

- 原因：Docker Desktop 啟動後，沙箱對 Docker named pipe 的存取被拒絕。
- 修正：Compose 整合命令使用正式升級權限流程，不以其他工具繞過。
- 預防：區分引擎未啟動與權限不足，依實際錯誤處理。

## 2026-10-11：前後端 Playwright 瀏覽器版本不同

- 錯誤：只安裝 Python Playwright 的 Chromium，前端 E2E 找不到需要的執行檔。
- 原因：Python 與 Node.js Playwright 使用不同瀏覽器 revision，不能共用單一版本的執行檔。
- 修正：使用前端 `npx playwright install chromium` 安裝相符版本，兩者保存在忽略的 `browser-data` 目錄。
- 預防：README 分別記錄 Worker 與 E2E 的安裝命令；lockfile 固定依賴版本。

## 2026-10-11：容器診斷關鍵字漏掉空格

- 原因：將 `Data Scientist` 誤寫成 `DataScientist`，Cake 回傳 404，不能用於原關鍵字驗收。
- 修正：PowerShell 使用 `-e 'COLLECT_KEYWORDS=Data Scientist'` 保留完整參數，重新實測成功取得 5 筆職缺。
- 預防：來源驗證需核對實際搜尋參數，不能為了簡化命令改動關鍵字。

## 2026-10-11：Cake 混合來源分頁提早結束

- 原因：排除其他網站轉載後的空列表，被當成搜尋結束；實際 total_pages 顯示後面仍有頁面。
- 修正：以 total_pages 判斷結束，跳過只有其他来源轉載的頁面，保留結果筆數與分頁上限。
- 預防：新增混合來源頁面接續到真實 Cake 職缺的回歸測試，並核對真實第 1／2 頁的 pageMap。

## 2026-10-11：Worker 租約預設參數綁定原 Session factory

- 原因：函式預設參數直接設成 SessionLocal，定義時即綁定；測試替換模組中的 factory 後，租約仍可能使用原連線。
- 修正：預設設為 None，在建立 Lease 時決定 Session factory，與其他 Worker DB 操作使用同一個可替換 factory。
- 預防：CI 與 PostgreSQL 測試將預設應用 DB 設為空白記憶體，測試資料只寫入 fixture 的独立 DB；重新驗證兩種資料庫 19 項均通過。

## 2026-10-11：Docker 忽略規則未包含巢狀資料目錄

- 原因：只排除根目錄 data，backend/data 的本機 SQLite 檔可能被 COPY 進映像。
- 修正：加入 `**/data` 與巢狀測試產物排除，重新建置並確認 `/app/data/jobs.db` 不存在。
- 預防：Git 與 Docker build context 的忽略規則需分別驗證，不以 Git 已忽略作為映像未包含的證據。
