# Bybit V5 → Venue ABC Alignment

**用途**：在動工 `BybitVenue` 之前，**先確認** Bybit V5 API 對得起 `Venue` / `MarketDataSource` / 相關 domain types 的假設。發現不對齊 → 早期修正 ABC 或補進 BybitVenue 的 adapter logic。

**狀態**：desk research 完成；smoke script 在 `scripts/bybit_testnet_smoke.py`，需實際跑過 testnet 才能 finalize 第 5 節 checklist。

**配對閱讀**：[PHASE0_DESIGN.md](PHASE0_DESIGN.md) §1.5（已建抽象清單）。

---

## 1. Endpoint Mapping

| Venue ABC 方法 | Bybit V5 endpoint | pybit 對應 | 備註 |
|---|---|---|---|
| `place_order` | `POST /v5/order/create` | `http.place_order(category="linear", ...)` | 需 `category` 參數，Phase 0 全部 `linear`（USDT 永續）|
| `cancel_order` | `POST /v5/order/cancel` | `http.cancel_order(category, symbol, orderId)` | 必須帶 `symbol`，不能只有 orderId |
| `get_order` | `GET /v5/order/realtime`（open） + `/v5/order/history`（已關閉）| `http.get_open_orders` / `get_order_history` | 拆兩個 endpoint：active vs historical |
| `get_open_orders` | `GET /v5/order/realtime` | `http.get_open_orders(category, ...)` | 支援 pagination (cursor) |
| `get_positions` | `GET /v5/position/list` | `http.get_positions(category, settleCoin="USDT")` | `settleCoin` 必填，避免一次拉所有合約 |
| `get_balance` | `GET /v5/account/wallet-balance` | `http.get_wallet_balance(accountType="UNIFIED")` | Unified Account 用 UNIFIED；舊 contract account 不在 Phase 0 範圍 |
| `on_order_update` | WS private `order` topic | `ws.order_stream(callback)` | 包含 status transitions |
| `on_fill` | WS private `execution` topic | `ws.execution_stream(callback)` | 跟 order 是兩個 stream |
| —（時鐘）| `GET /v5/market/time` | `http.get_server_time()` | 應同步本地 Clock 偏差 |
| —（ticker）| `GET /v5/market/tickers` | `http.get_tickers(...)` | 取 last price / mark price |

`MarketDataSource` 的 endpoint 暫不列（待另一個 smoke script）。

---

## 2. Field Mapping

### 2.1 Order

| 我們的 `Order` 欄位 | Bybit 欄位 | 注意事項 |
|---|---|---|
| `order_id: OrderId` | `orderId: str` | UUID 樣式字串 |
| `client_order_id: ClientOrderId` | `orderLinkId: str` | **最長 36 字元** — 我們的 `oms-{uuid4().hex}` 剛好 36 字（4+32）|
| `symbol: Symbol` | `symbol: str` | 直接對應 |
| `side: OrderSide.BUY/SELL` | `side: "Buy"/"Sell"` | **大小寫差異** — BybitVenue adapter 轉換 |
| `type: OrderType` | `orderType: "Limit"/"Market"` | **POST_ONLY 不在這 → §4.1 mismatch** |
| `quantity: Decimal` | `qty: str` | Bybit 所有數字欄位都是 string，轉 Decimal |
| `filled_quantity: Decimal` | `cumExecQty: str` | 同上 |
| `price: Decimal \| None` | `price: str`（market order 為 "0"）| 轉 Decimal；market order 注意 |
| `average_fill_price: Decimal \| None` | `avgPrice: str` | 同上；未成交時為 "0" 或 None |
| `status: OrderStatus` | `orderStatus: str` | **8 vs 11 個值的 mapping → §4.2** |
| `time_in_force: TimeInForce` | `timeInForce: "GTC"/"IOC"/"FOK"/"PostOnly"` | 多一個 PostOnly |
| `reduce_only: bool` | `reduceOnly: bool` | 直接對應 |
| `created_at: datetime` | `createdTime: str` (ms epoch) | 轉 `datetime` (UTC) |
| `updated_at: datetime` | `updatedTime: str` (ms epoch) | 同上 |

### 2.2 Position

| 我們的 `Position` 欄位 | Bybit 欄位 | 注意事項 |
|---|---|---|
| `symbol: Symbol` | `symbol: str` | 直接對應 |
| `side: PositionSide` | `side: "Buy"/"Sell"/""` | **空字串 = FLAT** — BybitVenue 過濾 / 轉換 |
| `quantity: Decimal` | `size: str` | 轉 Decimal |
| `entry_price: Decimal` | `avgPrice: str` | 加權平均 |
| `mark_price: Decimal` | `markPrice: str` | 直接對應 |
| `unrealized_pnl: Decimal` | `unrealisedPnl: str` | 注意拼字 `unrealised` (英式) |
| `realized_pnl: Decimal` | `cumRealisedPnl: str` | 累積 realized；semantics 接近但**可能 lifetime 而非開倉以來**，需驗證 |
| `leverage: Decimal` | `leverage: str` | 直接對應 |
| `liquidation_price: Decimal \| None` | `liqPrice: str` | 空字串或 "0" 時 → None |
| `updated_at: datetime` | `updatedTime: str` (ms) | 轉 datetime |

### 2.3 Balance

| 我們的 `Balance` 欄位 | Bybit 欄位（Unified）| 注意事項 |
|---|---|---|
| `currency: str` | n/a（Unified 是多幣種 collateral）| Phase 0 hard-code "USDT"；長期要重新設計 |
| `total: Decimal` | `totalEquity: str` | **已含未實現 PnL**（Bybit Unified 行為）— 跟我們 PaperVenue 一致 |
| `available: Decimal` | `totalAvailableBalance: str` | ⚠️ [實測 2026-05-20] 小餘額/無持倉帳戶此欄為**空字串 `""`**（非 "0"）。get_balance 須 fallback：`""` → available = totalEquity（視為無 margin 占用）|
| `margin_used: Decimal` | `totalEquity - totalAvailableBalance`（推算） | ⚠️ available 為 `""` 時無法相減 → margin_used = 0。`totalMarginBalance` / `totalInitialMargin` 實測同樣為 `""`，不可靠 |
| `updated_at: datetime` | response timestamp | 用 server time |

### 2.4 Fill / Execution

| 我們的 `Fill` 欄位 | Bybit `execution` 欄位 | 注意事項 |
|---|---|---|
| `order_id: OrderId` | `orderId` | 直接對應 |
| `symbol: Symbol` | `symbol` | 直接對應 |
| `side: OrderSide` | `side: "Buy"/"Sell"` | 大小寫轉換 |
| `price: Decimal` | `execPrice: str` | 轉 Decimal |
| `quantity: Decimal` | `execQty: str` | 轉 Decimal |
| `fee: Decimal` | `execFee: str` | 轉 Decimal |
| `fee_currency: str` | `feeCurrency: str`（V5）| 通常 "USDT" |
| `is_maker: bool` | `isMaker: bool` | 直接對應 |
| `timestamp: datetime` | `execTime: str` (ms) | 轉 datetime |

**[實測 2026-05-20 — mainnet read-only smoke]**（`scripts/bybit_mainnet_readonly_smoke.py`，指向 mainnet、零下單呼叫）：用真實 mainnet UNIFIED 帳戶（餘額近 0）驗證。
- ✅ Balance：`result.list[0]` 結構確認，數字皆 string，**不適用欄位為 `""`**（見 §2.3 修正 — available/margin 欄空字串）。`coin[].cumRealisedPnl` 實測 `"-41.30959261"` 確認是 **lifetime 累積**（非開倉以來）。
- ✅ 約定三連：數字=string、空=`""`、timestamp 為 ms（top-level `time` 是 int，欄位是 string）。
- ⏳ **未驗證**（帳戶無持倉、無歷史訂單，`result.list` 皆空 `[]`）：Position item 欄位、Order item 欄位 + `orderStatus` 字串拼寫、Position `side=''` FLAT 實況。adapters.py 的 `_STATUS_FROM_BYBIT` / position 欄位映射目前依 Bybit V5 官方文件，**待真實持倉或 testnet 下單時做最終校驗**（doc §7 sign-off 前）。

**有一個 Phase 0 之後要處理的維度**：Bybit `execType` 有多種值：
- `Trade` — 正常成交（我們唯一處理）
- `Funding` — funding payment（不是 order 成交，但會影響 PnL）
- `AdlTrade` — 自動減倉
- `BustTrade` — 強制平倉
- `Settle` — 結算

Phase 0 BybitVenue 只處理 `Trade`，其他 log + skip。Funding 之後可以用獨立 channel 處理。

---

## 3. WebSocket 細節

### 3.1 Channels

| 用途 | URL | Auth |
|---|---|---|
| Public market data | `wss://stream-testnet.bybit.com/v5/public/linear` | none |
| Private (orders + executions + position) | `wss://stream-testnet.bybit.com/v5/private` | API key + HMAC signature |

### 3.2 Reconnection

Bybit 30 分鐘斷一次 connection（無 heartbeat 就斷）。`pybit.WebSocket` 內建 ping + auto-reconnect。

但 **重連後 state 重置** — 上一次斷線到重連之間的 order 更新 / executions 可能漏。BybitVenue 重連後需要：
1. REST 拉所有 open orders / positions
2. 跟內部 cache diff，補上漏的 order updates
3. Fills 漏掉的話從 `get_execution_list` 補

→ DefaultOMS 的 reconciliation 設計（現在還沒實作）就是這個用途。

### 3.3 Heartbeat

Bybit WS 要每 20 秒發 ping。pybit 內建處理。BybitVenue 不用管。

---

## 4. 已知 ABC / Domain 不對齊（修正 BEFORE BybitVenue）

### 4.1 [HIGH] [RESOLVED 2026-05-20] `POST_ONLY` 從 `OrderType` 遷移到 `TimeInForce`

**Status**：✅ 已修。提交在這份 doc 寫完當天的同一天。

**修法**：
- `OrderType` 拿掉 `POST_ONLY`，剩 `MARKET / LIMIT / STOP_MARKET / STOP_LIMIT`
- `TimeInForce` 加入 `POST_ONLY`，現在是 `GTC / IOC / FOK / POST_ONLY`
- **POST_ONLY 拒絕邏輯放在 `PaperVenue.place_order`**：若 `TIF=POST_ONLY` 且當下 book 顯示會 cross → status=REJECTED；否則正常 ACKED 進 open orders
- **`ImmediateFillEngine` 完全不認識 POST_ONLY**：一旦 venue 接受了，就跟普通 LIMIT 一樣的 fill 邏輯（市場往我們的價格動就成交）
- 沒 book snapshot 時保守 ACCEPT（跟真實 matching engine 對齊 — 不能用「不知道」當拒絕理由）

**新增測試**：
- `test_post_only_buy_rejected_when_would_cross_at_submission`
- `test_post_only_sell_rejected_when_would_cross_at_submission`
- `test_post_only_accepted_when_not_crossing`
- `test_post_only_accepted_when_no_book_available`
- `test_post_only_accepted_then_fills_via_later_orderbook_move`

**Meta 觀察**：這個 ABC 設計錯誤是在**沒寫 BybitVenue 半行 code 的情況下**透過 alignment exercise 抓到的。如果直接動工 BybitVenue，會在實作到一半時被迫處理 — 要嘛在 BybitVenue 加 ad-hoc 邏輯（讓 abstraction 變髒），要嘛中斷 BybitVenue 回去改 domain enum 並 ripple 到 ImmediateFillEngine / PaperVenue / 6 個測試。**Desk research 換來 1 個 HIGH 級設計修正，總體 cost 比 mid-implementation 修少很多**。

### 4.2 [MED] `OrderStatus` 映射有 gap

| Bybit V5 | 我們的 `OrderStatus` | 處理 |
|---|---|---|
| `Created` | （無對應）| 罕見短暫狀態。BybitVenue 視為 `SUBMITTED` |
| `New` | `ACKED` | 直接映射 |
| `PartiallyFilled` | `PARTIALLY_FILLED` | ✓ |
| `Filled` | `FILLED` | ✓ |
| `Cancelled` | `CANCELLED` | ✓ |
| `PartiallyFilledCanceled` | `CANCELLED` | 注意：cumExecQty > 0，要分流 fill 通知 |
| `Rejected` | `REJECTED` | ✓ |
| `Untriggered` / `Triggered` / `Deactivated` | （無）| 條件單，Phase 0 不支援 → BybitVenue placeOrder reject |
| `Active` | （無）| TP/SL linked order — 不在 Phase 0 |
| —（無）| `EXPIRED` | Bybit 沒有；TIF=IOC/FOK 自動 → `Cancelled`。我們的 EXPIRED 留給 GTD（GoodTillDate）支援，目前無需 |
| —（無）| `FAILED` | 純 local — 網路 / timeout，沒到 venue |
| —（無）| `SUBMITTED` | local — 已送出但 venue 還沒 ack |

**動作**：BybitVenue adapter 寫一個 `_status_map: dict[str, OrderStatus]`，未知 status fail loud（log + REJECTED + alert）。

### 4.3 [LOW] `Symbol` 跨交易所 conventions

Bybit BTCUSDT 永續 = "BTCUSDT"，跟我們 `Symbol("BTCUSDT")` 一致。但 Binance 用 "BTCUSDT" / "BTCUSDT_PERP" / etc.，Hyperliquid 用 "BTC"。

→ Phase 0 single-venue（Bybit）不是問題。Phase 3+ cross-venue 才要正規化（可能加 `Instrument` 抽象區別 venue 內 symbol 和 canonical symbol）。

### 4.4 [LOW] 多幣種 collateral

Bybit Unified Trading Account 支援 BTC / ETH / USDT / USDC 同時當 collateral。我們的 `Balance.currency` 是單一字串。

**Phase 0 策略**：BybitVenue.get_balance 永遠回 `currency="USDT"`，其他 collateral 折算到 USDT equity（Bybit 已經算好，看 `totalEquity`）。

長期：可能要 `Balance` 變 `dict[Currency, BalanceEntry]` 或新增 `equity_usd` 欄位。Phase 0 不動。

### 4.5 [LOW] 數字欄位是 string

Bybit 所有數字（price / qty / pnl / etc）在 JSON 裡是 **string**。我們所有對應欄位是 `Decimal`。

**adapter pattern**：BybitVenue 寫一個 `_to_decimal(s)` helper，所有 venue response 過一遍。空字串或 "0" 適當處理（空通常代表 None / 不適用，例如 LiqPrice = "" 表示 cross margin 沒清算價）。

### 4.6 [INFO] Rate limits

Bybit Unified Trading Account V5 限速（per UID）：
- Order placement: **10 req/sec** sustained, burst 可短暫超過
- Order cancel: **10 req/sec**
- Position / wallet queries: **50 req/sec**
- Total: 600/min total order ops（含 batch ops）

對 Phase 0 funding capture（每 8 小時下單一兩次）完全足夠。但 BybitVenue 應該有 rate limiter（token bucket）防意外。觸發 429 → trigger `RateLimitBreaker`（還沒實作）。

### 4.7 [INFO] Time / timestamp

- Bybit 所有 timestamp = ms epoch string，e.g. `"1700000000000"`
- 我們所有 datetime 是 timezone-aware UTC
- BybitVenue 統一轉換：`datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)`

---

## 5. To verify by running the smoke script

**[2026-05-20 mainnet read-only smoke 已驗證]**（`scripts/bybit_mainnet_readonly_smoke.py`）：
- ✅ `get_server_time` 格式 `{retCode:0, result:{timeSecond, timeNano}, time:<ms int>}`
- ✅ `get_wallet_balance` 結構符合 §2.3（含 available 空字串修正）
- ✅ `get_positions` 無倉位回 `result.list: []`（不 raise、不 None）
- ✅ `get_open_orders` / `get_order_history` 無訂單回 `result.list: [] + nextPageCursor: ""`
- ⏳ 未驗證（需真實持倉/訂單或 testnet）：position `side=''` FLAT、order item 欄位、status 拼寫、WS streams、cid 36 字元、cancel 延遲

下面原始 checklist 保留供日後 testnet 全驗證（含 write path）使用：

執行 `python scripts/bybit_testnet_smoke.py` 後，逐項確認：

- [ ] `get_server_time()` 回傳格式是 `{retCode: 0, result: {timeSecond, timeNano}}`
- [ ] `get_wallet_balance` 回傳結構符合 §2.3 欄位假設
- [ ] `get_positions` 在無倉位時回 empty list（不是 raise，不是 None）
- [ ] `get_positions` 倉位 `side` 在 FLAT 時確實是 `""`（不是 `null`）
- [ ] `place_order(orderLinkId="oms-..." )` 接受 36 字元的 cid（不會被 truncate / reject）
- [ ] `place_order` 回傳的 `orderId` 是 UUID 樣式字串
- [ ] WS `order_stream` 訊息結構包含 `orderId`, `orderLinkId`, `orderStatus`, `cumExecQty`, `avgPrice`, `updatedTime`
- [ ] WS `execution_stream` 訊息結構包含 `orderId`, `execPrice`, `execQty`, `execFee`, `isMaker`, `execTime`, `execType`
- [ ] WS 認證 → subscribe → 收到第一個 event 的 latency 約 < 1 秒
- [ ] 同一個訂單成交時，WS order 跟 execution 兩個 stream **都會 fire**（驗證 §2.4 整合假設）
- [ ] 訂單從 cancel 到 status 變成 "Cancelled" 的延遲約 < 200ms
- [ ] cancelled order 還能用 `get_order_history` 查到（不是只 active orders）
- [ ] timestamps 在 WS 與 REST 是否一致（都是 ms epoch string）

---

## 6. 對 BybitVenue 實作的影響清單

跑完 smoke + 上面 checklist 確認後，BybitVenue 動工要解決：

1. **POST_ONLY 遷移** — 改 domain enum + 更新 ImmediateFillEngine + PaperVenue 行為（4.1）
2. **`_status_map` 字典** — Bybit → OrderStatus 映射，未知 fail loud（4.2）
3. **`_to_decimal(s)` adapter** — 所有 string number 統一轉換（4.5）
4. **`_to_datetime(ms_str)` adapter** — ms epoch → UTC datetime（4.7）
5. **Side 大小寫轉換** — "Buy"/"Sell" ↔ `OrderSide.BUY`/`SELL`
6. **空字串 = FLAT 處理** — Position 跟 LiqPrice 兩個欄位都有（2.2）
7. **WS 重連後 reconciliation** — 補回斷線期間漏的 events（3.2）
8. **Rate limiter** — token bucket，per-endpoint（4.6）
9. **execType filter** — Phase 0 只認 `Trade`，其他 log + skip（2.4）

每一項都應該是 BybitVenue impl 的單獨 PR / commit。

---

## 7. 何時 sign-off

當：
- 第 5 節 checklist 全部打勾
- 第 4 節 [HIGH] 等級的 ABC 修正已落地（POST_ONLY 遷移）
- 第 6 節 9 項都有對應的 BybitVenue test plan

→ BybitVenue 可以動工。

---

## 8. 後續 docs

- `BYBIT_MARKET_DATA_ALIGNMENT.md`（TBD）— public WS（orderbook / trades / funding）跟 `MarketDataSource` ABC 對齊，配 `scripts/bybit_market_data_smoke.py`（TBD）
- `OPERATIONS_RUNBOOK.md`（TBD）— Live 跑起來後出狀況時的 SOP（QUARANTINE / acknowledge / 手動 cancel / 對帳工具）
