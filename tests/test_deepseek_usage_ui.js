const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../app.js'), 'utf8');
function setup() {
  const nodes = new Map();
  const $ = id => {
    if (!nodes.has(id)) nodes.set(id, {});
    return nodes.get(id);
  };
  const context = vm.createContext({ $, loadDeepSeekSettings() {}, updateDeepSeekControls() {} });
  vm.runInContext(source.slice(source.indexOf('function renderDeepSeekSettings('), source.indexOf('async function loadDeepSeekSettings(')), context);
  return { context, $ };
}
test('usage summary distinguishes measured tokens and missing usage', () => {
  const {context, $} = setup();
  context.renderDeepSeekSettings({configured: true, model: 'deepseek-flash', usageToday: {
    requests: 3, knownUsageRequests: 2, totalTokens: 900, promptTokens: 700, completionTokens: 200,
    cacheHitTokens: 128, byPurpose: {title_initial: 1, title_repair: 1, attribute: 1}, recordErrors: 0
  }});
  const text = $('#deepSeekModel').textContent;
  assert.match(text, /3 次请求、900 tokens/);
  assert.match(text, /修正 1 次/);
  assert.match(text, /实际消耗以DeepSeek账单为准/);
});
test('older server with no usage still renders configuration', () => {
  const {context, $} = setup();
  context.renderDeepSeekSettings({configured: false});
  assert.equal($('#deepSeekConfigured').textContent, '未配置');
  assert.doesNotMatch($('#deepSeekModel').textContent, /今日记录/);
});
