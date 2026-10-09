const dateOptions = {
  month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
};

function timestampDate(value) {
  if (!['number', 'string'].includes(typeof value)) return null;
  const input = typeof value === 'string' ? value.trim() : value;
  if (input === '') return null;
  const date = new Date(typeof input === 'string' && /^[+-]?\d+$/.test(input) ? Number(input) : input);
  const millis = date.getTime();
  return Number.isFinite(millis) && millis > 0 && millis <= 253402300799999 ? date : null;
}

export function formatTimestamp(value, options = {}) {
  const date = timestampDate(value);
  if (!date) return '';
  // Omit timeZone in normal use so the monitor follows this device's local time,
  // matching the Android customer app instead of the server's clock settings.
  const parts = new Intl.DateTimeFormat('en-CA', {...dateOptions, ...options}).formatToParts(date);
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.month}-${values.day} ${values.hour}:${values.minute}:${values.second}`;
}

function renderTime(value, options) {
  const date = timestampDate(value);
  if (!date) return '';
  return `<time datetime="${date.toISOString()}">${formatTimestamp(date.getTime(), options)}</time>`;
}

export function renderMessageTimes(message, options = {}) {
  const inbound = message.direction === 'INBOUND';
  const eventTime = inbound ? message.receivedAt : message.sentAt;
  const event = renderTime(eventTime, options);
  const primary = event || renderTime(message.createdAt, options) || '时间未知';
  const label = event ? (inbound ? '接收时间' : '发送时间') : '创建时间';
  const delivered = renderTime(message.deliveredAt, options);
  return `<span class="message-time">${label}：${primary}</span>`
    + (delivered ? `<span class="message-time">送达时间：${delivered}</span>` : '');
}
