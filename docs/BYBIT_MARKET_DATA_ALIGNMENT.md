# Bybit V5 Public WS → MarketDataSource Alignment

**用途**：動工 `LiveMarketDataSource` 前，確認 Bybit public WebSocket 格式對得上 `OrderbookSnapshot` / `Trade` / `FundingRate`。配 `scripts/bybit_market_data_smoke.py`（public、**免 API key**）。

**狀態**：smoke 已跑（2026-05-20，mainnet linear BTCUSDT，`orderbook.50` / `publicTrade` / `tickers`）。

**配對閱讀**：[BYBIT_ABC_ALIGNMENT.md](BYBIT_ABC_ALIGNMENT.md)（私有 REST/WS）、[PHASE0_DESIGN.md](PHASE0_DESIGN.md) §8。

---

## 1. 重大發現

### 1.1 [CRITICAL] pybit 給的 orderbook levels **未排序**

`orderbook.50` snapshot 的 `data.a`（asks）實測非升序：
```
a[0]=76623.30, a[1]=76623.70, a[2]=76620.70, a[3]=76620.80 ...
```
真正的 best ask（76614.50，對照同時刻 ticker `ask1Price`）**埋在列表中間**。bids 這批恰好降序，但**不可假設**。

→ **LiveMarketDataSource 必須自己排序**（bids 降序、asks 升序）。`OrderbookSnapshot.best_bid/best_ask` 取 `[0]`，不排序會拿到錯價 → PaperVenue 的 fill 判斷全錯。

### 1.2 pybit `orderbook_stream` 給「完整 book」非增量

12s 內 5 條 orderbook 全 `type:"snapshot"`、各含完整 50+50 levels、`data.u`=`data.seq` 相同。原始 Bybit 流是 1 snapshot + N delta — **pybit 內部已合併**，每次 callback 給完整 book。

→ 但**不假設 pybit 永遠合併**：LiveMarketDataSource 自己維護 book（`type=snapshot` 重置整個 book；`type=delta` 套用增量、`size="0"` 刪除該 level），兼容兩種行為 + 符合 MarketDataSource ABC「實作負責 snapshot+diff sequencing」注釋。

### 1.3 數字皆 string、orderbook/trade 的 `ts` 是 ms **int**

`[price, size]` 皆 string。但 top-level `ts` 與 trade 的 `T` 是 ms epoch **int**（注意：REST 那邊的 ms 是 string，§BYBIT_ABC_ALIGNMENT §4.7）。to_datetime 需容納 int。

---

## 2. 欄位映射

### 2.1 Orderbook（topic `orderbook.50.{SYMBOL}`）

| `OrderbookSnapshot` | Bybit | 備註 |
|---|---|---|
| `symbol` | `data.s` | |
| `bids` | `data.b`: `[[price,size],...]` | **自己排降序**；delta 中 `size="0"` = 刪除該 price |
| `asks` | `data.a` | **自己排升序** |
| `sequence` | `data.u`（update id）| `data.seq` 是 cross-topic seq，用 `u` 做 book 連續性 |
| `timestamp` | top-level `ts`（ms int）| |

### 2.2 Trade（topic `publicTrade.{SYMBOL}`）

`data` 是 array（一則訊息可含多筆成交），逐筆轉 `Trade`：

| `Trade` | Bybit | 備註 |
|---|---|---|
| `symbol` | `s` | |
| `price` | `p`（string）| |
| `quantity` | `v`（string）| |
| `side` | `S`（`"Buy"`/`"Sell"`）| 復用 `adapters.side_from_bybit` |
| `timestamp` | `T`（ms int）| |

### 2.3 Funding（藏在 topic `tickers.{SYMBOL}`）

Bybit 無獨立 funding WS topic。ticker 是 **snapshot+delta**（delta 只含變動欄位）→ **只在 `data` 含 `fundingRate` 時才 fire `FundingRate`**。

| `FundingRate` | Bybit ticker | 備註 |
|---|---|---|
| `symbol` | `data.symbol` | |
| `rate` | `data.fundingRate`（string）| |
| `predicted_rate` | （無）→ `None` | Bybit ticker 無預測 funding 欄位 |
| `next_funding_time` | `data.nextFundingTime`（ms string）| |
| `timestamp` | top-level `ts` | |

---

## 3. LiveMarketDataSource 設計要點

- `pybit.unified_trading.WebSocket(testnet=..., channel_type="linear")` — public，免 auth。
- **orderbook**：自維 `dict[Decimal, Decimal]`（price→size）兩個（bid/ask）；每次更新後 `sorted()` 成 bids 降序 / asks 升序 → `OrderbookSnapshot` → callback。
- **trade**：`data` array 逐筆 → `Trade` → callback。
- **funding**：ticker 過濾出含 `fundingRate` 的 → `FundingRate` → callback。
- **執行緒橋接**：pybit callback 跑在它自己的 thread，但我們的 callback 是 `async`。start() 時記 `loop = asyncio.get_running_loop()`，thread 內用 `asyncio.run_coroutine_threadsafe(callback(evt), loop)` 派回 event loop。
- **seq gap**：delta 的 `u` 不連續 → log warning（Phase 0；pybit 多半已處理斷線重連與 resnapshot）。

---

## 4. 待驗證（這次 smoke 沒涵蓋）

- delta 訊息實際長相（這批 pybit 全給 snapshot，沒看到 `type:"delta"` 的真實樣本）→ 自維 book 的 delta 分支（`size="0"` 刪除、seq-gap warning）目前依 Bybit 官方文件 + 合成測試覆蓋，需在真實 delta 出現時校驗。
- 斷線重連後 pybit 是否自動 resnapshot + seq 是否重置。
- testnet vs mainnet public 行為是否一致。
- **執行緒模型**：`_OrderbookState` / `LiveMarketDataSource._books` 無鎖，假設 pybit 用單一 receiver thread 串行調 callback。若 pybit 並發調用會有 race。需確認 pybit WebSocket 的執行緒保證；若非單緒，需加鎖或把更新 marshal 到單一 thread。
