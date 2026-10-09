const $ = id => document.getElementById(id);
const allowed = /\.(png|jpe?g|webp)$/i;
let files = [];
let previewUrl = null;
let comparisonUrl = null;
let jobFiles = [];
let firstSize = null;
let currentJob = null;
let pollTimer = null;
let selectionMode = 'files';

function showMessage(message, error = false) {
  $('plan').textContent = message;
  $('plan').classList.toggle('error', error);
}
function setFiles(inputFiles, mode = 'files') {
  selectionMode = mode;
  const all = Array.from(inputFiles);
  files = all.filter(file => allowed.test(file.name));
  const skipped = all.length - files.length;
  $('selection').textContent = files.length ? `已选择 ${files.length} 张${skipped ? ` · 跳过 ${skipped} 个其他文件` : ''}` : '未找到图片';
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  if (comparisonUrl) URL.revokeObjectURL(comparisonUrl);
  previewUrl = null;
  comparisonUrl = null;
  firstSize = null;
  $('comparison').hidden = true;
  $('source-preview').hidden = true;
  $('empty-state').hidden = !!files.length;
  $('preview').classList.toggle('empty', !files.length);
  $('viewer-meta').textContent = files.length ? files[0].name : '未选择图片';
  $('caption').textContent = files.length ? '原图' : '';
  if (files.length) {
    previewUrl = URL.createObjectURL(files[0]);
    const image = $('source-preview');
    image.onload = () => {
      firstSize = [image.naturalWidth, image.naturalHeight];
      image.hidden = false;
      if (!$('width').value || !$('height').value) {
        $('width').value = image.naturalWidth * 2;
        $('height').value = image.naturalHeight * 2;
      }
      updatePlan();
    };
    image.src = previewUrl;
  } else {
    updatePlan();
  }
}
function estimate(source, target) {
  const [sw, sh] = source, [tw, th] = target;
  const gcd = (a, b) => b ? gcd(b, a % b) : a;
  const divisor = gcd(tw, th);
  const rw = tw / divisor, rh = th / divisor;
  const k = Math.min(Math.floor(sw / rw), Math.floor(sh / rh));
  if (k < 1) return null;
  const crop = [rw * k, rh * k];
  const factor = [1, 2, 4].find(x => crop[0] * x >= tw && crop[1] * x >= th);
  return factor ? { factor, crop } : null;
}
function updatePlan() {
  const w = Number($('width').value), h = Number($('height').value);
  const valid = files.length && Number.isInteger(w) && Number.isInteger(h) && w > 0 && h > 0;
  $('start').disabled = !valid || !!currentJob;
  if (!valid) { showMessage('选择图片后显示预计倍率'); return; }
  if (w * h > 60000000) { showMessage('目标超过 6000 万像素，请调小尺寸。', true); $('start').disabled = true; return; }
  if (!firstSize) { showMessage('读取图片中…'); return; }
  const plan = estimate(firstSize, [w, h]);
  if (!plan) { showMessage('第一张图片需要超过 4 倍放大，或无法按目标比例裁剪。', true); $('start').disabled = true; return; }
  showMessage(`预计 ${plan.factor} 倍放大${files.length > 1 ? ' · 每张图片单独计算' : ''}`);
}
function updateComparison() {
  const position = Number($('compare-slider').value);
  $('result-preview').style.clipPath = `inset(0 ${100 - position}% 0 0)`;
  $('compare-line').style.left = `${position}%`;
}
function fitComparison() {
  const image = $('result-preview');
  if (!image.naturalWidth || !image.naturalHeight) return;
  const bounds = $('preview').getBoundingClientRect();
  const scale = Math.min(bounds.width / image.naturalWidth, bounds.height / image.naturalHeight);
  $('comparison').style.width = `${image.naturalWidth * scale}px`;
  $('comparison').style.height = `${image.naturalHeight * scale}px`;
}
function showComparison(item, index) {
  if (comparisonUrl) URL.revokeObjectURL(comparisonUrl);
  comparisonUrl = URL.createObjectURL(jobFiles[index]);
  $('compare-source').src = comparisonUrl;
  $('result-preview').onload = () => {
    fitComparison();
    $('comparison').hidden = false;
  };
  $('result-preview').src = item.ai;
  $('compare-slider').value = 50;
  updateComparison();
  $('source-preview').hidden = true;
  $('comparison').hidden = true;
  $('viewer-meta').textContent = item.name;
  $('caption').textContent = '拖动分界线，对比原图与 AI 修复';
}
function renderJob(job) {
  $('job-panel').hidden = false;
  $('job-title').textContent = job.status === 'done' ? `处理完成 · ${job.completed}/${job.total}` : `正在处理 · ${job.completed}/${job.total}`;
  $('progress-fill').style.width = `${job.total ? job.completed / job.total * 100 : 0}%`;
  const list = $('job-items');
  list.replaceChildren();
  job.items.forEach((item, index) => {
    const row = document.createElement('div'); row.className = `job-item ${item.status}`;
    const details = document.createElement('div');
    const name = document.createElement('div'); name.className = 'job-item-name'; name.textContent = `${index + 1}. ${item.name}`;
    const note = document.createElement('div'); note.className = 'job-item-note';
    note.textContent = item.status === 'done' ? `${item.source.join(' × ')} · 自动选择 ${item.factor} 倍` :
      item.status === 'error' ? item.error : item.status === 'running' ? '正在进行 AI 处理…' : '等待处理';
    details.append(name, note); row.append(details);
    if (item.status === 'done') {
      const compare = document.createElement('button');
      compare.type = 'button';
      compare.className = 'compare-button';
      compare.textContent = '对比';
      compare.setAttribute('aria-label', `对比 ${item.name} 的处理效果`);
      compare.addEventListener('click', () => showComparison(item, index));
      row.append(compare);
    }
    list.append(row);
  });
  if (job.status === 'done') {
    currentJob = null;
    $('download-all').href = `/api/jobs/${job.id}/download`;
    $('download-all').hidden = !job.items.some(item => item.status === 'done');
    $('download-all').textContent = job.download_kind === 'zip' ? '下载 ZIP ↓' : '下载图片 ↓';
    $('start').disabled = false;
    const firstIndex = job.items.findIndex(item => item.status === 'done');
    const first = job.items[firstIndex];
    if (first) {
      showComparison(first, firstIndex);
    } else {
      showMessage('所有图片处理失败。请查看下方各项错误。', true);
    }
  }
}
async function start() {
  if (!files.length || currentJob) return;
  jobFiles = [...files];
  const form = new FormData();
  jobFiles.forEach(file => form.append('files', file, file.webkitRelativePath || file.name));
  form.append('width', $('width').value); form.append('height', $('height').value);
  form.append('model', document.querySelector('input[name="model"]:checked').value);
  form.append('focus_x', '0.5'); form.append('focus_y', '0.5');
  form.append('selection_mode', selectionMode);
  $('start').disabled = true; $('start').firstChild.textContent = '正在上传 '; showMessage('图片正在上传到本机处理服务…');
  try {
    const response = await fetch('/api/jobs', { method: 'POST', body: form });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || '无法创建任务。');
    currentJob = job.id;
    $('start').firstChild.textContent = '正在处理 ';
    $('download-all').hidden = true;
    renderJob(job);
    pollTimer = setInterval(async () => {
      try {
        const result = await fetch(`/api/jobs/${job.id}`);
        if (!result.ok) throw new Error('无法读取任务进度。');
        const state = await result.json();
        renderJob(state);
        if (state.status === 'done') {
          clearInterval(pollTimer); pollTimer = null;
          $('start').firstChild.textContent = '开始处理 ';
          showMessage(`处理完成：${state.items.filter(item => item.status === 'done').length} / ${state.total} 张成功。`);
          updatePlan();
        }
      } catch (error) {
        clearInterval(pollTimer); pollTimer = null; currentJob = null;
        $('start').firstChild.textContent = '开始处理 '; updatePlan(); showMessage(error.message, true);
      }
    }, 850);
  } catch (error) {
    currentJob = null; $('start').firstChild.textContent = '开始处理 '; updatePlan(); showMessage(error.message, true);
  }
}
$('pick-files').addEventListener('click', () => $('file-input').click());
$('pick-folder').addEventListener('click', () => $('folder-input').click());
$('file-input').addEventListener('change', event => setFiles(event.target.files, 'files'));
$('folder-input').addEventListener('change', event => setFiles(event.target.files, 'folder'));
['width', 'height'].forEach(id => $(id).addEventListener('input', updatePlan));
$('start').addEventListener('click', start);
$('compare-slider').addEventListener('input', updateComparison);
new ResizeObserver(fitComparison).observe($('preview'));
const drop = $('drop-zone');
drop.addEventListener('click', () => $('file-input').click());
drop.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); $('file-input').click(); } });
drop.addEventListener('dragover', event => { event.preventDefault(); drop.classList.add('dragging'); });
drop.addEventListener('dragleave', () => drop.classList.remove('dragging'));
drop.addEventListener('drop', event => { event.preventDefault(); drop.classList.remove('dragging'); setFiles(event.dataTransfer.files); });
