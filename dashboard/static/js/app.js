function api(path) {
  return fetch(path).then(async function (r) {
    var text = await r.text();
    var data;
    try { data = text ? JSON.parse(text) : {}; } catch (e) {
      if (!r.ok) throw new Error(text || r.statusText || '서버 오류');
      throw e;
    }
    if (!r.ok) throw new Error(data.error || data.detail || text || r.statusText);
    return data;
  });
}
const runResult = document.getElementById('runResult');
const jobList = document.getElementById('jobList');
const btnRun = document.getElementById('btnRun');

function elapsedSec(startedAt) {
  if (!startedAt) return 0;
  return Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000);
}
function renderJob(job) {
  var esc = function(s) { return (s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); };
  var statusClass = job.status === 'running' ? 'running' : job.status === 'done' ? 'done' : job.status === 'cancelled' ? 'cancelled' : 'failed';
  var statusText = job.status === 'running' ? '수집 중 (' + elapsedSec(job.started_at) + '초)' : (job.status === 'cancelled' ? '중단됨' : job.status);
  var status = '<span class="status ' + statusClass + '">' + statusText + '</span>';
  if (job.status === 'running') status += ' <button type="button" class="btn-cancel" data-job-id="' + job.id + '">중단</button>';
  var detail = '<div class="cell-actions">';
  if (job.status === 'done' && job.count != null) {
    detail += '<div class="cell-meta"><span class="path">' + esc(job.out_dir) + '</span> · <span class="count">' + job.count + '장</span></div>';
    if (job.count > 0) detail += '<button type="button" class="btn-sm" data-job-id="' + job.id + '">이미지 보기</button>';
  }
  if (job.status !== 'running') detail += '<a href="/static/log.html?job_id=' + job.id + '" class="btn-sm" target="_blank">로그</a>';
  detail += '<button type="button" class="btn-delete btn-sm" data-job-id="' + job.id + '" title="이력에서만 삭제">삭제</button>';
  if ((job.status === 'failed' || job.status === 'cancelled') && job.error) {
    var errLine = (job.error || '').split('\n')[0].trim().slice(0, 120);
    if ((job.error || '').length > 120) errLine += '…';
    detail += '<div class="error-wrap"><button type="button" class="btn-copy" data-job-id="' + job.id + '">복사</button>';
    detail += '<div class="error-summary">' + esc(errLine) + '</div>';
    detail += '<button type="button" class="btn-error-toggle" data-job-id="' + job.id + '">에러 상세</button>';
    detail += '<div class="error-full">' + esc(job.error) + '</div></div>';
  }
  detail += '</div>';
  return '<tr><td>' + job.id + '</td><td class="query-cell">' + esc(job.query) + '</td><td>' + job.limit + '</td><td class="path-cell">' + esc(job.out_dir) + '</td><td class="status-cell">' + status + detail + '</td></tr>';
}

async function showJobImages(jobId) {
  const modal = document.getElementById('imageModal');
  const title = document.getElementById('modalTitle');
  const grid = document.getElementById('modalGrid');
  const job = await api('/api/jobs/' + jobId);
  title.textContent = '수집 이미지: ' + job.query + ' (' + (job.count || 0) + '장)';
  grid.innerHTML = '로딩 중...';
  modal.classList.add('show');
  try {
    const data = await api('/api/jobs/' + jobId + '/images');
    if (!data.files || data.files.length === 0) {
      grid.innerHTML = '<p class="empty">이미지 없음</p>';
      return;
    }
    grid.innerHTML = data.files.map(f => '<img src="/api/jobs/' + jobId + '/images/' + encodeURIComponent(f) + '" alt="" loading="lazy">').join('');
  } catch (e) {
    grid.innerHTML = '<p class="error">불러오기 실패: ' + e.message + '</p>';
  }
}
document.getElementById('modalClose').onclick = () => document.getElementById('imageModal').classList.remove('show');

document.getElementById('btnClearHistory').onclick = async function() {
  if (!confirm('수집 이력을 모두 삭제할까요?')) return;
  try {
    await fetch('/api/jobs/clear', { method: 'POST' });
    refreshJobs();
  } catch (e) { alert('삭제 실패: ' + e.message); }
};

document.addEventListener('click', async (e) => {
  if (e.target.classList.contains('btn-cancel') && e.target.dataset.jobId) {
    var id = e.target.dataset.jobId;
    e.target.disabled = true;
    e.target.textContent = '중단 중...';
    try {
      await fetch('/api/jobs/' + id + '/cancel', { method: 'POST' });
      refreshJobs();
    } finally {
      e.target.disabled = false;
      e.target.textContent = '중단';
    }
    return;
  }
  if (e.target.classList.contains('btn-delete') && e.target.dataset.jobId) {
    var id = e.target.dataset.jobId;
    var btn = e.target;
    if (!confirm('이 이력을 삭제할까요? (저장된 이미지 파일은 삭제되지 않습니다.)')) return;
    btn.disabled = true;
    fetch('/api/jobs/' + id, { method: 'DELETE' })
      .then(function(r) { return r.text().then(function(t) { var d = {}; try { if (t) d = JSON.parse(t); } catch (e) {} return { ok: r.ok, data: d }; }); })
      .then(function(res) { if (res.ok) refreshJobs(); else { alert('삭제 실패: ' + (res.data.error || res.data.detail || '알 수 없음')); btn.disabled = false; } })
      .catch(function(err) { alert('삭제 실패: ' + (err.message || err)); btn.disabled = false; });
    return;
  }
  if (e.target.classList.contains('btn-sm') && e.target.dataset.jobId && !e.target.classList.contains('btn-copy') && !e.target.classList.contains('btn-delete') && !e.target.closest('a')) showJobImages(e.target.dataset.jobId);
  if (e.target.classList.contains('btn-error-toggle')) {
    var wrap = e.target.closest('.error-wrap');
    if (wrap) {
      wrap.classList.toggle('expanded');
      e.target.textContent = wrap.classList.contains('expanded') ? '접기' : '에러 상세';
    }
    return;
  }
  if (e.target.classList.contains('btn-copy') && e.target.dataset.jobId) {
    var btn = e.target;
    try {
      var job = await api('/api/jobs/' + btn.dataset.jobId);
      await navigator.clipboard.writeText(job.error || '');
      btn.textContent = '복사됨'; btn.classList.add('copied');
      setTimeout(() => { btn.textContent = '복사'; btn.classList.remove('copied'); }, 2000);
    } catch (err) { btn.textContent = '실패'; setTimeout(() => btn.textContent = '복사', 2000); }
  }
});

var refreshInterval = null;
var currentPage = 1;
var perPage = 10;

function refreshJobs(page) {
  if (page != null) currentPage = page;
  var url = '/api/jobs?page=' + currentPage + '&per_page=' + perPage;
  api(url).then(function(data) {
    var list = data.jobs || [];
    var total = data.total || 0;
    var pageNum = data.page || 1;
    var totalPages = Math.max(1, Math.ceil(total / perPage));
    if (total > 0 && list.length === 0 && pageNum > 1) {
      currentPage = 1;
      refreshJobs(1);
      return;
    }
    if (total === 0) {
      jobList.innerHTML = '<p class="empty">아직 수집 이력이 없습니다.</p>';
      stopElapsedTicker();
      return;
    }
    var hasRunning = list.some(function(j) { return j.status === 'running'; });
    var tableHtml = '<table><thead><tr><th>ID</th><th>검색어</th><th>개수</th><th>저장 폴더</th><th>상태 / 이미지 보기</th></tr></thead><tbody>' +
      list.map(renderJob).join('') + '</tbody></table>';
    var paginationHtml = '';
    if (totalPages > 1) {
      var parts = [];
      if (pageNum > 1) parts.push('<button type="button" class="btn-page" data-page="' + (pageNum - 1) + '">이전</button>');
      parts.push('<span class="page-info">' + pageNum + ' / ' + totalPages + ' (총 ' + total + '건)</span>');
      if (pageNum < totalPages) parts.push('<button type="button" class="btn-page" data-page="' + (pageNum + 1) + '">다음</button>');
      paginationHtml = '<div class="pagination">' + parts.join(' ') + '</div>';
    }
    jobList.innerHTML = tableHtml + paginationHtml;
    if (hasRunning && !refreshInterval) startElapsedTicker();
    if (!hasRunning) stopElapsedTicker();
  }).catch(function(e) { jobList.innerHTML = '<p class="empty">이력 불러오기 실패: ' + (e.message || '') + '</p>'; stopElapsedTicker(); });
}

document.addEventListener('click', function(e) {
  if (e.target.classList.contains('btn-page') && e.target.dataset.page) {
    refreshJobs(parseInt(e.target.dataset.page, 10));
  }
});
function startElapsedTicker() { refreshInterval = setInterval(refreshJobs, 1000); }
function stopElapsedTicker() { if (refreshInterval) { clearInterval(refreshInterval); refreshInterval = null; } }

btnRun.addEventListener('click', async () => {
  const query = document.getElementById('query').value.trim();
  const limit = parseInt(document.getElementById('limit').value, 10) || 20;
  const out_dir = document.getElementById('out_dir').value.trim() || 'data/naver_collected';
  if (!query) { runResult.style.display = 'block'; runResult.innerHTML = '<span class="error">검색어를 입력하세요.</span>'; return; }

  btnRun.disabled = true;
  runResult.style.display = 'block';
  runResult.innerHTML = '작업 시작 중...';

  try {
    const res = await fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, limit, out_dir })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '실패');
    runResult.innerHTML = '작업 ID: <strong>' + data.job_id + '</strong> — 수집 중...';
    refreshJobs();
    const jobId = data.job_id;
    const startTime = Date.now();
    const interval = setInterval(async () => {
      const job = await api('/api/jobs/' + jobId);
      var sec = Math.floor((Date.now() - startTime) / 1000);
      runResult.innerHTML = '작업 ID: <strong>' + jobId + '</strong> — 수집 중 (' + sec + '초)';
      if (job.status !== 'running') {
        clearInterval(interval);
        btnRun.disabled = false;
        if (job.status === 'done') runResult.innerHTML = '완료! 저장: <span class="path">' + job.out_dir + '</span>, 수집: <span class="count">' + job.count + '장</span>';
        else if (job.status === 'cancelled') runResult.innerHTML = '중단됨.';
        else runResult.innerHTML = '실패: ' + (job.error || '').substring(0, 200);
        refreshJobs();
      }
    }, 1000);
  } catch (e) {
    runResult.innerHTML = '<span class="error">' + e.message + '</span>';
    btnRun.disabled = false;
  }
});

refreshJobs();
