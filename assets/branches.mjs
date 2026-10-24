import { esc, fmt, finite, warrantCp, daysUntil, toCSV } from './model.mjs';

export const branchSourceUrl = code => /^\d{5}[0-9A-Z]$/.test(code) ? `https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco_${code}_1.djhtm` : null;
export function filterRankings(rows, query = '', side = '') {
  const q = query.trim().toLowerCase();
  return rows.filter(r => (!side || r.side === side) && (!q || `${r.branchCode} ${r.branchName}`.toLowerCase().includes(q)));
}
export function comparisonLabel(day) {
  if (!finite(day?.quoteVolume)) return '日期不一致或官方成交量缺值，暫不比較。';
  if (day.buyGap === 0 && day.sellGap === 0) return '本日排行買賣合計與官方成交量相符；仍不代表此來源提供全部分點。';
  return `官方成交量 ${fmt(day.quoteVolume, 3)} 張；與排行買進合計差 ${fmt(day.buyGap, 3)} 張、賣出合計差 ${fmt(day.sellGap, 3)} 張。口徑差異或排行缺漏均可能造成差額。`;
}

export function createWarrantBranches({ load, table, tr, select, dateTag, signed, metric, empty }) {
  const state = { query: '', branch: '', day: '', page: 0, listOpen: false };
  let exportRows = [], exportCode = '', exportDay = '';
  const status = item => ({ ok: '更新成功', retained: '更新失敗・保留舊值', unavailable: '暫無資料' }[item.status] || '等待更新');
  const external = code => `<a class="text-link" href="${branchSourceUrl(code)}" target="_blank" rel="noopener noreferrer">富邦原始排行 ↗</a>`;
  const link = code => `#warrant-branches?code=${encodeURIComponent(code)}`;

  async function summary() {
    let index;
    try { index = await load('warrant-branches'); } catch { return `<section class="panel"><h2>權證分點排行</h2><p class="note">分點資料暫時無法載入，請稍後重試。</p></section>`; }
    return `<section class="panel source-list"><span class="eyebrow">WARRANT BROKER RANKINGS</span><h2>富邦權證分點排行 · 非全部分點</h2><p>${esc(index.schedule || '每日台北時間 20:13')}。依最新官方權證成交量取前 ${fmt(index.topCount)} 檔，另含固定追蹤清單；只收錄權證。每檔資料日期與失敗保留狀態另行顯示。</p><p>最近檢查：${esc(index.checkedAt ? new Date(index.checkedAt).toLocaleString('zh-TW', { timeZone: 'Asia/Taipei', hour12: false }) : '尚無紀錄')}（台北） · ${fmt(index.items.filter(i => i.status === 'ok').length)} / ${fmt(index.items.length)} 檔更新成功。${index.error ? esc(index.error) : ''}</p><a href="#warrant-branches">查看分點與收錄清單 →</a> · <a href="https://github.com/liaontu/derivatives-analysis/actions/workflows/warrant-branches-daily.yml" target="_blank" rel="noopener noreferrer">查看分點更新紀錄 ↗</a><p class="note">來源：富邦證券公開網站／嘉實資訊。每檔買超及賣超排行各最多 15 名；未出現在排行的分點不代表沒有交易，買賣相抵為零的分點可能未列入。排行不等於全部券商分點，分點也不代表單一投資人。</p></section>`;
  }

  async function page() {
    const index = await load('warrant-branches');
    const requested = new URLSearchParams(location.hash.split('?')[1] || '').get('code');
    const code = branchSourceUrl(requested || '') ? requested : index.items.find(i => i.code === '081105')?.code || index.items[0]?.code;
    const selected = index.items.find(i => i.code === code);
    const query = state.query.trim().toLowerCase();
    const items = index.items.filter(i => !query || `${i.code} ${i.name} ${i.underlying} ${i.underlyingName}`.toLowerCase().includes(query));
    state.page = Math.max(0, Math.min(state.page, Math.ceil(items.length / 15) - 1));
    let detail = '';
    exportRows = [];
    if (selected) {
      const payload = await load(`warrant-branches/${code}`);
      const dates = (payload.history || []).map(d => d.date);
      const chosen = dates.includes(state.day) ? state.day : dates[0];
      const day = payload.history?.find(d => d.date === chosen);
      const rows = filterRankings(day?.rows || [], state.branch);
      exportCode = code; exportDay = chosen;
      exportRows = rows.map(r => ({ code, name: selected.name, date: chosen, ...r, unit: '張', completeBranchCoverage: '否：買超／賣超各最多15名', source: '富邦證券／嘉實資訊' }));
      const stale = payload.date && daysUntil(payload.date) < -4;
      const ranking = side => `<section class="panel"><div class="panel-head"><h2>${side === 'buy' ? '買超' : '賣超'}分點排行</h2><span class="tag">最多 15 名 · 張</span></div>${table(['分點 / 排名', '買進', '賣出', '買賣超', '占成交比重'], rows.filter(r => r.side === side).map(r => tr([`${esc(r.branchName)}<small>${esc(r.branchCode)} · #${r.rank}</small>`, fmt(r.buy, 3), fmt(r.sell, 3), signed(r.net), `${fmt(r.share, 2)}%`])))}<p class="note">僅顯示來源排行；未列入不代表交易量為零。數字依來源顯示取整，買進－賣出可能與買賣超差 1 張；排行也可能列出顯示為 0 張的分點。</p></section>`;
      detail = `<section class="panel"><div class="panel-head"><div><span class="eyebrow">${esc(code)} · ${selected.market === 'TWSE' ? '上市' : '上櫃'} · ${warrantCp(selected.cp)}</span><h2>${esc(selected.name)}</h2></div>${external(code)}</div>${selected.underlying ? `<p><a class="text-link" href="#warrants?underlying=${encodeURIComponent(selected.underlying)}">查看 ${esc(selected.underlyingName || selected.underlying)} 的全部權證 →</a></p>` : ''}<p class="${selected.status !== 'ok' || stale ? 'status-bad' : 'muted'}">${status(selected)}${stale ? ' · 資料距今超過 4 天，可能含休市日' : ''} · 最近資料 ${esc(payload.date || '尚無資料')}</p><div class="filters compact">${select('wb-day', '資料日期', dates, chosen || '')}<label>搜尋分點名稱或代碼<input id="wb-branch" type="search" value="${esc(state.branch)}" placeholder="例如 元大、9824"></label><button id="wb-branch-apply">篩選分點</button><button id="wb-export" ${rows.length ? '' : 'disabled'}>匯出排行 CSV</button></div><p><a class="text-link" href="#daily-watch?code=${code}">查看每日價格與分點變化 →</a></p><p class="note">每日快照最多累積 30 個資料日。來源是買超／賣超排行，非全部分點；分點買賣超不是投資人的庫存或未平倉部位。</p></section>${day ? `<div class="metrics">${metric('本日列出分點', fmt(day.rows.length), '買超與賣超排行合計 · 非全部')}${metric('排行買進合計', fmt(day.buyTotal, 3), '張 · 僅計列入排行的分點')}${metric('排行賣出合計', fmt(day.sellTotal, 3), '張 · 僅計列入排行的分點')}${metric('同日官方成交量', fmt(day.quoteVolume, 3), '張 · 日期不同不比較')}</div><p class="section-intro">${esc(comparisonLabel(day))}</p><div class="split branch-tables">${ranking('buy')}${ranking('sell')}</div>` : empty('此權證尚未取得分點排行；目前沒有用零值或示範資料替代。')}`;
    } else if (code) {
      const catalog = await load('warrants');
      const warrant = catalog.rows.find(r => r.code === code);
      detail = `<section class="panel"><h2>${esc(code)}${warrant ? ` · ${esc(warrant.name)}` : ''}</h2><p class="section-intro">${warrant ? '此權證未納入本次每日更新清單，可前往富邦查看原始排行。' : '官方權證清單中找不到此代碼；此功能不查現股。'}</p>${warrant ? external(code) : ''}</section>`;
    }
    return `<section class="panel"><a class="text-link" href="#warrants">依個股查看全部權證、到期日／履約價排序、歐式／美式 →</a><p class="note">完整權證行情清單與每日分點追蹤範圍分別顯示。</p></section><div class="notice warning"><strong>非全部券商分點</strong> · 每檔只收錄富邦買超／賣超排行各最多 15 名。未出現的分點不等於沒有交易。</div><section class="panel"><div class="panel-head"><div><span class="eyebrow">DAILY WARRANT WATCH</span><h2>每日追蹤權證</h2><p class="muted">成交量前 ${fmt(index.topCount)} 檔＋固定追蹤 ${index.watchlist?.length || 0} 檔，去重後 ${fmt(index.items.length)} 檔 · ${esc(index.schedule || '')}</p></div><a class="text-link" href="#sources">更新狀態 ↗</a></div><div class="filters"><label class="search">搜尋權證代碼、名稱或標的<input id="wb-search" type="search" value="${esc(state.query)}" placeholder="例如 081105、台積電（僅權證）"></label><button id="wb-find">搜尋／查看</button><button id="wb-reset">清除篩選</button></div><p class="muted">固定追蹤：${esc((index.watchlist || []).join('、'))}。收錄範圍每日依官方權證成交量調整；輸入完整權證代碼可查看是否收錄。${index.error ? `<span class="status-bad">${esc(index.error)}</span>` : ''}</p>${index.issues?.length ? `<p class="status-bad">${index.issues.map(i => `${esc(i.code)}：${esc(i.reason)}`).join('；')}</p>` : ''}<details id="wb-list" ${state.listOpen ? 'open' : ''}><summary>查看收錄清單（${fmt(items.length)} 檔）</summary>${table(['權證 / 代碼', '市場 / 權別', '資料日', '分點數', '更新狀態'], items.slice(state.page * 15, (state.page + 1) * 15).map(i => tr([`<a class="text-link" href="${link(i.code)}">${esc(i.name)}<small>${esc(i.code)}${i.fixed ? ' · 固定追蹤' : ''}</small></a>`, `${i.market === 'TWSE' ? '上市' : '上櫃'} / ${warrantCp(i.cp)}`, esc(i.date || '—'), fmt(i.count), `<span class="${i.status === 'ok' ? 'status-ok' : 'status-bad'}">${status(i)}</span>`])))}<div class="pagination"><span>${fmt(items.length)} 檔 · 第 ${state.page + 1} / ${Math.max(1, Math.ceil(items.length / 15))} 頁</span><div><button data-wb-page="-1" ${state.page === 0 ? 'disabled' : ''}>上一頁</button><button data-wb-page="1" ${(state.page + 1) * 15 >= items.length ? 'disabled' : ''}>下一頁</button></div></div></details></section>${detail}`;
  }

  function bind(render) {
    const $ = id => document.getElementById(id);
    const find = () => {
      state.query = $('wb-search').value.trim(); state.page = 0; state.listOpen = !branchSourceUrl(state.query);
      if (branchSourceUrl(state.query)) {
        state.day = ''; state.branch = '';
        const next = link(state.query);
        if (location.hash !== next) { location.hash = next; return; }
      }
      render();
    };
    $('wb-list').addEventListener('toggle', () => { state.listOpen = $('wb-list').open; });
    $('wb-find').addEventListener('click', find);
    $('wb-search').addEventListener('keydown', e => { if (e.key === 'Enter') find(); });
    $('wb-reset').addEventListener('click', () => { state.query = ''; state.page = 0; render(); });
    document.querySelectorAll('[data-wb-page]').forEach(el => el.addEventListener('click', () => { state.page += Number(el.dataset.wbPage); render(); }));
    if (!$('wb-day')) return;
    $('wb-day').addEventListener('change', () => { state.day = $('wb-day').value; render(); });
    const filter = () => { state.branch = $('wb-branch').value; render(); };
    $('wb-branch-apply').addEventListener('click', filter);
    $('wb-branch').addEventListener('keydown', e => { if (e.key === 'Enter') filter(); });
    $('wb-export').addEventListener('click', () => {
      const fields = [['code', '權證代碼'], ['name', '權證名稱'], ['date', '資料日'], ['branchCode', '分點代碼'], ['branchName', '分點名稱'], ['side', '排行方向'], ['rank', '名次'], ['buy', '買進'], ['sell', '賣出'], ['net', '買賣超'], ['unit', '單位'], ['completeBranchCoverage', '是否全部分點'], ['source', '來源']];
      const url = URL.createObjectURL(new Blob([toCSV(exportRows, fields)], { type: 'text/csv;charset=utf-8' }));
      const a = document.createElement('a'); a.href = url; a.download = `warrant-ranking-${exportCode}-${exportDay}-partial.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  return { page, bind, summary };
}
