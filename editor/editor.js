'use strict';
const $ = id => document.getElementById(id);
let session, draft, dirty = false, busy = false, importing = false, noticeTimer;
const categories = {cycling:'자전거', swimming:'수영', making:'만드는 일'};
const messages = {
  'Unreviewed record source':'원문은 네이버 블로그·티스토리·디시인사이드의 HTTPS 주소를 입력해 주세요.',
  'Record needs a title':'제목을 입력해 주세요.',
  'Record needs a body':'본문이나 사진을 추가해 주세요.',
  'Record date must be YYYY-MM-DD':'작성일을 확인해 주세요.',
};

function node(tag, text, cls) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (cls) element.className = cls;
  return element;
}
function notice(text, error = false) {
  clearTimeout(noticeTimer);
  $('notice').textContent = messages[text] || text;
  $('notice').className = error ? 'notice error' : 'notice';
  $('notice').hidden = false;
  noticeTimer = setTimeout(() => { $('notice').hidden = true; }, error ? 12000 : 7000);
}
async function api(path, data, file) {
  const options = {credentials:'same-origin', cache:'no-store'};
  if (data !== undefined || file) {
    options.method = 'POST';
    options.headers = {'X-Editor-Token':session.token, 'Content-Type':file ? file.type || 'application/octet-stream' : 'application/json'};
    options.body = file || JSON.stringify(data);
  }
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '작업을 마치지 못했습니다.');
  return result;
}
function changed() {
  dirty = true;
  $('save-state').textContent = '저장하지 않은 변경 사항';
}
async function run(action) {
  if (busy) return;
  busy = true;
  document.querySelectorAll('button,input,textarea,select').forEach(control => { control.disabled = true; });
  try { await action(); }
  catch (error) { notice(error.message, true); }
  finally {
    busy = false;
    document.querySelectorAll('button,input,textarea,select').forEach(control => { control.disabled = false; });
    refreshBlockButtons();
  }
}
function mayLeave() {
  return !dirty || window.confirm('저장하지 않은 변경 사항이 있습니다. 저장하지 않고 다른 글을 열까요?');
}
async function loadList() {
  const list = await api('/api/posts');
  $('post-list').replaceChildren();
  if (!list.length) $('post-list').append(node('p','아직 저장한 글이 없습니다.','sidebar-empty'));
  for (const post of list) {
    const button = node('button', undefined, 'post-card');
    button.type = 'button';
    if (draft && post.id === draft.entry.id) button.setAttribute('aria-current','true');
    button.append(node('strong',post.title),node('small',`${post.date} · ${categories[post.category]}`),node('span',post.status,'post-status'));
    button.addEventListener('click',() => { if (mayLeave()) run(async () => openPost(await api('/api/posts/'+post.id))); });
    $('post-list').append(button);
  }
}
function openPost(value) {
  draft = value;
  dirty = false;
  $('empty').hidden = true;
  $('post-form').hidden = false;
  for (const field of ['title','category','date']) $(field).value = draft.entry[field];
  $('source-url').value = draft.entry.source?.url || '';
  $('source-label').value = draft.entry.source?.label || '';
  $('save-state').textContent = '맥에 저장됨';
  $('import-summary').hidden = !draft.import_info;
  $('import-summary').textContent = draft.import_info ? `${draft.entry.source?.label || '원문'}에서 가져온 초안 · 원문 이미지 ${draft.import_info.image_count}장. 본문과 사진 순서를 확인해 주세요.` : '';
  renderBlocks();
  loadList().catch(error => notice(error.message,true));
}
function refreshBlockButtons() {
  const blocks = [...$('blocks').children];
  blocks.forEach((block,index) => {
    block.querySelector('[data-move="up"]').disabled = busy || index === 0;
    block.querySelector('[data-move="down"]').disabled = busy || index === blocks.length - 1;
  });
}
function renderBlocks() {
  $('blocks').replaceChildren();
  draft.entry.body.forEach((block,index) => {
    const isImage = typeof block !== 'string';
    const element = node('section', undefined, isImage ? 'block image-block' : 'block');
    element.setAttribute('aria-label',`${index+1}번째 ${isImage ? '사진' : '글'}`);
    const controls = node('div', undefined, 'block-controls');
    controls.append(node('span',`${index+1}. ${isImage ? '사진' : '글'}`));
    const buttons = node('div', undefined, 'block-buttons');
    for (const [label,direction,offset] of [['위로','up',-1],['아래로','down',1]]) {
      const button = node('button',label);
      button.type = 'button';
      button.dataset.move = direction;
      button.setAttribute('aria-label',`${index+1}번째 ${isImage ? '사진' : '글'} ${label}`);
      button.addEventListener('click',() => {
        const target = index + offset;
        [draft.entry.body[index],draft.entry.body[target]] = [draft.entry.body[target],draft.entry.body[index]];
        changed(); renderBlocks();
      });
      buttons.append(button);
    }
    const remove = node('button','빼기');
    remove.type = 'button';
    remove.setAttribute('aria-label',`${index+1}번째 ${isImage ? '사진' : '글'} 본문에서 빼기`);
    remove.addEventListener('click',() => { draft.entry.body.splice(index,1); changed(); renderBlocks(); });
    buttons.append(remove); controls.append(buttons); element.append(controls);
    if (isImage) {
      const image = node('img'); image.src = block.src; image.alt = block.alt || '';
      element.append(image);
      for (const [key,label,placeholder] of [['caption','사진 설명','사진 아래에 표시할 설명 (선택)'],['alt','대체 텍스트','사진을 보지 못할 때 전달할 설명 (선택)']]) {
        const wrapper = node('label',label);
        const input = node('input'); input.value = block[key] || ''; input.maxLength = 2000; input.placeholder = placeholder;
        input.addEventListener('input',() => { block[key] = input.value; changed(); });
        wrapper.append(input); element.append(wrapper);
      }
    } else {
      const text = node('textarea');
      text.value = block; text.placeholder = '글을 쓰거나 다른 곳에 쓴 본문을 붙여넣으세요.';
      text.setAttribute('aria-label',`${index+1}번째 글 본문`);
      text.addEventListener('input',() => { draft.entry.body[index] = text.value; changed(); });
      element.append(text);
    }
    $('blocks').append(element);
  });
  refreshBlockButtons();
}
function entryFromForm() {
  const entry = {...draft.entry};
  for (const field of ['title','category','date']) entry[field] = $(field).value;
  const url = $('source-url').value.trim();
  if (url) entry.source = {url, label:$('source-label').value.trim() || '원문'};
  else delete entry.source;
  return entry;
}
async function save() {
  const entry = entryFromForm();
  draft = await api('/api/posts/'+entry.id+'/save',{entry, revision:draft.revision});
  dirty = false;
  renderBlocks();
  $('save-state').textContent = '맥에 저장됨 · '+new Date().toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'});
  await loadList();
}
async function newPost() {
  if (!mayLeave()) return;
  await run(async () => { openPost(await api('/api/posts',{})); $('title').focus(); });
}
async function review(prepare) {
  await run(async () => {
    await save();
    $('review-title').textContent = prepare ? '공개본에 넣기 전 확인' : '미리보기';
    $('review-note').textContent = prepare ? '이 글과 본문에 넣은 사진만 공개본에 포함합니다. 서버 반영은 별도입니다.' : '저장한 초안의 실제 페이지 모습입니다.';
    $('review-result').textContent = '';
    $('confirm-prepare').hidden = !prepare;
    $('preview-frame').src = '/preview/'+draft.entry.id+'/';
    $('review').showModal();
  });
}
async function upload(files) {
  if (!files.length) return;
  await run(async () => {
    await save();
    try {
      for (const file of files) {
        if (file.size > 20_000_000) throw new Error('사진은 한 장에 20MB까지 추가할 수 있습니다.');
        $('save-state').textContent = '사진을 준비하고 있습니다…';
        draft.entry.body.push(await api('/api/posts/'+draft.entry.id+'/images',undefined,file));
        changed();
      }
    } finally {
      renderBlocks();
      if (dirty) await save();
    }
    notice('사진을 추가했습니다. 위아래로 옮겨 본문 사이에 배치할 수 있습니다.');
  });
}
$('new-post').addEventListener('click',newPost);
$('first-post').addEventListener('click',newPost);
function openImport(url = '') {
  $('import-url').value = url;
  $('import-category').value = draft ? $('category').value : 'cycling';
  $('import-status').textContent = '';
  $('import-dialog').showModal();
  $('import-url').focus();
}
$('import-post').addEventListener('click',() => openImport());
$('import-source').addEventListener('click',() => openImport($('source-url').value.trim()));
$('close-import').addEventListener('click',() => $('import-dialog').close());
$('import-dialog').addEventListener('cancel',event => { if (importing) event.preventDefault(); });
$('import-form').addEventListener('submit',event => {
  event.preventDefault();
  const url = $('import-url').value.trim();
  const category = $('import-category').value;
  run(async () => {
    importing = true;
    $('import-status').textContent = '본문과 사진을 가져오고 있습니다. 사진 수에 따라 잠시 걸릴 수 있습니다…';
    try {
      if (draft && dirty) await save();
      const result = await api('/api/import',{url,category});
      openPost(result.draft);
      $('import-dialog').close();
      notice(result.duplicate ? '이미 가져온 글을 열었습니다. 기존 초안은 유지했습니다.' : `본문과 사진 ${result.draft.import_info.image_count}장을 새 초안으로 저장했습니다.`);
    } catch (error) {
      $('import-status').textContent = messages[error.message] || error.message;
      throw error;
    } finally { importing = false; }
  });
});
$('post-form').addEventListener('submit',event => { event.preventDefault(); run(async () => { await save(); notice('초안을 맥에 저장했습니다.'); }); });
for (const field of ['title','category','date','source-url','source-label']) $(field).addEventListener('input',changed);
$('add-text').addEventListener('click',() => { draft.entry.body.push(''); changed(); renderBlocks(); $('blocks').lastElementChild.querySelector('textarea').focus(); });
$('add-image').addEventListener('click',() => $('image-file').click());
$('image-file').addEventListener('change',async () => { const files = [...$('image-file').files]; $('image-file').value = ''; await upload(files); });
$('post-form').addEventListener('paste',event => {
  const images = [...event.clipboardData.files].filter(file => file.type.startsWith('image/'));
  if (images.length) { event.preventDefault(); upload(images); }
});
$('preview').addEventListener('click',() => review(false));
$('prepare').addEventListener('click',() => review(true));
$('close-review').addEventListener('click',() => $('review').close());
$('confirm-prepare').addEventListener('click',() => run(async () => {
  if (!draft.entry.title.trim()) throw new Error('제목을 입력해 주세요.');
  $('review-result').textContent = '글과 사진을 검사하고 공개본을 준비하고 있습니다…';
  try {
    const result = await api('/api/posts/'+draft.entry.id+'/prepare',{revision:draft.revision});
    $('review-result').textContent = result.message;
    $('confirm-prepare').hidden = true;
    await loadList();
    notice(result.message);
  } catch (error) { $('review-result').textContent = messages[error.message] || error.message; throw error; }
}));
window.addEventListener('beforeunload',event => { if (dirty || importing) { event.preventDefault(); event.returnValue = ''; } });
(async () => {
  try { session = await api('/api/session'); await loadList(); }
  catch (error) { notice('편집기에 연결하지 못했습니다. 실행한 터미널과 주소를 확인해 주세요.',true); }
})();
