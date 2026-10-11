# AI Agent 開發規則

先讀 README.md 與 Architecture.md，再確認 Git 狀態與本機 .env 設定。

1. 搜尋 API 僅讀資料庫。外部網站請求只能在 Worker / ingestion 中執行。
2. 五來源各自隔離；來源失敗保留資料，禁止以測試資料偽裝真實職缺。
3. 每次 Worker 啟動先收集，再依 Asia/Taipei 每日排程。使用資料庫租約避免兩個 Worker 同時收集。
4. 保留原始薪資；月薪 × 12 標明估算，外幣與時薪不換算。未知薪資預設可見。
5. 新模型需同步 Alembic migration，並測試 SQLite、PostgreSQL。
6. 所有重要參數經 .env 管理；禁止提交 .env、資料庫、瀏覽器狀態或憑證。
7. 修改後執行 backend 測試與 frontend build；整合變更執行 Compose 與瀏覽器驗證。
8. 在 modify.md 記錄改動及驗證。實際犯錯時，將原因、修正及預防寫入 lesson.md。
9. 提交前檢查 staged diff；使用簡單中文 commit。未獲使用者要求不可刪除持久化 volume。

## 標準驗證

- `backend` 目錄：`../.venv/Scripts/python.exe -m pytest -q`。
- `frontend` 目錄：`npm run build`；Compose 啟動後 `npm run test:e2e`。首次需依 README 安裝 Node Playwright 瀏覽器。
- 根目錄：`docker compose exec -T api python test_postgres.py`、`docker compose exec -T api python -m alembic check`。
- 真實容器重啟驗證：`.venv/Scripts/python.exe scripts/verify_local.py`。會暫停 Worker、測試並恢復收藏；完成後 `docker compose up -d` 恢復 Worker。
- 測試僅使用暫存 SQLite 或 `test_*` PostgreSQL。租約 Session factory 必須可替換，禁止讓測試接觸正式資料庫。

## Git 與交付

遠端為 `https://github.com/laalaa-555/search_job.git`，主分支 `main`。提交前執行 `git diff --cached --check`，確認忽略目錄及實際密碼未加入暫存。
使用簡短中文 commit，推送後核對遠端 SHA。禁止 force push、未備份的破壞性操作及提交測試回應為正式職缺。
五站真實接入未完成的項目必須在 modify.md 明確記錄，不能只以連接器或 fixture 測試通過宣告完成。
