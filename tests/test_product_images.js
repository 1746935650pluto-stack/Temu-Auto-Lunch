const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'live-editor.js'), 'utf8');

test('editing and adding images beyond ten never truncates existing URLs', () => {
  const images = Array.from({ length: 50 }, (_, i) => `https://example.com/${i}.jpg`);
  const input = { value: images.join('\n') };
  const newImage = { value: 'https://example.com/new.jpg', focus() {} };
  const notices = [];
  const context = vm.createContext({
    editEl: id => id === 'editImages' ? input : newImage,
    renderProductImagePreview() {}, setEditorStage() {}, showToast: message => notices.push(message),
  });
  vm.runInContext(source.slice(source.indexOf('function getProductImageUrls()'), source.indexOf('function setEditorStage(')), context);
  assert.deepEqual(Array.from(vm.runInContext('getProductImageUrls()', context)), images);
  vm.runInContext("const edited = getProductImageUrls(); edited[20] = 'https://example.com/replaced.jpg'; setProductImageUrls(edited); addProductImage();", context);
  const saved = input.value.split('\n');
  assert.equal(saved.length, 51);
  assert.equal(saved[20], 'https://example.com/replaced.jpg');
  assert.equal(saved[49], images[49]);
  assert.equal(saved[50], 'https://example.com/new.jpg');
  assert.equal(notices.length, 0);
  newImage.value = 'javascript:alert(1)';
  vm.runInContext('addProductImage()', context);
  assert.equal(input.value.split('\n').length, 51);
  assert.equal(notices.length, 1);
});
