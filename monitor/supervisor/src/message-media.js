const esc = value => String(value ?? '').replace(/[&<>"']/g, character => (
  {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]
));
const attachmentPath = /^\/supervisor\/api\/conversations\/[^/?#]+\/attachments\/[^/?#]+\?account_id=[^&#]+$/;
const imageTypes = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp']);

export function renderAttachments(attachments = []) {
  return attachments.map(attachment => {
    if (!attachmentPath.test(attachment.url || '')) return '';
    const url = esc(attachment.url);
    const name = esc(attachment.name || '彩信附件');
    const image = imageTypes.has(attachment.contentType)
      ? `<img class="message-image" src="${url}" alt="${name}" loading="lazy">`
      : '';
    return `<a class="message-attachment" href="${url}" target="_blank" rel="noopener noreferrer">${image}<span class="attachment-fallback">${image ? '点击查看原图' : name}</span></a>`;
  }).join('');
}
