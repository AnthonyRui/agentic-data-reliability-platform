const $ = id => document.getElementById(id);
const names = {schema: '字段结构变化', missing_amount: '订单金额缺失', duplicate_order: '重复订单'};
const states = {pass: '检查通过', fail: '发现异常', unknown: '结果不确定'};
let offset = 0, selected = null, generation = 0, detailGeneration = 0;
const el = (tag, text, cls) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; };
const time = value => value ? new Date(value).toLocaleString('zh-CN', {timeZoneName: 'short'}) : '—';
const scopeName = scope => scope.startsWith('sandbox:') ? '沙箱演示' : scope === 'local-compose' ? '真实源表' : scope;
const badge = item => el('span', item.status === 'open' ? '未恢复' : '已恢复', 'badge ' + item.status);
async function api(path) { const response = await fetch(path); const data = await response.json(); if (!response.ok) throw new Error(data.error || '读取失败'); return data; }
function showError(error) { $('error').hidden = false; $('error').textContent = '暂时无法读取记录，请检查本地服务。' + error.message; }
async function detail(id, evidenceOffset = 0) {
  const token = ++detailGeneration;
  selected = id;
  $('detail').replaceChildren(el('p', '读取证据中…', 'empty'));
  try {
    const data = await api('/api/incidents/' + encodeURIComponent(id) + '?offset=' + evidenceOffset);
    if (token !== detailGeneration) return;
    const item = data.incident, title = el('div', undefined, 'row');
    title.append(el('h2', names[item.check_name] || item.check_name), badge(item));
    const facts = el('dl', undefined, 'facts');
    for (const [label, value] of [['范围', scopeName(item.scope)], ['数据表', item.asset], ['首次发现', time(item.opened_at)], ['最近异常', time(item.last_seen)], ['恢复时间', time(item.resolved_at)], ['异常观测次数', item.occurrences], ['事件 ID', item.id]]) { facts.append(el('dt', label), el('dd', value)); }
    $('detail').replaceChildren(title, facts, el('h2', '检查证据'));
    for (const observation of data.observations) {
      const block = el('article', undefined, 'observation');
      block.append(el('strong', states[observation.payload.state] || observation.payload.state), el('p', time(observation.observed_at), 'sub'));
      const evidence = el('details'); evidence.append(el('summary', '查看原始证据 / SQL'), el('pre', JSON.stringify(observation.payload.evidence, null, 2)));
      block.append(evidence, el('p', '观测 ID：' + observation.id, 'sub')); $('detail').append(block);
    }
    const nav = el('nav'), previous = el('button', '上一页证据'), next = el('button', '下一页证据');
    previous.disabled = evidenceOffset === 0; next.disabled = evidenceOffset + data.limit >= data.total;
    previous.onclick = () => detail(id, evidenceOffset - data.limit); next.onclick = () => detail(id, evidenceOffset + data.limit);
    nav.append(previous, el('span', '共 ' + data.total + ' 条观测'), next); $('detail').append(nav);
    for (const button of $('list').querySelectorAll('button')) button.classList.toggle('active', button.dataset.id === id);
  } catch (error) { if (token === detailGeneration) { $('detail').replaceChildren(el('p', '证据读取失败', 'empty')); showError(error); } }
}
async function load() {
  const token = ++generation; ++detailGeneration; selected = null;
  $('error').hidden = true; $('list').replaceChildren(el('p', '读取中…')); $('detail').replaceChildren(el('p', '选择事件以查看证据。', 'empty'));
  $('prev').disabled = $('next').disabled = true;
  try {
    const query = new URLSearchParams({scope: $('scope').value, status: $('status').value, offset: String(offset)});
    const data = await api('/api/incidents?' + query);
    if (token !== generation) return;
    $('list').replaceChildren(); $('count').textContent = '当前筛选：' + data.total + ' 个事件';
    if (!data.items.length) $('list').append(el('p', data.available ? '没有匹配事件。没有事件不等于数据健康，请结合检查记录判断。' : '尚无检查记录。先运行数据管道或故障演示，再刷新此页。', 'empty'));
    for (const item of data.items) {
      const button = el('button', undefined, 'event'); button.dataset.id = item.id;
      const row = el('div', undefined, 'row'); row.append(el('strong', names[item.check_name] || item.check_name), badge(item));
      button.append(row, el('div', scopeName(item.scope) + ' · ' + item.asset, 'sub'), el('div', time(item.opened_at), 'sub'));
      button.onclick = () => detail(item.id); $('list').append(button);
    }
    $('prev').disabled = offset === 0; $('next').disabled = offset + data.limit >= data.total;
    $('page').textContent = data.total ? '第 ' + (offset / data.limit + 1) + ' 页' : '暂无事件';
  } catch (error) { if (token === generation) { $('list').replaceChildren(); $('count').textContent = '读取失败'; showError(error); } }
}
$('scope').onchange = $('status').onchange = () => { offset = 0; load(); };
$('refresh').onclick = () => load();
$('prev').onclick = () => { offset -= 50; load(); };
$('next').onclick = () => { offset += 50; load(); };
load();
