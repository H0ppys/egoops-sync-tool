#!/usr/bin/env node
/**
 * EgoOps → 飞书多维表格 同步工具（headless）
 *
 * 数据源：https://mrobo.uav.meituan.com/egoops/api/v1/recordings
 * 目标表：Base「项目数据汇总 MT_SY」→ MTSY-egoops录制数据
 *
 * 用法：
 *   node egoops_sync.js --days 3            # 增量：最近 3 天（按录制日期）
 *   node egoops_sync.js --since 2026-11-17  # 指定起始日期
 *   node egoops_sync.js --full              # 全量
 *   node egoops_sync.js --days 1 --dry-run  # 只抓不写
 *   node egoops_sync.js --max-pages 3       # 调试：只抓前 3 页
 */
'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const ROOT = __dirname;
const CFG_PATH = path.join(ROOT, 'egoops_sync.config.json');

const cfg = JSON.parse(fs.readFileSync(CFG_PATH, 'utf8'));
const API = cfg.apiBase.replace(/\/+$/, '');

// ---------- 参数 ----------
function arg(name, def = null) {
  const i = process.argv.indexOf(`--${name}`);
  if (i < 0) return def;
  const v = process.argv[i + 1];
  return v && !v.startsWith('--') ? v : true;
}
const DRY = process.argv.includes('--dry-run');
const FULL = process.argv.includes('--full');
const DAYS = arg('days') ? Number(arg('days')) : null;
const SINCE = arg('since');
const MAX_PAGES = arg('max-pages') ? Number(arg('max-pages')) : null;

// ---------- 日志 ----------
const LOG = path.join(ROOT, cfg.logFile || 'egoops_sync.log');
function log(line) {
  const s = `${new Date().toISOString()} ${line}`;
  console.log(s);
  try { fs.appendFileSync(LOG, s + '\n'); } catch (_) {}
}

// ---------- token ----------
function jwtPayload(tok) {
  try {
    const p = tok.split('.')[1];
    return JSON.parse(Buffer.from(p + '='.repeat(-p.length % 4), 'base64url').toString('utf8'));
  } catch (_) { return {}; }
}

function loadToken() {
  const file = path.join(ROOT, cfg.tokenFile);
  if (!fs.existsSync(file)) return null;
  const j = JSON.parse(fs.readFileSync(file, 'utf8'));
  const tok = j.access_token || j.token;
  if (!tok) return null;
  const exp = jwtPayload(tok).exp || j.exp;
  return { token: tok, exp, payload: jwtPayload(tok) };
}

function tryRefreshFromEdge() {
  const script = path.join(ROOT, cfg.edgeExtractScript || 'extract_oidc2.py');
  if (!fs.existsSync(script)) return false;
  log('token 即将过期，尝试从 Edge 会话重新提取…');
  const r = spawnSync('python', [script], { cwd: ROOT, encoding: 'utf8', timeout: 120000 });
  if (r.status !== 0) {
    log(`重新提取失败: ${(r.stderr || r.stdout || '').slice(0, 300)}`);
    return false;
  }
  return true;
}

function getToken() {
  let t = loadToken();
  if (!t) throw new Error(`未找到 token 文件 ${cfg.tokenFile}`);
  const now = Math.floor(Date.now() / 1000);
  if (!t.exp || t.exp - now < 300) {
    if (tryRefreshFromEdge()) t = loadToken();
  }
  if (!t.exp || t.exp - now < 0) {
    throw new Error(`token 已过期（${new Date((t.exp || 0) * 1000).toLocaleString()}），请在 Edge 重新打开 egoops 后重试`);
  }
  return t;
}

// ---------- 抓取 ----------
async function fetchPage(token, page, extra) {
  const qs = new URLSearchParams({ page: String(page), size: String(cfg.pageSize || 20) });
  for (const [k, v] of Object.entries({ ...cfg.filters, ...extra })) {
    if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
  }
  const url = `${API}/api/v1/recordings?${qs}`;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const res = await fetch(url, {
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: 'application/json, text/plain, */*',
          'User-Agent': 'egoops-sync/1.0',
        },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return await res.json();
    } catch (e) {
      if (attempt === 3) throw e;
      await new Promise(r => setTimeout(r, 800 * attempt));
    }
  }
}

async function fetchAll(token) {
  const extra = {};
  if (DAYS) {
    const d = new Date();
    const fmt = (x) => x.toISOString().slice(0, 10);
    extra.record_date_from = fmt(new Date(d.getTime() - (DAYS - 1) * 86400000));
    extra.record_date_to = fmt(d);
  }
  if (SINCE) extra.record_date_from = SINCE;
  if (FULL) { delete extra.record_date_from; delete extra.record_date_to; }

  const first = await fetchPage(token, 1, extra);
  const total = first.total || 0;
  const pageSize = (first.items || []).length || cfg.pageSize || 20;
  let pages = Math.ceil(total / pageSize);
  if (MAX_PAGES) pages = Math.min(pages, MAX_PAGES);
  log(`过滤条件: ${JSON.stringify(extra)} → total=${total}, 页数=${pages}`);

  const items = [...(first.items || [])];
  const CONC = Math.max(1, Number(cfg.concurrency || 4));
  let next = 2;
  const worker = async () => {
    while (next <= pages) {
      const p = next++;
      try {
        const j = await fetchPage(token, p, extra);
        items.push(...(j.items || []));
        if (p % 20 === 0) log(`  已抓取 ${items.length}/${total}`);
      } catch (e) {
        log(`  第 ${p} 页失败: ${e.message}`);
      }
    }
  };
  await Promise.all(Array.from({ length: CONC }, worker));
  return { items, total };
}

// ---------- 映射 ----------
function fmtDuration(secs) {
  const s = Math.round(Number(secs) || 0);
  if (s < 60) return `${s}s`;
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const parts = [];
  if (h) parts.push(`${h}h`);
  if (m) parts.push(`${m}m`);
  if (sec) parts.push(`${sec}s`);
  return parts.join(' ') || '0s';
}

function fmtDateTime(v) {
  if (!v) return null;
  return String(v).replace('T', ' ').slice(0, 16);
}

const QC_MAP = { pass: '通过', reject: '拒绝', failed: '执行失败', pending: '待质检' };
const MODE_MAP = { passive: '被动', active: '主动' };

function toRecord(it) {
  const qc = it.void ? '拒绝' : (QC_MAP[it.qc_status] || '待质检');
  return {
    'Token ID': it.token_id,
    '任务': it.task_name || '',
    '采集员': it.collector_name || '',
    '执行者姓名': it.actor_name || '',
    '模式': MODE_MAP[it.collect_mode] ? [MODE_MAP[it.collect_mode]] : undefined,
    '时长（原文）': fmtDuration(it.duration_secs),
    '时长（秒）': Math.round(Number(it.duration_secs) || 0),
    'QC': [qc],
    '入库时间': fmtDateTime(it.created_at),
    '批次': `egoops-${new Date().toISOString().slice(0, 10)}`,
    '大小（GB）': it.file_size_bytes ? Math.round(it.file_size_bytes / 1073741824 * 100) / 100 : null,
    '录制时间': fmtDateTime(it.record_time),
    '设备 SN': it.device_serial || '',
    '数据源节点': it.tet_node_path || '',
    '质检结果': it.void ? `void:${it.split_result || 'unknown'}` : (it.qc_result || ''),
  };
}

// ---------- lark-cli ----------
function lark(args, { timeout = 120000 } = {}) {
  const r = spawnSync(cfg.larkBin, args, { encoding: 'utf8', timeout, windowsHide: true });
  const out = r.stdout || '';
  let j = null;
  try { j = JSON.parse(out); } catch (_) {}
  return { status: r.status, out, err: r.stderr || '', json: j };
}

function existingTokenIds() {
  const ids = new Set();
  const tmp = path.join(os.tmpdir(), `egoops_dedupe_${process.pid}.ndjson`);
  let offset = 0;
  const limit = 2000;
  for (let round = 0; round < 50; round++) {
    try { fs.unlinkSync(tmp); } catch (_) {}
    const r = lark([
      'base', '+record-list',
      '--base-token', cfg.baseToken,
      '--table-id', cfg.tableId,
      '--field-id', 'Token ID',
      '--limit', String(limit),
      '--offset', String(offset),
      '--format', 'ndjson',
      '--output', tmp,
    ]);
    if (!r.json || !fs.existsSync(tmp)) break;
    const cnt = r.json.records_count || 0;
    const lines = fs.readFileSync(tmp, 'utf8').split('\n').filter(Boolean);
    for (const line of lines) {
      try {
        const o = JSON.parse(line);
        const v = o['Token ID'];
        if (v) ids.add(String(v).trim());
      } catch (_) {}
    }
    if (!r.json.has_more || lines.length === 0) break;
    offset += cnt;
  }
  return ids;
}

function writeBatch(records) {
  const tmp = path.join(os.tmpdir(), `egoops_batch_${process.pid}.json`);
  fs.writeFileSync(tmp, JSON.stringify({ create_records: records }), 'utf8');
  const r = lark([
    'base', '+record-batch-create',
    '--base-token', cfg.baseToken,
    '--table-id', cfg.tableId,
    '--json', `@${tmp}`,
  ], { timeout: 180000 });
  try { fs.unlinkSync(tmp); } catch (_) {}
  return r;
}

// ---------- 主流程 ----------
(async () => {
  const t = getToken();
  log(`启动：用户=${t.payload.name || '?'} token 到期=${new Date(t.exp * 1000).toLocaleString()}${DRY ? ' [dry-run]' : ''}`);

  const { items, total } = await fetchAll(t.token);
  log(`抓取完成：${items.length} 条（服务端 total=${total}）`);

  const mapped = items.map(toRecord).filter(r => r['Token ID']);
  const uniq = new Map();
  for (const r of mapped) uniq.set(r['Token ID'], r);
  log(`去重（本次抓取内）：${mapped.length} → ${uniq.size}`);

  const existing = existingTokenIds();
  log(`目标表已存在 ${existing.size} 条 Token ID`);

  const fresh = [...uniq.values()].filter(r => !existing.has(r['Token ID']));
  log(`需要新增：${fresh.length} 条`);

  if (DRY) {
    log(`[dry-run] 示例记录：${JSON.stringify(fresh[0] || {}, null, 0).slice(0, 400)}`);
    return;
  }

  const BATCH = Number(cfg.batchSize || 100);
  let ok = 0, fail = 0;
  for (let i = 0; i < fresh.length; i += BATCH) {
    const chunk = fresh.slice(i, i + BATCH);
    const r = writeBatch(chunk);
    if (r.json && r.json.ok) {
      ok += chunk.length;
      log(`  写入 ${i + chunk.length}/${fresh.length}`);
    } else {
      fail += chunk.length;
      log(`  批次失败（${chunk.length} 条）：${(r.json ? JSON.stringify(r.json.error) : r.err || r.out).slice(0, 300)}`);
    }
  }
  log(`完成：成功 ${ok} 条，失败 ${fail} 条`);
})().catch(e => {
  log(`ERROR: ${e.message}`);
  process.exit(1);
});
