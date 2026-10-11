# AI Agent 開發規則

先讀 README.md 與 Architecture.md，再確認 Git 狀態與本機 .env 設定。

1. 搜尋 API 僅讀資料庫。外部網站請求只能在 Worker / ingestion 中執行。
2. 五來源各自隔離；來源失敗保留資料，禁止以測試資料偽裝真實職缺。
3. 每次 Worker 啟動先收集，再依 Asia/Taipei 每日排程。使用資料庫租約避免兩個 Worker 同時收集。
4. 保留原始薪資；月薪 × 12 標明估算，外幣與時薪不換算。未知薪資預設可見。
5. 新模型需同步 Alembic migration，並測試 SQLite、PostgreSQL。
6. 所有重要參數經 .env 管理；禁止提交 .env、資料庫、瀏覽器狀態或憑證。
7. 修改後執行 backend 測試與 frontend build；整合變更執行 Compose 與瀏覽器驗證。
8. 在 modify.md 記錄改動及驗證。實際犯錯時，將原因、修正及预防寫入 lesson.md。
9. 提交前檢查 staged diff；使用簡單中文 commit。未獲使用者要求不可刪除持久化 volume。

標準命令將於 README.md 隨專案完成同步更新。
