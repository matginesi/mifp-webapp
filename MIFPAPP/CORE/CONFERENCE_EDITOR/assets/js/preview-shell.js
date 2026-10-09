// Imported website code is confined to an opaque sandbox. The shell receives
// content only from the editor window and forwards page navigation back to it.
(() => {
  const frame = document.querySelector('iframe');
  let currentUrl = '';
  let currentHtml = '';
  window.addEventListener('message', event => {
    const data = event.data;
    if (event.source === window.opener && event.origin === location.origin && data?.type === 'mifp-preview-render' && typeof data.html === 'string') {
      if (data.html !== currentHtml) {
        const previous = currentUrl;
        currentHtml = data.html;
        currentUrl = URL.createObjectURL(new Blob([data.html], {type: 'text/html'}));
        frame.src = currentUrl;
        if (previous) setTimeout(() => URL.revokeObjectURL(previous), 2500);
      }
      window.opener.postMessage({type: 'mifp-preview-rendered'}, location.origin);
    } else if (event.source === frame.contentWindow && data?.type === 'mifp-preview-open-page') {
      window.opener?.postMessage({type: data.type, page: String(data.page || ''), project: String(data.project || '')}, location.origin);
    }
  });
  window.opener?.postMessage({type: 'mifp-preview-ready'}, location.origin);
})();
