import assert from 'node:assert/strict';
import test from 'node:test';
import {formatTimestamp, renderMessageTimes} from '../src/message-time.js';

const local = {timeZone: 'America/Vancouver'};
const created = Date.parse('2026-10-08T08:02:03Z');
const received = Date.parse('2026-10-08T07:01:02Z');
const sent = Date.parse('2026-10-08T07:03:04Z');
const delivered = Date.parse('2026-10-08T07:04:05Z');

test('timestamps show month-day and 24-hour time including seconds without a year', () => {
  assert.equal(formatTimestamp(received, local), '10-08 00:01:02');
  assert.equal(formatTimestamp(String(received), local), '10-08 00:01:02');
  assert.equal(formatTimestamp('2026-10-08T07:01:02Z', local), '10-08 00:01:02');
  assert.equal(formatTimestamp(received), formatTimestamp(received, {
    timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  }));
});

test('local dates and daylight saving use the selected device time zone', () => {
  assert.equal(formatTimestamp(Date.parse('2026-10-08T06:59:59Z'), local), '10-07 23:59:59');
  assert.equal(formatTimestamp(Date.parse('2025-11-02T09:01:02Z'), local), '11-02 01:01:02');
  assert.equal(formatTimestamp(Date.parse('2025-11-02T10:01:02Z'), local), '11-02 02:01:02');
});

test('SMS and picture-only MMS both display receive time ahead of record creation time', () => {
  for (const messageType of ['SMS', 'MMS']) {
    const html = renderMessageTimes({direction: 'INBOUND', messageType,
      text: '', attachments: messageType === 'MMS' ? [{id: 'photo'}] : [],
      receivedAt: received, createdAt: created}, local);
    assert.match(html, /接收时间：<time datetime="2026-10-08T07:01:02.000Z">10-08 00:01:02<\/time>/);
    assert.doesNotMatch(html.replace(/<[^>]*>/g, ''), /2026/);
    assert.doesNotMatch(html, /01:02:03/);
  }
});

test('outbound send time and delivery time remain separate', () => {
  const html = renderMessageTimes({direction: 'OUTBOUND', sentAt: sent,
    deliveredAt: delivered, createdAt: created}, local);
  assert.match(html, /发送时间：<time[^>]+>10-08 00:03:04<\/time>/);
  assert.match(html, /送达时间：<time[^>]+>10-08 00:04:05<\/time>/);
  assert.doesNotMatch(html.replace(/<[^>]*>/g, ''), /2026/);
});

test('missing or invalid receive/send times fall back to the original creation time', () => {
  for (const direction of ['INBOUND', 'OUTBOUND']) {
    for (const value of [null, undefined, '', 'bad-date', 0, '0', -1, '-1', Infinity, 253402300800000]) {
      const html = renderMessageTimes({direction, receivedAt: value, sentAt: value,
        createdAt: created}, local);
      assert.match(html, /10-08 01:02:03/);
      assert.match(html, /创建时间：/);
      assert.doesNotMatch(html, /接收时间|发送时间/);
    }
  }
});

test('invalid legacy timestamps cannot stop the chat from rendering', () => {
  for (const value of [null, undefined, '', ' ', 'bad-date', Infinity, false,
    0, '0', -1, '-1', 253402300800000, '253402300800000',
    '1970-01-01T00:00:00Z', '1969-12-31T23:59:59Z', '+010000-01-01T00:00:00Z']) {
    assert.equal(formatTimestamp(value, local), '');
  }
  const html = renderMessageTimes({direction: 'INBOUND', createdAt: 'bad-date', deliveredAt: 'bad-date'}, local);
  assert.match(html, /创建时间：时间未知/);
  assert.doesNotMatch(html, /送达时间|Invalid Date/);
});

test('supported timestamps include the first positive millisecond and end of year 9999', () => {
  assert.equal(formatTimestamp(1, {timeZone: 'UTC'}), '01-01 00:00:00');
  assert.equal(formatTimestamp(253402300799999, {timeZone: 'UTC'}), '12-31 23:59:59');
});
