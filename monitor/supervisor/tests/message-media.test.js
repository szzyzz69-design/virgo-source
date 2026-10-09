import assert from 'node:assert/strict';
import test from 'node:test';
import {renderAttachments} from '../src/message-media.js';

const photo = {url: '/supervisor/api/conversations/conv/attachments/photo?account_id=account',
  contentType: 'image/jpeg', name: 'moon.jpg'};

test('MMS renders a picture and an authenticated original-image link', () => {
  const html = renderAttachments([photo]);
  assert.match(html, /<img /);
  assert.match(html, /点击查看原图/);
  assert.match(html, /loading="lazy"/);
  assert.match(html, /rel="noopener noreferrer"/);
});

test('attachment filenames are escaped and active content is not displayed inline', () => {
  const html = renderAttachments([{...photo, name: '<img onerror="alert(1)">', contentType: 'image/svg+xml'}]);
  assert.doesNotMatch(html, /<img /);
  assert.match(html, /&lt;img onerror=&quot;/);
});

test('only the authenticated monitor attachment route can load images', () => {
  for (const url of ['javascript:alert(1)', '//other.example/a.jpg', 'https://other.example/a.jpg', '/admin/db']) {
    assert.equal(renderAttachments([{...photo, url}]), '');
  }
  assert.equal(renderAttachments(), '');
});
