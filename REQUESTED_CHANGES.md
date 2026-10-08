# 本次三項修改

本次以目前的 `route_system.zip` 及相符的解壓縮版本為基礎修改。原始 ZIP 保留；工作資料夾內的程式已更新。

1. ESG 加入司機路線數、點位數、維護時間、行車時間、總工時、里程及清潔後照片的合格／不合格／待判定數與合格率，無排程司機也會列出。下方加入計算公式。
2. 里程依走訪順序加總相鄰點位的路段距離。首段為場站至第一點，保留原有方案邊界：普通方案不回站，跨縣市方案回站。有 OSRM 分段距離時逐段加總並換算公里；普通方案依各站前一段里程加總。無分段資料的跨縣市舊檔保留既有總里程，重新計算後保留完整分段資料。既有基準路線匯入及是否可比較的判斷規則保留。
3. 影像結果只輸出與評估 `overflow_bin`、`bottle`、`toiletpaper`。清掃前風險分數為滿溢垃圾桶數 × 3 + 瓶子數 + 衛生紙數，沿用 > 8 的風險門檻；清掃後以上三類皆為零時合格。

路線數、點位與工時是目前所選方案的排程統計；清潔狀況是目前公司所有清潔後照片的歷史統計。照片筆數不等於實際完成點位數，合格率只以已判定照片為分母。資料庫讀取失敗會顯示提示與「—」，不會誤顯示為零。

未修改資料庫結構、既有紀錄、歷史分數、照片、模型、帳密或路線輸出檔；未執行 migration、初始化或資料刪除。歷史判定不會自動重算。實際 PostgreSQL 連線與真實照片辨識尚未驗證。

驗證：25 項新舊測試通過、Django 系統檢查通過、Python 與 JavaScript 語法檢查通過。與原始 ZIP 比對 67 個 routing 程式／模板檔案，只有下列 8 個原有檔案變更：

- `routing/views.py`
- `routing/mobile_api.py`
- `routing/services/routing_cost_provider.py`
- `routing/services/dashboard_assets.py`
- `routing/services/phase2_scheduler_cross_county.py`
- `routing/services/phase2_scheduler_cross_county_compact.py`
- `routing/templates/routing/esg.html`
- `routing/templates/routing/cleaning_report.html`

新增 `routing/test_esg_cleaning.py` 與本說明。原始程式備份在工作區的 `original_sources_before_requested_changes.zip`。

`route_system_requested_changes.zip` 只包含修改檔案、測試與說明，不是完整專案。解壓至原專案外層，使 ZIP 內的 `route_system/manage.py` 所在層級對應你的原專案 `manage.py` 層級（修改包本身不包含 manage.py），再覆蓋相對應檔案。重新啟動 Django 後可看到變更；要讓既有路線輸出保存完整分段資料，請使用網站原有的重新計算功能。
