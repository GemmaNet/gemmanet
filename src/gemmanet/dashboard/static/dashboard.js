// Served as a file, not inline, so the page's Content-Security-Policy can
// forbid inline scripts (the API key lives in localStorage).
const BASE = document.currentScript.dataset.coordinatorUrl;

// Everything nodes report (names, capabilities, ...) is untrusted: escape it
// before it goes anywhere near innerHTML.
function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
}

// Persist API key in localStorage
const apiKeyInput = document.getElementById('api-key');
apiKeyInput.value = localStorage.getItem('gemmanet_api_key') || '';
apiKeyInput.addEventListener('input', () => {
    localStorage.setItem('gemmanet_api_key', apiKeyInput.value);
});

function getHeaders() {
    const key = apiKeyInput.value.trim();
    const h = { 'Content-Type': 'application/json' };
    if (key) h['Authorization'] = 'Bearer ' + key;
    return h;
}

async function loadStatus() {
    try {
        const r = await fetch(BASE + '/api/v1/status');
        const d = await r.json();
        document.getElementById('m-nodes').textContent = d.online_nodes;
        document.getElementById('m-tasks').textContent = d.total_tasks_today;
        document.getElementById('m-version').textContent = d.version;
    } catch(e) {
        console.error('Status error:', e);
    }
}

async function loadNodes() {
    try {
        const r = await fetch(BASE + '/api/v1/nodes');
        const nodes = await r.json();
        const tbody = document.getElementById('nodes-body');
        if (nodes.length === 0) {
            tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No nodes online</td></tr>';
            return;
        }
        // Fetch reputation and benchmark for each node
        const rows = await Promise.all(nodes.map(async n => {
            let repScore = '-';
            let repClass = 'rep-mid';
            let speedText = '-';
            let benchIcon = '-';
            try {
                const rr = await fetch(BASE + '/api/v1/reputation/' + encodeURIComponent(n.node_id || ''));
                const rd = await rr.json();
                repScore = Number(rd.score);
                repClass = repScore > 70 ? 'rep-high' : repScore >= 40 ? 'rep-mid' : 'rep-low';
            } catch(e) {}
            try {
                const br = await fetch(BASE + '/api/v1/benchmark/' + encodeURIComponent(n.node_id || ''));
                const bd = await br.json();
                if (bd.benchmark) {
                    speedText = Number(bd.benchmark.estimated_tokens_per_sec) + ' t/s';
                    benchIcon = bd.benchmark.benchmark_passed
                        ? '<span style="color:#4ade80">PASS</span>'
                        : '<span style="color:#f87171">FAIL</span>';
                }
            } catch(e) {}
            return `<tr>
                <td>${esc(n.name || n.node_id)}</td>
                <td title="official: run by this network's operator; community: anyone else (they can see the requests they serve)">${esc(n.trust || 'community')}</td>
                <td>${esc((n.capabilities || []).join(', '))}</td>
                <td>${esc((n.languages || []).join(', '))}</td>
                <td class="${repClass}">${esc(repScore)}</td>
                <td>${esc(speedText)}</td>
                <td>${benchIcon}</td>
                <td class="status-online">● Online</td>
            </tr>`;
        }));
        tbody.innerHTML = rows.join('');
    } catch(e) {
        console.error('Nodes error:', e);
    }
}

async function loadLeaderboard() {
    try {
        const r = await fetch(BASE + '/api/v1/leaderboard?limit=10');
        const lb = await r.json();
        const tbody = document.getElementById('lb-body');
        if (lb.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="empty-state">No reputation data yet</td></tr>';
            return;
        }
        tbody.innerHTML = lb.map((n, i) => `<tr>
            <td>${i + 1}</td>
            <td>${esc(n.name || n.node_id)}</td>
            <td class="${n.score > 70 ? 'rep-high' : n.score >= 40 ? 'rep-mid' : 'rep-low'}">${esc(n.score)}</td>
            <td>${esc(n.total_tasks)}</td>
            <td>${(Number(n.success_rate) * 100).toFixed(1)}%</td>
            <td>${esc(n.avg_response_ms)}ms</td>
        </tr>`).join('');
    } catch(e) {
        console.error('Leaderboard error:', e);
    }
}

async function submitTask() {
    const key = apiKeyInput.value.trim();
    if (!key) { alert('Please enter an API key'); return; }
    const btn = document.getElementById('submit-btn');
    const area = document.getElementById('result-area');
    btn.disabled = true;
    area.style.display = 'block';
    area.textContent = 'Processing...';

    const taskType = document.getElementById('task-type').value;
    const content = document.getElementById('task-content').value;
    let params = {};
    try {
        const raw = document.getElementById('task-params').value.trim();
        if (raw) params = JSON.parse(raw);
    } catch(e) {
        area.textContent = 'Error: Invalid JSON in params';
        btn.disabled = false;
        return;
    }

    try {
        const r = await fetch(BASE + '/api/v1/request', {
            method: 'POST',
            headers: getHeaders(),
            body: JSON.stringify({ task_type: taskType, content, params })
        });
        const d = await r.json();
        if (!r.ok) {
            area.textContent = 'Error: ' + (typeof d.detail === 'string' ? d.detail : JSON.stringify(d));
        } else {
            area.textContent = `Status: ${d.status}\nResult: ${d.result}\nNode: ${d.node_id || 'N/A'}\nTime: ${d.processing_time_ms}ms`;
        }
    } catch(e) {
        area.textContent = 'Error: ' + e.message;
    }
    btn.disabled = false;
}

// Initial load
loadStatus();
loadNodes();
loadLeaderboard();

// Auto-refresh every 10 seconds
setInterval(() => { loadStatus(); loadNodes(); }, 10000);
// Refresh leaderboard every 30 seconds
setInterval(loadLeaderboard, 30000);

// Feedback modal
function openFeedback() { document.getElementById('fb-overlay').classList.add('active'); }
function closeFeedback() {
    document.getElementById('fb-overlay').classList.remove('active');
    document.getElementById('fb-msg').textContent = '';
}

async function submitFeedback() {
    const msg = document.getElementById('fb-message').value.trim();
    if (!msg) { showFbMsg('Please enter a message', 'error'); return; }
    const payload = {
        type: document.getElementById('fb-type').value,
        message: msg,
        email: document.getElementById('fb-email').value.trim() || null,
    };
    const headers = { 'Content-Type': 'application/json' };
    const key = apiKeyInput.value.trim();
    if (key) headers['Authorization'] = 'Bearer ' + key;

    try {
        const r = await fetch(BASE + '/api/v1/feedback', {
            method: 'POST', headers, body: JSON.stringify(payload),
        });
        if (!r.ok) { const d = await r.json(); showFbMsg(d.detail || 'Error', 'error'); return; }
        showFbMsg('Thank you for your feedback!', 'success');
        document.getElementById('fb-message').value = '';
        document.getElementById('fb-email').value = '';
        setTimeout(closeFeedback, 2000);
    } catch(e) {
        showFbMsg('Network error: ' + e.message, 'error');
    }
}

function showFbMsg(text, type) {
    const el = document.getElementById('fb-msg');
    el.textContent = text;
    el.className = 'fb-msg ' + type;
}

document.getElementById('submit-btn').addEventListener('click', submitTask);
document.getElementById('feedback-open').addEventListener('click', openFeedback);
document.getElementById('feedback-close').addEventListener('click', closeFeedback);
document.getElementById('feedback-submit').addEventListener('click', submitFeedback);
