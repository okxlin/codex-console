/**
 * 注册页面 JavaScript
 * 使用 utils.js 中的工具库
 */

// 状态
let currentTask = null;
let currentBatch = null;
let logPollingInterval = null;
let batchPollingInterval = null;
let autoMonitorPollingInterval = null;
let accountsPollingInterval = null;
let todayStatsPollingInterval = null;
let todayStatsResetInterval = null;
let isBatchMode = false;
let isOutlookBatchMode = false;
let isAutoMode = false;
let outlookAccounts = [];
let taskCompleted = false;  // 标记任务是否已完成
let batchCompleted = false;  // 标记批量任务是否已完成
let taskFinalStatus = null;  // 保存任务的最终状态
let batchFinalStatus = null;  // 保存批量任务的最终状态
let displayedLogs = new Set();  // 用于日志去重
let toastShown = false;  // 标记是否已显示过 toast
let availableServices = {
    tempmail: { available: true, services: [] },
    yyds_mail: { available: false, services: [] },
    outlook: { available: false, services: [] },
    moe_mail: { available: false, services: [] },
    temp_mail: { available: false, services: [] },
    codex_otp: { available: false, services: [] },
    codex_otp_d1: { available: false, services: [] },
    duck_mail: { available: false, services: [] },
    freemail: { available: false, services: [] }
};

// WebSocket 相关变量
let webSocket = null;
let batchWebSocket = null;  // 批量任务 WebSocket
let useWebSocket = true;  // 是否使用 WebSocket
let wsHeartbeatInterval = null;  // 心跳定时器
let batchWsHeartbeatInterval = null;  // 批量任务心跳定时器
let activeTaskUuid = null;   // 当前活跃的单任务 UUID（用于页面重新可见时重连）
let activeBatchId = null;    // 当前活跃的批量任务 ID（用于页面重新可见时重连）
let wsReconnectTimer = null;
let batchWsReconnectTimer = null;
let wsReconnectAttempts = 0;
let batchWsReconnectAttempts = 0;
let wsManualClose = false;
let batchWsManualClose = false;
let autoMonitorLastLogIndex = 0;
let historyTasks = [];
let selectedHistoryTaskUuid = null;
let inspectingHistoryTask = false;

const WS_RECONNECT_BASE_DELAY = 1000;
const WS_RECONNECT_MAX_DELAY = 10000;

// DOM 元素
const elements = {
    form: document.getElementById('registration-form'),
    emailService: document.getElementById('email-service'),
    emailServiceGroup: document.getElementById('email-service')?.closest('.form-group'),
    regMode: document.getElementById('reg-mode'),
    regModeGroup: document.getElementById('reg-mode-group'),
    batchCountGroup: document.getElementById('batch-count-group'),
    batchCount: document.getElementById('batch-count'),
    batchOptions: document.getElementById('batch-options'),
    intervalMin: document.getElementById('interval-min'),
    intervalMax: document.getElementById('interval-max'),
    startBtn: document.getElementById('start-btn'),
    cancelBtn: document.getElementById('cancel-btn'),
    taskStatusRow: document.getElementById('task-status-row'),
    batchProgressSection: document.getElementById('batch-progress-section'),
    consoleLog: document.getElementById('console-log'),
    clearLogBtn: document.getElementById('clear-log-btn'),
    // 任务状态
    taskId: document.getElementById('task-id'),
    taskEmail: document.getElementById('task-email'),
    taskStatus: document.getElementById('task-status'),
    taskService: document.getElementById('task-service'),
    taskScheme: document.getElementById('task-scheme'),
    playwrightDiagnostics: document.getElementById('playwright-diagnostics'),
    playwrightStage: document.getElementById('playwright-stage'),
    playwrightStrategy: document.getElementById('playwright-strategy'),
    playwrightFailure: document.getElementById('playwright-failure'),
    playwrightDiagnosis: document.getElementById('playwright-diagnosis'),
    playwrightDiagnosisHint: document.getElementById('playwright-diagnosis-hint'),
    playwrightAction: document.getElementById('playwright-action'),
    playwrightStrategyFlags: document.getElementById('playwright-strategy-flags'),
    playwrightPostFailureStrategy: document.getElementById('playwright-post-failure-strategy'),
    playwrightNextRunPolicy: document.getElementById('playwright-next-run-policy'),
    playwrightTokens: document.getElementById('playwright-tokens'),
    playwrightBrowserProbe: document.getElementById('playwright-browser-probe'),
    playwrightPageState: document.getElementById('playwright-page-state'),
    playwrightPageTitle: document.getElementById('playwright-page-title'),
    playwrightIpify: document.getElementById('playwright-ipify'),
    playwrightRefresh: document.getElementById('playwright-refresh'),
    playwrightCallback: document.getElementById('playwright-callback'),
    playwrightCurrentUrl: document.getElementById('playwright-current-url'),
    playwrightPath: document.getElementById('playwright-path'),
    playwrightArtifact: document.getElementById('playwright-artifact'),
    taskStatusBadge: document.getElementById('task-status-badge'),
    autoMonitorStatusBadge: document.getElementById('auto-monitor-status-badge'),
    autoMonitorLastChecked: document.getElementById('auto-monitor-last-checked'),
    autoMonitorPlaywrightSummary: document.getElementById('auto-monitor-playwright-summary'),
    taskLastChecked: document.getElementById('task-last-checked'),
    taskInventory: document.getElementById('task-inventory'),
    // 批量状态
    batchProgressText: document.getElementById('batch-progress-text'),
    batchProgressPercent: document.getElementById('batch-progress-percent'),
    progressBar: document.getElementById('progress-bar'),
    batchSuccess: document.getElementById('batch-success'),
    batchFailed: document.getElementById('batch-failed'),
    batchRemaining: document.getElementById('batch-remaining'),
    // 已注册账号
    recentAccountsTable: document.getElementById('recent-accounts-table'),
    refreshAccountsBtn: document.getElementById('refresh-accounts-btn'),
    historyTasksTable: document.getElementById('history-tasks-table'),
    historyTaskFilter: document.getElementById('history-task-filter'),
    refreshHistoryBtn: document.getElementById('refresh-history-btn'),
    restoreActiveTaskBtn: document.getElementById('restore-active-task-btn'),
    artifactPreviewOverlay: document.getElementById('artifact-preview-overlay'),
    artifactPreviewImage: document.getElementById('artifact-preview-image'),
    artifactPreviewMeta: document.getElementById('artifact-preview-meta'),
    artifactPreviewDownload: document.getElementById('artifact-preview-download'),
    artifactPreviewClose: document.getElementById('artifact-preview-close'),
    // 今日统计
    todayStatsTotal: document.getElementById('today-stats-total'),
    todayStatsSuccess: document.getElementById('today-stats-success'),
    todayStatsFailed: document.getElementById('today-stats-failed'),
    todayStatsRate: document.getElementById('today-stats-rate'),
    todayStatsReset: document.getElementById('today-stats-reset'),
    pwStatsSamples: document.getElementById('pw-stats-samples'),
    pwStatsRotate: document.getElementById('pw-stats-rotate'),
    pwStatsFingerprint: document.getElementById('pw-stats-fingerprint'),
    pwStatsThrottle: document.getElementById('pw-stats-throttle'),
    pwStatsDiagnosis: document.getElementById('pw-stats-diagnosis'),
    pwStatsAlerts: document.getElementById('pw-stats-alerts'),
    // Outlook 批量注册
    outlookBatchSection: document.getElementById('outlook-batch-section'),
    outlookAccountsContainer: document.getElementById('outlook-accounts-container'),
    outlookIntervalMin: document.getElementById('outlook-interval-min'),
    outlookIntervalMax: document.getElementById('outlook-interval-max'),
    outlookSkipRegistered: document.getElementById('outlook-skip-registered'),
    outlookConcurrencyMode: document.getElementById('outlook-concurrency-mode'),
    outlookConcurrencyCount: document.getElementById('outlook-concurrency-count'),
    outlookConcurrencyHint: document.getElementById('outlook-concurrency-hint'),
    outlookIntervalGroup: document.getElementById('outlook-interval-group'),
    // 批量并发控件
    concurrencyMode: document.getElementById('concurrency-mode'),
    concurrencyCount: document.getElementById('concurrency-count'),
    concurrencyHint: document.getElementById('concurrency-hint'),
    intervalGroup: document.getElementById('interval-group'),
    // 注册后自动操作
    autoUploadCpa: document.getElementById('auto-upload-cpa'),
    cpaServiceSelectGroup: document.getElementById('cpa-service-select-group'),
    cpaServiceSelect: document.getElementById('cpa-service-select'),
    autoUploadSub2api: document.getElementById('auto-upload-sub2api'),
    sub2apiServiceSelectGroup: document.getElementById('sub2api-service-select-group'),
    sub2apiServiceSelect: document.getElementById('sub2api-service-select'),
    autoUploadTm: document.getElementById('auto-upload-tm'),
    tmServiceSelectGroup: document.getElementById('tm-service-select-group'),
    tmServiceSelect: document.getElementById('tm-service-select'),
    autoRegistrationSection: document.getElementById('auto-registration-section'),
    autoRegistrationEnabled: document.getElementById('auto-registration-enabled'),
    autoRegistrationCheckInterval: document.getElementById('auto-registration-check-interval'),
    autoRegistrationMinReady: document.getElementById('auto-registration-min-ready'),
    autoRegistrationCpaServiceId: document.getElementById('auto-registration-cpa-service-id'),
    autoRegistrationEmailServiceType: document.getElementById('auto-registration-email-service-type'),
    autoRegistrationEmailServiceId: document.getElementById('auto-registration-email-service-id'),
    autoRegistrationProxy: document.getElementById('auto-registration-proxy'),
    autoRegistrationMode: document.getElementById('auto-registration-mode'),
    autoRegistrationConcurrency: document.getElementById('auto-registration-concurrency'),
    autoRegistrationIntervalGroup: document.getElementById('auto-registration-interval-group'),
    autoRegistrationIntervalMin: document.getElementById('auto-registration-interval-min'),
    autoRegistrationIntervalMax: document.getElementById('auto-registration-interval-max'),
};

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    initEventListeners();
    handleModeChange({ target: elements.regMode });
    loadAvailableServices();
    loadRecentAccounts();
    loadHistoryTasks();
    loadAutoRegistrationSettings();
    loadAutoRegistrationCpaOptions();
    startAccountsPolling();
    loadTodayStats(true);
    startTodayStatsPolling();
    startTodayStatsResetTicker();
    initVisibilityReconnect();
    restoreActiveTask();
    initAutoUploadOptions();
});

// 初始化注册后自动操作选项（CPA / Sub2API / TM）
async function initAutoUploadOptions() {
    await Promise.all([
        loadServiceSelect('/cpa-services?enabled=true', elements.cpaServiceSelect, elements.autoUploadCpa, elements.cpaServiceSelectGroup),
        loadServiceSelect('/sub2api-services?enabled=true', elements.sub2apiServiceSelect, elements.autoUploadSub2api, elements.sub2apiServiceSelectGroup),
        loadServiceSelect('/tm-services?enabled=true', elements.tmServiceSelect, elements.autoUploadTm, elements.tmServiceSelectGroup),
    ]);
}

// 通用：构建自定义多选下拉组件并处理联动
async function loadServiceSelect(apiPath, container, checkbox, selectGroup) {
    if (!checkbox || !container) return;
    let services = [];
    try {
        services = await api.get(apiPath);
    } catch (e) {}

    if (!services || services.length === 0) {
        checkbox.disabled = true;
        checkbox.title = '请先在设置中添加对应服务';
        const label = checkbox.closest('label');
        if (label) label.style.opacity = '0.5';
        container.innerHTML = '<div class="msd-empty">暂无可用服务</div>';
    } else {
        const items = services.map(s =>
            `<label class="msd-item">
                <input type="checkbox" value="${s.id}" checked>
                <span>${escapeHtml(s.name)}</span>
            </label>`
        ).join('');
        container.innerHTML = `
            <div class="msd-dropdown" id="${container.id}-dd">
                <div class="msd-trigger" onclick="toggleMsd('${container.id}-dd')">
                    <span class="msd-label">全部 (${services.length})</span>
                    <span class="msd-arrow">▼</span>
                </div>
                <div class="msd-list">${items}</div>
            </div>`;
        // 监听 checkbox 变化，更新触发器文字
        container.querySelectorAll('.msd-item input').forEach(cb => {
            cb.addEventListener('change', () => updateMsdLabel(container.id + '-dd'));
        });
        // 点击外部关闭
        document.addEventListener('click', (e) => {
            const dd = document.getElementById(container.id + '-dd');
            if (dd && !dd.contains(e.target)) dd.classList.remove('open');
        }, true);
    }

    // 联动显示/隐藏服务选择区
    checkbox.addEventListener('change', () => {
        if (selectGroup) selectGroup.style.display = checkbox.checked ? 'block' : 'none';
    });
}

function toggleMsd(ddId) {
    const dd = document.getElementById(ddId);
    if (dd) dd.classList.toggle('open');
}

function updateMsdLabel(ddId) {
    const dd = document.getElementById(ddId);
    if (!dd) return;
    const all = dd.querySelectorAll('.msd-item input');
    const checked = dd.querySelectorAll('.msd-item input:checked');
    const label = dd.querySelector('.msd-label');
    if (!label) return;
    if (checked.length === 0) label.textContent = '未选择';
    else if (checked.length === all.length) label.textContent = `全部 (${all.length})`;
    else label.textContent = Array.from(checked).map(c => c.nextElementSibling.textContent).join(', ');
}

// 获取自定义多选下拉中选中的服务 ID 列表
function getSelectedServiceIds(container) {
    if (!container) return [];
    return Array.from(container.querySelectorAll('.msd-item input:checked')).map(cb => parseInt(cb.value));
}

// 事件监听
function initEventListeners() {
    // 注册表单提交
    elements.form.addEventListener('submit', handleStartRegistration);

    // 注册模式切换
    elements.regMode.addEventListener('change', handleModeChange);

    // 邮箱服务切换
    elements.emailService.addEventListener('change', handleServiceChange);
    if (elements.autoRegistrationEmailServiceType) {
        elements.autoRegistrationEmailServiceType.addEventListener('change', () => populateAutoRegistrationEmailServiceOptions(0));
    }
    if (elements.autoRegistrationMode) {
        elements.autoRegistrationMode.addEventListener('change', () => {
            handleConcurrencyModeChange(
                elements.autoRegistrationMode,
                elements.concurrencyHint,
                elements.autoRegistrationIntervalGroup
            );
        });
    }

    // 取消按钮
    elements.cancelBtn.addEventListener('click', handleCancelTask);

    // 清空日志
    elements.clearLogBtn.addEventListener('click', () => {
        elements.consoleLog.innerHTML = '<div class="log-line info">[系统] 日志已清空</div>';
        displayedLogs.clear();  // 清空日志去重集合
    });

    // 刷新账号列表
    elements.refreshAccountsBtn.addEventListener('click', () => {
        loadRecentAccounts();
        toast.info('已刷新');
    });

    if (elements.refreshHistoryBtn) {
        elements.refreshHistoryBtn.addEventListener('click', async () => {
            await loadHistoryTasks();
            toast.info('历史任务已刷新');
        });
    }

    if (elements.historyTaskFilter) {
        elements.historyTaskFilter.addEventListener('change', () => {
            renderHistoryTasks();
        });
    }

    if (elements.restoreActiveTaskBtn) {
        elements.restoreActiveTaskBtn.addEventListener('click', async () => {
            await restoreCurrentActiveTaskView();
        });
    }

    if (elements.artifactPreviewClose) {
        elements.artifactPreviewClose.addEventListener('click', closeArtifactPreview);
    }

    if (elements.artifactPreviewOverlay) {
        elements.artifactPreviewOverlay.addEventListener('click', (event) => {
            if (event.target === elements.artifactPreviewOverlay) {
                closeArtifactPreview();
            }
        });
    }

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
            closeArtifactPreview();
        }
    });

    // 并发模式切换
    elements.concurrencyMode.addEventListener('change', () => {
        handleConcurrencyModeChange(elements.concurrencyMode, elements.concurrencyHint, elements.intervalGroup);
    });
    elements.outlookConcurrencyMode.addEventListener('change', () => {
        handleConcurrencyModeChange(elements.outlookConcurrencyMode, elements.outlookConcurrencyHint, elements.outlookIntervalGroup);
    });
}

// 加载可用的邮箱服务
async function loadAvailableServices() {
    try {
        const data = await api.get('/registration/available-services');
        availableServices = data;

        // 更新邮箱服务选择框
        updateEmailServiceOptions();
        populateAutoRegistrationEmailServiceOptions(parseInt(elements.autoRegistrationEmailServiceId?.value || '0', 10) || 0);

        addLog('info', '[系统] 邮箱服务列表已加载');
    } catch (error) {
        console.error('加载邮箱服务列表失败:', error);
        addLog('warning', '[警告] 加载邮箱服务列表失败');
    }
}

// 更新邮箱服务选择框
function updateEmailServiceOptions() {
    const select = elements.emailService;
    select.innerHTML = '';

    // 官方临时邮箱渠道
    if ((availableServices.tempmail && availableServices.tempmail.available) ||
        (availableServices.yyds_mail && availableServices.yyds_mail.available)) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = '🌐 临时邮箱';

        if (availableServices.tempmail && availableServices.tempmail.available) {
            availableServices.tempmail.services.forEach(service => {
                const option = document.createElement('option');
                option.value = `tempmail:${service.id || 'default'}`;
                option.textContent = service.name;
                option.dataset.type = 'tempmail';
                optgroup.appendChild(option);
            });
        }

        if (availableServices.yyds_mail && availableServices.yyds_mail.available) {
            availableServices.yyds_mail.services.forEach(service => {
                const option = document.createElement('option');
                option.value = `yyds_mail:${service.id || 'default'}`;
                option.textContent = service.name + (service.default_domain ? ` (@${service.default_domain})` : '');
                option.dataset.type = 'yyds_mail';
                optgroup.appendChild(option);
            });
        }

        select.appendChild(optgroup);
    }

    // Outlook
    if (availableServices.outlook.available) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = `📧 Outlook (${availableServices.outlook.count} 个账户)`;

        availableServices.outlook.services.forEach(service => {
            const option = document.createElement('option');
            option.value = `outlook:${service.id}`;
            option.textContent = service.name + (service.has_oauth ? ' (OAuth)' : '');
            option.dataset.type = 'outlook';
            option.dataset.serviceId = service.id;
            optgroup.appendChild(option);
        });

        select.appendChild(optgroup);

        // Outlook 批量注册选项
        const batchOption = document.createElement('option');
        batchOption.value = 'outlook_batch:all';
        batchOption.textContent = `📋 Outlook 批量注册 (${availableServices.outlook.count} 个账户)`;
        batchOption.dataset.type = 'outlook_batch';
        optgroup.appendChild(batchOption);
    } else {
        const optgroup = document.createElement('optgroup');
        optgroup.label = '📧 Outlook (未配置)';

        const option = document.createElement('option');
        option.value = '';
        option.textContent = '请先在邮箱服务页面导入账户';
        option.disabled = true;
        optgroup.appendChild(option);

        select.appendChild(optgroup);
    }

    // 自定义域名
    if (availableServices.moe_mail.available) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = `🔗 自定义域名 (${availableServices.moe_mail.count} 个服务)`;

        availableServices.moe_mail.services.forEach(service => {
            const option = document.createElement('option');
            option.value = `moe_mail:${service.id || 'default'}`;
            option.textContent = service.name + (service.default_domain ? ` (@${service.default_domain})` : '');
            option.dataset.type = 'moe_mail';
            if (service.id) {
                option.dataset.serviceId = service.id;
            }
            optgroup.appendChild(option);
        });

        select.appendChild(optgroup);
    } else {
        const optgroup = document.createElement('optgroup');
        optgroup.label = '🔗 自定义域名 (未配置)';

        const option = document.createElement('option');
        option.value = '';
        option.textContent = '请先在邮箱服务页面添加服务';
        option.disabled = true;
        optgroup.appendChild(option);

        select.appendChild(optgroup);
    }

    // Temp-Mail（自部署）
    if (availableServices.temp_mail && availableServices.temp_mail.available) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = `📮 Temp-Mail 自部署 (${availableServices.temp_mail.count} 个服务)`;

        availableServices.temp_mail.services.forEach(service => {
            const option = document.createElement('option');
            option.value = `temp_mail:${service.id}`;
            option.textContent = service.name + (service.domain ? ` (@${service.domain})` : '');
            option.dataset.type = 'temp_mail';
            option.dataset.serviceId = service.id;
            optgroup.appendChild(option);
        });

        select.appendChild(optgroup);
    }

    if (availableServices.codex_otp && availableServices.codex_otp.available) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = `🧩 Codex OTP (${availableServices.codex_otp.count} 个服务)`;

        availableServices.codex_otp.services.forEach(service => {
            const option = document.createElement('option');
            option.value = `codex_otp:${service.id || 'default'}`;
            option.textContent = service.name + (service.domain ? ` (@${service.domain})` : '');
            option.dataset.type = 'codex_otp';
            if (service.id) {
                option.dataset.serviceId = service.id;
            }
            optgroup.appendChild(option);
        });

        select.appendChild(optgroup);
    }

    if (availableServices.codex_otp_d1 && availableServices.codex_otp_d1.available) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = `🗄️ Codex OTP D1 (${availableServices.codex_otp_d1.count} 个服务)`;

        availableServices.codex_otp_d1.services.forEach(service => {
            const option = document.createElement('option');
            option.value = `codex_otp_d1:${service.id || 'default'}`;
            option.textContent = service.name + (service.domain ? ` (@${service.domain})` : '');
            option.dataset.type = 'codex_otp_d1';
            if (service.id) {
                option.dataset.serviceId = service.id;
            }
            optgroup.appendChild(option);
        });

        select.appendChild(optgroup);
    }

    // DuckMail
    if (availableServices.duck_mail && availableServices.duck_mail.available) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = `🦆 DuckMail (${availableServices.duck_mail.count} 个服务)`;

        availableServices.duck_mail.services.forEach(service => {
            const option = document.createElement('option');
            option.value = `duck_mail:${service.id}`;
            option.textContent = service.name + (service.default_domain ? ` (@${service.default_domain})` : '');
            option.dataset.type = 'duck_mail';
            option.dataset.serviceId = service.id;
            optgroup.appendChild(option);
        });

        select.appendChild(optgroup);
    }

    // Freemail
    if (availableServices.freemail && availableServices.freemail.available) {
        const optgroup = document.createElement('optgroup');
        optgroup.label = `📧 Freemail (${availableServices.freemail.count} 个服务)`;

        availableServices.freemail.services.forEach(service => {
            const option = document.createElement('option');
            option.value = `freemail:${service.id}`;
            option.textContent = service.name + (service.domain ? ` (@${service.domain})` : '');
            option.dataset.type = 'freemail';
            option.dataset.serviceId = service.id;
            optgroup.appendChild(option);
        });

        select.appendChild(optgroup);
    }
}

// 处理邮箱服务切换
function handleServiceChange(e) {
    const value = e.target.value;
    if (!value) return;

    const [type, id] = value.split(':');
    // 处理 Outlook 批量注册模式
    if (type === 'outlook_batch') {
        isOutlookBatchMode = true;
        elements.outlookBatchSection.style.display = 'block';
        elements.regModeGroup.style.display = 'none';
        elements.batchCountGroup.style.display = 'none';
        elements.batchOptions.style.display = 'none';
        loadOutlookAccounts();
        addLog('info', '[系统] 已切换到 Outlook 批量注册模式');
        return;
    } else {
        isOutlookBatchMode = false;
        elements.outlookBatchSection.style.display = 'none';
        elements.regModeGroup.style.display = 'block';
    }

    // 显示服务信息
    if (type === 'outlook') {
        const service = availableServices.outlook.services.find(s => s.id == id);
        if (service) {
            addLog('info', `[系统] 已选择 Outlook 账户: ${service.name}`);
        }
    } else if (type === 'yyds_mail') {
        const service = availableServices.yyds_mail.services.find(s => (s.id || 'default') == id);
        if (service) {
            addLog('info', `[系统] 已选择 YYDS Mail 渠道: ${service.name}`);
        }
    } else if (type === 'moe_mail') {
        const service = availableServices.moe_mail.services.find(s => s.id == id);
        if (service) {
            addLog('info', `[系统] 已选择自定义域名服务: ${service.name}`);
        }
    } else if (type === 'temp_mail') {
        const service = availableServices.temp_mail.services.find(s => s.id == id);
        if (service) {
            addLog('info', `[系统] 已选择 Temp-Mail 自部署服务: ${service.name}`);
        }
    } else if (type === 'codex_otp') {
        const service = availableServices.codex_otp.services.find(s => (s.id || 'default') == id);
        if (service) {
            addLog('info', `[系统] 已选择 Codex OTP 服务: ${service.name}`);
        }
    } else if (type === 'codex_otp_d1') {
        const service = availableServices.codex_otp_d1.services.find(s => (s.id || 'default') == id);
        if (service) {
            addLog('info', `[系统] 已选择 Codex OTP D1 服务: ${service.name}`);
        }
    } else if (type === 'duck_mail') {
        const service = availableServices.duck_mail.services.find(s => s.id == id);
        if (service) {
            addLog('info', `[系统] 已选择 DuckMail 服务: ${service.name}`);
        }
    } else if (type === 'freemail') {
        const service = availableServices.freemail.services.find(s => s.id == id);
        if (service) {
            addLog('info', `[系统] 已选择 Freemail 服务: ${service.name}`);
        }
    }
}

// 模式切换
function handleModeChange(e) {
    const mode = e.target.value;
    isAutoMode = mode === 'auto';
    isBatchMode = mode === 'batch';

    elements.batchCountGroup.style.display = isBatchMode ? 'block' : 'none';
    elements.batchOptions.style.display = isBatchMode ? 'block' : 'none';
    if (elements.autoRegistrationSection) {
        elements.autoRegistrationSection.style.display = isAutoMode ? 'block' : 'none';
    }
    if (elements.emailServiceGroup) {
        elements.emailServiceGroup.style.display = isAutoMode ? 'none' : 'block';
    }
    const autoUploadGroup = elements.autoUploadCpa?.closest('#auto-upload-group');
    if (autoUploadGroup) {
        autoUploadGroup.style.display = isAutoMode ? 'none' : 'block';
    }
    elements.startBtn.textContent = isAutoMode ? '💾 保存自动注册设置' : '🚀 开始注册';

    if (isAutoMode) {
        elements.cancelBtn.disabled = false;
    } else {
        stopAutoRegistrationMonitor();
        updateAutoMonitorHeader('idle', null);
        elements.cancelBtn.disabled = true;
    }
}

// 并发模式切换（批量）
function handleConcurrencyModeChange(selectEl, hintEl, intervalGroupEl) {
    const mode = selectEl.value;
    if (mode === 'parallel') {
        hintEl.textContent = '所有任务分成 N 个并发批次同时执行';
        intervalGroupEl.style.display = 'none';
    } else {
        hintEl.textContent = '同时最多运行 N 个任务，每隔 interval 秒启动新任务';
        intervalGroupEl.style.display = 'block';
    }
}

// 开始注册
async function handleStartRegistration(e) {
    e.preventDefault();

    if (isAutoMode) {
        await handleSaveAutoRegistration();
        return;
    }

    const selectedValue = elements.emailService.value;
    if (!selectedValue) {
        toast.error('请选择一个邮箱服务');
        return;
    }

    // 处理 Outlook 批量注册模式
    if (isOutlookBatchMode) {
        await handleOutlookBatchRegistration();
        return;
    }

    const [emailServiceType, serviceId] = selectedValue.split(':');

    // 禁用开始按钮
    elements.startBtn.disabled = true;
    elements.cancelBtn.disabled = false;

    // 清空日志
    elements.consoleLog.innerHTML = '';

    // 构建请求数据（代理从设置中自动获取）
    const requestData = {
        email_service_type: emailServiceType,
        auto_upload_cpa: elements.autoUploadCpa ? elements.autoUploadCpa.checked : false,
        cpa_service_ids: elements.autoUploadCpa && elements.autoUploadCpa.checked ? getSelectedServiceIds(elements.cpaServiceSelect) : [],
        auto_upload_sub2api: elements.autoUploadSub2api ? elements.autoUploadSub2api.checked : false,
        sub2api_service_ids: elements.autoUploadSub2api && elements.autoUploadSub2api.checked ? getSelectedServiceIds(elements.sub2apiServiceSelect) : [],
        auto_upload_tm: elements.autoUploadTm ? elements.autoUploadTm.checked : false,
        tm_service_ids: elements.autoUploadTm && elements.autoUploadTm.checked ? getSelectedServiceIds(elements.tmServiceSelect) : [],
    };

    // 如果选择了数据库中的服务，传递 service_id
    if (serviceId && serviceId !== 'default') {
        requestData.email_service_id = parseInt(serviceId);
    }

    if (isBatchMode) {
        await handleBatchRegistration(requestData);
    } else {
        await handleSingleRegistration(requestData);
    }
}


async function loadAutoRegistrationSettings() {
    if (!elements.autoRegistrationEnabled) return;
    try {
        const data = await api.get('/settings');
        const reg = data.registration || {};
        elements.autoRegistrationEnabled.checked = reg.auto_enabled || false;
        elements.autoRegistrationCheckInterval.value = reg.auto_check_interval || 60;
        elements.autoRegistrationMinReady.value = reg.auto_min_ready_auth_files || 1;
        elements.autoRegistrationEmailServiceType.value = reg.auto_email_service_type || 'tempmail';
        elements.autoRegistrationProxy.value = reg.auto_proxy || '';
        elements.autoRegistrationMode.value = reg.auto_mode || 'pipeline';
        elements.autoRegistrationConcurrency.value = reg.auto_concurrency || 1;
        elements.autoRegistrationIntervalMin.value = reg.auto_interval_min || 5;
        elements.autoRegistrationIntervalMax.value = reg.auto_interval_max || 30;
        handleConcurrencyModeChange(
            elements.autoRegistrationMode,
            elements.concurrencyHint,
            elements.autoRegistrationIntervalGroup
        );
        elements.autoRegistrationEmailServiceId.dataset.selectedId = String(reg.auto_email_service_id || 0);
        elements.autoRegistrationCpaServiceId.dataset.selectedId = String(reg.auto_cpa_service_id || 0);
        populateAutoRegistrationEmailServiceOptions(reg.auto_email_service_id || 0);
    } catch (error) {
        console.error('加载自动注册设置失败:', error);
    }
}

async function loadAutoRegistrationCpaOptions() {
    if (!elements.autoRegistrationCpaServiceId) return;
    try {
        const services = await api.get('/cpa-services?enabled=true');
        const options = ['<option value="0">请选择 CPA 服务</option>'];
        services.forEach(service => {
            options.push(`<option value="${service.id}">${escapeHtml(service.name)} (#${service.id})</option>`);
        });
        elements.autoRegistrationCpaServiceId.innerHTML = options.join('');
        elements.autoRegistrationCpaServiceId.value = elements.autoRegistrationCpaServiceId.dataset.selectedId || '0';
    } catch (error) {
        console.error('加载 CPA 服务失败:', error);
    }
}

function populateAutoRegistrationEmailServiceOptions(selectedId = 0) {
    if (!elements.autoRegistrationEmailServiceId || !elements.autoRegistrationEmailServiceType) return;
    const selectedType = elements.autoRegistrationEmailServiceType.value || 'tempmail';
    const options = ['<option value="0">自动选择</option>'];
    const bucket = availableServices[selectedType];
    if (bucket && Array.isArray(bucket.services)) {
        bucket.services.forEach(service => {
            options.push(`<option value="${service.id}">${escapeHtml(service.name)} (#${service.id})</option>`);
        });
    }
    elements.autoRegistrationEmailServiceId.innerHTML = options.join('');
    elements.autoRegistrationEmailServiceId.value = String(selectedId || elements.autoRegistrationEmailServiceId.dataset.selectedId || 0);
}

async function handleSaveAutoRegistration() {
    const autoCheckInterval = parseInt(elements.autoRegistrationCheckInterval.value, 10) || 60;
    const autoMinReady = parseInt(elements.autoRegistrationMinReady.value, 10) || 1;
    const autoEmailServiceId = parseInt(elements.autoRegistrationEmailServiceId.value, 10) || 0;
    const autoConcurrency = parseInt(elements.autoRegistrationConcurrency.value, 10) || 1;
    const autoIntervalMin = parseInt(elements.autoRegistrationIntervalMin.value, 10) || 0;
    const autoIntervalMax = parseInt(elements.autoRegistrationIntervalMax.value, 10) || 0;
    const autoCpaServiceId = parseInt(elements.autoRegistrationCpaServiceId.value, 10) || 0;

    if (autoCheckInterval < 5 || autoCheckInterval > 3600) {
        toast.error('自动注册检查间隔必须在 5-3600 秒之间');
        return;
    }
    if (autoMinReady < 1 || autoMinReady > 10000) {
        toast.error('自动注册保底数量必须在 1-10000 之间');
        return;
    }
    if (autoIntervalMin < 0 || autoIntervalMax < autoIntervalMin) {
        toast.error('自动注册启动间隔参数无效');
        return;
    }
    if (autoConcurrency < 1 || autoConcurrency > 100) {
        toast.error('自动注册并发数必须在 1-100 之间');
        return;
    }
    if (elements.autoRegistrationEnabled.checked && autoCpaServiceId <= 0) {
        toast.error('启用自动注册前请先选择一个 CPA 服务');
        return;
    }

    const data = await api.get('/settings');
    const reg = data.registration || {};
    const payload = {
        max_retries: reg.max_retries || 3,
        timeout: reg.timeout || 120,
        default_password_length: reg.default_password_length || 12,
        entry_flow: reg.entry_flow || 'native',
        sleep_min: reg.sleep_min || 5,
        sleep_max: reg.sleep_max || 30,
        auto_enabled: elements.autoRegistrationEnabled.checked,
        auto_check_interval: autoCheckInterval,
        auto_min_ready_auth_files: autoMinReady,
        auto_email_service_type: elements.autoRegistrationEmailServiceType.value,
        auto_email_service_id: autoEmailServiceId,
        auto_proxy: elements.autoRegistrationProxy.value.trim(),
        auto_interval_min: autoIntervalMin,
        auto_interval_max: autoIntervalMax,
        auto_concurrency: autoConcurrency,
        auto_mode: elements.autoRegistrationMode.value,
        auto_cpa_service_id: autoCpaServiceId,
        maintenance_enabled: !!reg.maintenance_enabled,
        maintenance_schedule_mode: reg.maintenance_schedule_mode || 'daily',
        maintenance_schedule_time: reg.maintenance_schedule_time || '03:00',
        maintenance_schedule_cron: reg.maintenance_schedule_cron || '0 3 * * *',
        maintenance_validation_proxy: reg.maintenance_validation_proxy || '',
        maintenance_validation_interval_minutes: reg.maintenance_validation_interval_minutes || 1440,
        maintenance_debug_enabled: !!reg.maintenance_debug_enabled,
        maintenance_cleanup_local: !!reg.maintenance_cleanup_local,
        maintenance_cleanup_remote_cpa: !!reg.maintenance_cleanup_remote_cpa,
        maintenance_cpa_service_id: reg.maintenance_cpa_service_id || 0,
    };

    await api.post('/settings/registration', payload);
    toast.success('自动注册设置已保存');

    if (elements.autoRegistrationEnabled.checked) {
        sessionStorage.setItem('activeTask', JSON.stringify({ mode: 'auto' }));
        currentTask = null;
        activeTaskUuid = null;
        autoMonitorLastLogIndex = 0;
        displayedLogs.clear();
        elements.consoleLog.innerHTML = '';
        addLog('info', '[系统] 自动注册监控已启动');
        startAutoRegistrationMonitor();
    } else {
        stopAutoRegistrationMonitor();
        disconnectBatchWebSocket();
        stopBatchPolling();
        currentBatch = null;
        activeBatchId = null;
        batchCompleted = true;
        batchFinalStatus = 'cancelled';
        elements.batchProgressSection.style.display = 'none';
        elements.cancelBtn.disabled = true;
        updateAutoMonitorHeader('disabled', null);
        updateTaskStatus('disabled');
        const saved = sessionStorage.getItem('activeTask');
        if (saved) {
            try {
                const parsed = JSON.parse(saved);
                if (parsed.mode === 'auto') {
                    sessionStorage.removeItem('activeTask');
                }
            } catch {
                sessionStorage.removeItem('activeTask');
            }
        }
        addLog('info', '[系统] 自动注册已禁用');
    }
}

// 单次注册
async function handleSingleRegistration(requestData) {
    // 重置任务状态
    taskCompleted = false;
    taskFinalStatus = null;
    displayedLogs.clear();  // 清空日志去重集合
    toastShown = false;  // 重置 toast 标志

    addLog('info', '[系统] 正在启动注册任务...');

    try {
        const data = await api.post('/registration/start', requestData);

        currentTask = data;
        activeTaskUuid = data.task_uuid;  // 保存用于重连
        // 持久化到 sessionStorage，跨页面导航后可恢复
        sessionStorage.setItem('activeTask', JSON.stringify({ task_uuid: data.task_uuid, mode: 'single' }));
        addLog('info', `[系统] 任务已创建: ${data.task_uuid}`);
        showTaskStatus(data);
        updateTaskStatus('running');

        // 优先使用 WebSocket
        connectWebSocket(data.task_uuid);

    } catch (error) {
        addLog('error', `[错误] 启动失败: ${error.message}`);
        toast.error(error.message);
        resetButtons();
    }
}


// ============== WebSocket 功能 ==============

function getReconnectDelay(attempt) {
    return Math.min(WS_RECONNECT_BASE_DELAY * (2 ** Math.max(0, attempt - 1)), WS_RECONNECT_MAX_DELAY);
}

function clearWebSocketReconnect() {
    if (wsReconnectTimer) {
        clearTimeout(wsReconnectTimer);
        wsReconnectTimer = null;
    }
    wsReconnectAttempts = 0;
}

function clearBatchWebSocketReconnect() {
    if (batchWsReconnectTimer) {
        clearTimeout(batchWsReconnectTimer);
        batchWsReconnectTimer = null;
    }
    batchWsReconnectAttempts = 0;
}

function scheduleWebSocketReconnect(taskUuid) {
    if (!taskUuid || wsReconnectTimer || wsManualClose || taskCompleted || taskFinalStatus !== null || activeTaskUuid !== taskUuid) {
        return;
    }

    wsReconnectAttempts += 1;
    const delay = getReconnectDelay(wsReconnectAttempts);
    addLog('warning', `[系统] WebSocket 已断开，${delay / 1000} 秒后尝试重连任务监控...`);

    wsReconnectTimer = setTimeout(() => {
        wsReconnectTimer = null;
        connectWebSocket(taskUuid);
    }, delay);
}

function scheduleBatchWebSocketReconnect(batchId) {
    if (!batchId || batchWsReconnectTimer || batchWsManualClose || batchCompleted || batchFinalStatus !== null || activeBatchId !== batchId) {
        return;
    }

    batchWsReconnectAttempts += 1;
    const delay = getReconnectDelay(batchWsReconnectAttempts);
    addLog('warning', `[系统] 批量任务 WebSocket 已断开，${delay / 1000} 秒后尝试重连监控...`);

    batchWsReconnectTimer = setTimeout(() => {
        batchWsReconnectTimer = null;
        connectBatchWebSocket(batchId);
    }, delay);
}

function startCurrentBatchPolling(batchId) {
    if (!batchId) return;

    const pollingMode = currentBatch && currentBatch.batch_id === batchId
        ? currentBatch.pollingMode
        : (isOutlookBatchMode ? 'outlook_batch' : 'batch');

    if (pollingMode === 'outlook_batch') {
        startOutlookBatchPolling(batchId);
        return;
    }

    startBatchPolling(batchId);
}

// 连接 WebSocket
function connectWebSocket(taskUuid) {
    activeTaskUuid = taskUuid;

    if (webSocket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(webSocket.readyState)) {
        return;
    }

    if (wsReconnectTimer) {
        clearTimeout(wsReconnectTimer);
        wsReconnectTimer = null;
    }
    wsManualClose = false;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/ws/task/${taskUuid}`;

    try {
        const socket = new WebSocket(wsUrl);
        webSocket = socket;

        socket.onopen = () => {
            if (webSocket !== socket) return;
            console.log('WebSocket 连接成功');
            useWebSocket = true;
            clearWebSocketReconnect();
            // 停止轮询（如果有）
            stopLogPolling();
            // 开始心跳
            startWebSocketHeartbeat();
        };

        socket.onmessage = (event) => {
            if (webSocket !== socket) return;
            const data = JSON.parse(event.data);

            if (data.type === 'log') {
                const logType = getLogType(data.message);
                addLog(logType, data.message);
            } else if (data.type === 'status') {
                updateTaskStatus(data.status);
                if (data.email) {
                    elements.taskEmail.textContent = data.email;
                }
                if (data.email_service) {
                    elements.taskService.textContent = getServiceTypeText(data.email_service);
                }
                if (elements.taskScheme && data.effective_scheme) {
                    elements.taskScheme.textContent = data.effective_scheme;
                }

                // 检查是否完成
                if (['completed', 'failed', 'cancelled', 'cancelling'].includes(data.status)) {
                    // 保存最终状态，用于 onclose 判断
                    taskFinalStatus = data.status;
                    taskCompleted = true;

                    // 断开 WebSocket（异步操作）
                    disconnectWebSocket();

                    // 任务完成后再重置按钮
                    resetButtons();

                    // 只显示一次 toast
                    if (!toastShown) {
                        toastShown = true;
                        if (data.status === 'completed') {
                            addLog('success', '[成功] 注册成功！');
                            toast.success('注册成功！');
                            // 刷新账号列表
                            loadRecentAccounts();
                        } else if (data.status === 'failed') {
                            addLog('error', '[错误] 注册失败');
                            toast.error('注册失败');
                        } else if (data.status === 'cancelled' || data.status === 'cancelling') {
                            addLog('warning', '[警告] 任务已取消');
                        }
                    }
                }
            } else if (data.type === 'pong') {
                // 心跳响应，忽略
            }
        };

        socket.onclose = (event) => {
            const isCurrentSocket = webSocket === socket;
            if (isCurrentSocket) {
                webSocket = null;
                stopWebSocketHeartbeat();
            }

            console.log('WebSocket 连接关闭:', event.code);

            const shouldReconnect = isCurrentSocket &&
                !wsManualClose &&
                !taskCompleted &&
                taskFinalStatus === null &&
                activeTaskUuid === taskUuid;

            if (shouldReconnect) {
                console.log('WebSocket 断开，准备自动重连');
                useWebSocket = false;
                startLogPolling(taskUuid);
                scheduleWebSocketReconnect(taskUuid);
            }
        };

        socket.onerror = (error) => {
            if (webSocket !== socket) return;
            console.error('WebSocket 错误:', error);
            useWebSocket = false;
        };

    } catch (error) {
        console.error('WebSocket 连接失败:', error);
        useWebSocket = false;
        startLogPolling(taskUuid);
        scheduleWebSocketReconnect(taskUuid);
    }
}

// 断开 WebSocket
function disconnectWebSocket() {
    wsManualClose = true;
    clearWebSocketReconnect();
    stopWebSocketHeartbeat();
    if (webSocket) {
        const socket = webSocket;
        webSocket = null;
        socket.close();
    }
}

// 开始心跳
function startWebSocketHeartbeat() {
    stopWebSocketHeartbeat();
    wsHeartbeatInterval = setInterval(() => {
        if (webSocket && webSocket.readyState === WebSocket.OPEN) {
            webSocket.send(JSON.stringify({ type: 'ping' }));
        }
    }, 25000);  // 每 25 秒发送一次心跳
}

// 停止心跳
function stopWebSocketHeartbeat() {
    if (wsHeartbeatInterval) {
        clearInterval(wsHeartbeatInterval);
        wsHeartbeatInterval = null;
    }
}

// 发送取消请求
function cancelViaWebSocket() {
    if (webSocket && webSocket.readyState === WebSocket.OPEN) {
        webSocket.send(JSON.stringify({ type: 'cancel' }));
    }
}

// 批量注册
async function handleBatchRegistration(requestData) {
    // 重置批量任务状态
    batchCompleted = false;
    batchFinalStatus = null;
    displayedLogs.clear();  // 清空日志去重集合
    toastShown = false;  // 重置 toast 标志

    const count = parseInt(elements.batchCount.value) || 5;
    const intervalMin = parseInt(elements.intervalMin.value) || 5;
    const intervalMax = parseInt(elements.intervalMax.value) || 30;
    const concurrency = parseInt(elements.concurrencyCount.value) || 3;
    const mode = elements.concurrencyMode.value || 'pipeline';

    requestData.count = count;
    requestData.interval_min = intervalMin;
    requestData.interval_max = intervalMax;
    requestData.concurrency = Math.min(50, Math.max(1, concurrency));
    requestData.mode = mode;

    addLog('info', `[系统] 正在启动批量注册任务 (数量: ${count})...`);

    try {
        const data = await api.post('/registration/batch', requestData);

        currentBatch = { ...data, pollingMode: 'batch' };
        activeBatchId = data.batch_id;  // 保存用于重连
        // 持久化到 sessionStorage，跨页面导航后可恢复
        sessionStorage.setItem('activeTask', JSON.stringify({ batch_id: data.batch_id, mode: 'batch', total: data.count }));
        addLog('info', `[系统] 批量任务已创建: ${data.batch_id}`);
        addLog('info', `[系统] 共 ${data.count} 个任务已加入队列`);
        showBatchStatus(data);

        // 优先使用 WebSocket
        connectBatchWebSocket(data.batch_id);

    } catch (error) {
        addLog('error', `[错误] 启动失败: ${error.message}`);
        toast.error(error.message);
        resetButtons();
    }
}

// 取消任务
async function handleCancelTask() {
    // 禁用取消按钮，防止重复点击
    elements.cancelBtn.disabled = true;
    addLog('info', '[系统] 正在提交取消请求...');

    try {
        // 批量任务取消（包括普通批量模式和 Outlook 批量模式）
        if (currentBatch && (isBatchMode || isOutlookBatchMode || isAutoMode)) {
            // 优先通过 WebSocket 取消
            if (batchWebSocket && batchWebSocket.readyState === WebSocket.OPEN) {
                batchWebSocket.send(JSON.stringify({ type: 'cancel' }));
                addLog('warning', '[警告] 批量任务取消请求已提交');
                toast.info('任务取消请求已提交');
            } else {
                // 降级到 REST API
                const endpoint = isOutlookBatchMode
                    ? `/registration/outlook-batch/${currentBatch.batch_id}/cancel`
                    : `/registration/batch/${currentBatch.batch_id}/cancel`;

                await api.post(endpoint);
                addLog('warning', '[警告] 批量任务取消请求已提交');
                toast.info('任务取消请求已提交');
                if (!isAutoMode) {
                    stopBatchPolling();
                    resetButtons();
                }
            }
        }
        // 单次任务取消
        else if (currentTask) {
            // 优先通过 WebSocket 取消
            if (webSocket && webSocket.readyState === WebSocket.OPEN) {
                webSocket.send(JSON.stringify({ type: 'cancel' }));
                addLog('warning', '[警告] 任务取消请求已提交');
                toast.info('任务取消请求已提交');
            } else {
                // 降级到 REST API
                await api.post(`/registration/tasks/${currentTask.task_uuid}/cancel`);
                addLog('warning', '[警告] 任务已取消');
                toast.info('任务已取消');
                stopLogPolling();
                resetButtons();
            }
        }
        // 没有活动任务
        else {
            addLog('warning', '[警告] 没有活动的任务可以取消');
            toast.warning('没有活动的任务');
            resetButtons();
        }
    } catch (error) {
        addLog('error', `[错误] 取消失败: ${error.message}`);
        toast.error(error.message);
        // 恢复取消按钮，允许重试
        elements.cancelBtn.disabled = false;
    }
}

// 开始轮询日志
function startLogPolling(taskUuid) {
    if (logPollingInterval) {
        return;
    }

    let lastLogIndex = 0;

    logPollingInterval = setInterval(async () => {
        try {
            const data = await api.get(`/registration/tasks/${taskUuid}/logs`);

            // 更新任务状态
            updateTaskStatus(data.status);

            // 更新邮箱信息
            if (data.email) {
                elements.taskEmail.textContent = data.email;
            }
            if (data.email_service) {
                elements.taskService.textContent = getServiceTypeText(data.email_service);
            }
            if (elements.taskScheme && data.effective_scheme) {
                elements.taskScheme.textContent = data.effective_scheme;
            }
            renderPlaywrightDiagnostics(data.playwright);

            // 添加新日志
            const logs = data.logs || [];
            for (let i = lastLogIndex; i < logs.length; i++) {
                const log = logs[i];
                const logType = getLogType(log);
                addLog(logType, log);
            }
            lastLogIndex = logs.length;

            // 检查任务是否完成
            if (['completed', 'failed', 'cancelled'].includes(data.status)) {
                stopLogPolling();
                resetButtons();

                // 只显示一次 toast
                if (!toastShown) {
                    toastShown = true;
                    if (data.status === 'completed') {
                        addLog('success', '[成功] 注册成功！');
                        toast.success('注册成功！');
                        // 刷新账号列表
                        loadRecentAccounts();
                    } else if (data.status === 'failed') {
                        addLog('error', '[错误] 注册失败');
                        toast.error('注册失败');
                    } else if (data.status === 'cancelled') {
                        addLog('warning', '[警告] 任务已取消');
                    }
                }
            }
        } catch (error) {
            console.error('轮询日志失败:', error);
        }
    }, 1000);
}

// 停止轮询日志
function stopLogPolling() {
    if (logPollingInterval) {
        clearInterval(logPollingInterval);
        logPollingInterval = null;
    }
}

// 开始轮询批量状态
function startBatchPolling(batchId) {
    if (batchPollingInterval) {
        return;
    }

    batchPollingInterval = setInterval(async () => {
        try {
            const data = await api.get(`/registration/batch/${batchId}`);
            updateBatchProgress(data);

            // 检查是否完成
            if (data.finished) {
                stopBatchPolling();
                resetButtons();

                // 只显示一次 toast
                if (!toastShown) {
                    toastShown = true;
                    addLog('info', `[完成] 批量任务完成！成功: ${data.success}, 失败: ${data.failed}`);
                    if (data.success > 0) {
                        toast.success(`批量注册完成，成功 ${data.success} 个`);
                        // 刷新账号列表
                        loadRecentAccounts();
                    } else {
                        toast.warning('批量注册完成，但没有成功注册任何账号');
                    }
                }
            }
        } catch (error) {
            console.error('轮询批量状态失败:', error);
        }
    }, 2000);
}

// 停止轮询批量状态
function stopBatchPolling() {
    if (batchPollingInterval) {
        clearInterval(batchPollingInterval);
        batchPollingInterval = null;
    }
}

// 显示任务状态
function getTaskPlaywrightDiagnostics(task) {
    const metadata = task?.result?.metadata || {};
    const diagnostics = metadata?.playwright_diagnostics;
    const summary = metadata?.playwright_diagnosis_summary;

    if (summary && typeof summary === 'object') {
        return {
            ...(diagnostics && typeof diagnostics === 'object' ? diagnostics : {}),
            ...summary,
        };
    }

    return diagnostics && typeof diagnostics === 'object' ? diagnostics : null;
}

function showTaskStatus(task) {
    elements.taskStatusRow.style.display = 'grid';
    elements.batchProgressSection.style.display = 'none';
    elements.taskStatusBadge.style.display = 'inline-flex';
    elements.taskId.textContent = task.task_uuid.substring(0, 8) + '...';
    elements.taskEmail.textContent = '-';
    elements.taskService.textContent = '-';
    if (elements.taskScheme) {
        elements.taskScheme.textContent = task.effective_scheme || '-';
    }
    if (elements.taskLastChecked) {
        elements.taskLastChecked.textContent = '-';
    }
    if (elements.taskInventory) {
        elements.taskInventory.textContent = '-';
    }
    renderPlaywrightDiagnostics(getTaskPlaywrightDiagnostics(task), task);
}

function formatPlaywrightProbe(browserProbe) {
    if (!browserProbe || typeof browserProbe !== 'object') return '-';
    const parts = [];
    if (browserProbe.method) parts.push(String(browserProbe.method));
    if (browserProbe.hit !== undefined) parts.push(browserProbe.hit ? '命中' : '未命中');
    if (browserProbe.source) parts.push(String(browserProbe.source));
    return parts.length ? parts.join(' / ') : '-';
}

function formatPlaywrightPath(diagnostics) {
    if (!diagnostics || typeof diagnostics !== 'object') return '-';
    const parts = [];
    if (diagnostics.used_native_backfill) parts.push('native_backfill');
    if (diagnostics.used_browser_retry) parts.push('browser_retry');
    if (diagnostics.used_signin_bridge) parts.push('signin_bridge');
    return parts.length ? parts.join(' -> ') : 'browser_first';
}

function renderPlaywrightDiagnostics(diagnostics, taskContext = null) {
    if (!elements.playwrightDiagnostics) return;
    if (!diagnostics || typeof diagnostics !== 'object') {
        elements.playwrightDiagnostics.classList.remove('visible');
        if (elements.playwrightStage) elements.playwrightStage.textContent = '-';
        if (elements.playwrightStrategy) elements.playwrightStrategy.textContent = '-';
        if (elements.playwrightFailure) elements.playwrightFailure.textContent = '-';
        if (elements.playwrightDiagnosis) elements.playwrightDiagnosis.textContent = '-';
        if (elements.playwrightDiagnosisHint) elements.playwrightDiagnosisHint.textContent = '-';
        if (elements.playwrightAction) elements.playwrightAction.textContent = '-';
        if (elements.playwrightStrategyFlags) elements.playwrightStrategyFlags.textContent = '-';
        if (elements.playwrightPostFailureStrategy) elements.playwrightPostFailureStrategy.textContent = '-';
        if (elements.playwrightNextRunPolicy) elements.playwrightNextRunPolicy.textContent = '-';
        if (elements.playwrightTokens) elements.playwrightTokens.textContent = '-';
        if (elements.playwrightBrowserProbe) elements.playwrightBrowserProbe.textContent = '-';
        if (elements.playwrightPageState) elements.playwrightPageState.textContent = '-';
        if (elements.playwrightPageTitle) elements.playwrightPageTitle.textContent = '-';
        if (elements.playwrightIpify) elements.playwrightIpify.textContent = '-';
        if (elements.playwrightRefresh) elements.playwrightRefresh.textContent = '-';
        if (elements.playwrightCallback) elements.playwrightCallback.textContent = '-';
        if (elements.playwrightCurrentUrl) elements.playwrightCurrentUrl.textContent = '-';
        if (elements.playwrightPath) elements.playwrightPath.textContent = '-';
        if (elements.playwrightArtifact) elements.playwrightArtifact.textContent = '-';
        return;
    }

    const hasAccess = !!diagnostics.has_access_token;
    const hasSession = !!diagnostics.has_session_token;
    const hasRefresh = !!diagnostics.has_refresh_token;

    elements.playwrightDiagnostics.classList.add('visible');
    if (elements.playwrightStage) elements.playwrightStage.textContent = diagnostics.stage || '-';
    if (elements.playwrightStrategy) elements.playwrightStrategy.textContent = diagnostics.strategy || '-';
    if (elements.playwrightFailure) elements.playwrightFailure.textContent = diagnostics.failure_reason || '-';
    if (elements.playwrightDiagnosis) {
        elements.playwrightDiagnosis.textContent = diagnostics.diagnosis_label || diagnostics.diagnosis_category || '-';
    }
    if (elements.playwrightDiagnosisHint) elements.playwrightDiagnosisHint.textContent = diagnostics.diagnosis_hint || '-';
    if (elements.playwrightAction) elements.playwrightAction.textContent = diagnostics.recommended_action_hint || diagnostics.recommended_action || '-';
    if (elements.playwrightStrategyFlags) {
        const flags = diagnostics.strategy_flags || {};
        const labels = [];
        if (flags.safe_retry_same_env) labels.push('可原环境轻重试');
        if (flags.should_rotate_proxy) labels.push('建议换代理');
        if (flags.prefer_token_only_retry) labels.push('优先仅重试 Token');
        if (flags.prefer_session_only_retry) labels.push('优先仅补 Session');
        if (flags.needs_manual_review) labels.push('建议人工复核');
        elements.playwrightStrategyFlags.textContent = labels.length ? labels.join(' / ') : '-';
    }
    if (elements.playwrightPostFailureStrategy) {
        const strategy = diagnostics.post_failure_strategy || {};
        const parts = [];
        if (strategy.retry_scope) parts.push(`scope=${strategy.retry_scope}`);
        if (strategy.should_rotate_proxy) parts.push('rotate_proxy');
        if (strategy.safe_retry_same_env) parts.push('same_env_ok');
        if (strategy.needs_manual_review) parts.push('manual_review');
        elements.playwrightPostFailureStrategy.textContent = parts.length ? parts.join(' / ') : '-';
    }
    if (elements.playwrightNextRunPolicy) {
        const policy = diagnostics.next_run_policy || {};
        const labels = [];
        if (policy.fresh_browser_context) labels.push('fresh_context');
        if (policy.rotate_proxy_before_retry) labels.push('rotate_proxy');
        if (policy.prefer_fresh_fingerprint) labels.push('fresh_fingerprint');
        if (policy.isolate_task_cookies) labels.push('isolated_cookies');
        if (policy.reuse_browser_storage === false) labels.push('no_storage_reuse');
        elements.playwrightNextRunPolicy.textContent = labels.length ? labels.join(' / ') : '-';
    }
    if (elements.playwrightTokens) {
        elements.playwrightTokens.textContent = `${hasAccess ? 'Access:有' : 'Access:无'} / ${hasSession ? 'Session:有' : 'Session:无'}`;
    }
    if (elements.playwrightBrowserProbe) {
        elements.playwrightBrowserProbe.textContent = formatPlaywrightProbe(diagnostics.browser_probe);
    }
    if (elements.playwrightPageState) {
        elements.playwrightPageState.textContent = diagnostics.browser_probe?.page_state || '-';
    }
    if (elements.playwrightPageTitle) {
        elements.playwrightPageTitle.textContent = diagnostics.browser_probe?.chatgpt_title || '-';
    }
    if (elements.playwrightIpify) {
        elements.playwrightIpify.textContent = diagnostics.browser_probe?.ipify_before || diagnostics.browser_probe?.proxy || '-';
    }
    if (elements.playwrightRefresh) {
        elements.playwrightRefresh.textContent = hasRefresh ? '已补齐' : '待补/无';
    }
    if (elements.playwrightCallback) {
        elements.playwrightCallback.textContent = diagnostics.callback_url || diagnostics.callback_candidate || '-';
    }
    if (elements.playwrightCurrentUrl) {
        elements.playwrightCurrentUrl.textContent = diagnostics.current_url || '-';
    }
    if (elements.playwrightPath) {
        elements.playwrightPath.textContent = formatPlaywrightPath(diagnostics);
    }
    if (elements.playwrightArtifact) {
        const artifact = diagnostics.artifact;
        if (artifact && artifact.path) {
            const sizeKb = Math.round((Number(artifact.size_bytes || 0) / 1024) || 0);
            const href = getArtifactHref(artifact);
            elements.playwrightArtifact.innerHTML = `<a href="#" class="artifact-preview-link" id="playwright-artifact-preview">预览</a> / <a href="${href}" target="_blank" rel="noopener noreferrer">下载</a> <span style="color: var(--text-muted);">(${sizeKb} KB)</span>`;
            const previewLink = document.getElementById('playwright-artifact-preview');
            if (previewLink) {
                previewLink.addEventListener('click', (event) => {
                    event.preventDefault();
                    openArtifactPreview(artifact, taskContext || currentTask);
                });
            }
        } else {
            elements.playwrightArtifact.textContent = '-';
        }
    }
}

// 更新任务状态
function updateTaskStatus(status) {
    const statusInfo = {
        pending: { text: '等待中', class: 'pending' },
        running: { text: '运行中', class: 'running' },
        completed: { text: '已完成', class: 'completed' },
        failed: { text: '失败', class: 'failed' },
        cancelled: { text: '已取消', class: 'disabled' },
        checking: { text: '检查中', class: 'running' },
        idle: { text: '空闲', class: 'completed' },
        disabled: { text: '已禁用', class: 'disabled' },
        error: { text: '异常', class: 'failed' },
        cancelling: { text: '取消中', class: 'running' },
    };

    const info = statusInfo[status] || { text: status, class: '' };
    elements.taskStatusBadge.textContent = info.text;
    elements.taskStatusBadge.className = `status-badge ${info.class}`;
    elements.taskStatus.textContent = info.text;
}

// 显示批量状态
function showBatchStatus(batch) {
    elements.batchProgressSection.style.display = 'block';
    elements.taskStatusRow.style.display = isAutoMode ? 'grid' : 'none';
    elements.taskStatusBadge.style.display = isAutoMode ? 'inline-flex' : 'none';
    elements.batchProgressText.textContent = `0/${batch.count}`;
    elements.batchProgressPercent.textContent = '0%';
    elements.progressBar.style.width = '0%';
    elements.batchSuccess.textContent = '0';
    elements.batchFailed.textContent = '0';
    elements.batchRemaining.textContent = batch.count;

    // 重置计数器
    elements.batchSuccess.dataset.last = '0';
    elements.batchFailed.dataset.last = '0';
}

function formatAutoMonitorTimestamp(value) {
    if (!value) return '-';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString('zh-CN', {
        hour12: false,
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

function formatDateTime(value) {
    if (!value) return '-';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString('zh-CN', {
        hour12: false,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });
}

function updateAutoMonitorHeader(status, lastCheckedAt) {
    if (!elements.autoMonitorStatusBadge || !elements.autoMonitorLastChecked) return;

    if (!isAutoMode) {
        elements.autoMonitorStatusBadge.style.display = 'none';
        elements.autoMonitorLastChecked.style.display = 'none';
        return;
    }

    const statusInfo = {
        pending: { text: '自动等待', class: 'pending' },
        checking: { text: '自动检查中', class: 'running' },
        running: { text: '自动补货中', class: 'running' },
        idle: { text: '自动空闲', class: 'completed' },
        disabled: { text: '自动已禁用', class: 'disabled' },
        error: { text: '自动异常', class: 'failed' },
        cancelling: { text: '自动取消中', class: 'running' },
    };

    const info = statusInfo[status] || { text: `自动${status || '未知'}`, class: 'pending' };
    elements.autoMonitorStatusBadge.style.display = 'inline-flex';
    elements.autoMonitorStatusBadge.textContent = info.text;
    elements.autoMonitorStatusBadge.className = `status-badge ${info.class}`;
    elements.autoMonitorLastChecked.style.display = 'inline';
    elements.autoMonitorLastChecked.textContent = `最近检查: ${formatAutoMonitorTimestamp(lastCheckedAt)}`;
}

function renderAutoMonitorPlaywrightSummary(stats, alerts) {
    if (!elements.autoMonitorPlaywrightSummary) return;
    if (!isAutoMode) {
        elements.autoMonitorPlaywrightSummary.style.display = 'none';
        elements.autoMonitorPlaywrightSummary.textContent = '';
        return;
    }
    const top = Array.isArray(stats?.top_diagnosis) ? stats.top_diagnosis.slice(0, 3) : [];
    const alertMessages = Array.isArray(alerts?.messages) ? alerts.messages : [];
    const diagnosisText = top.length
        ? `高频诊断: ${top.map(item => `${item.label}(${item.count})`).join(' / ')}`
        : '暂无 Playwright 诊断样本';
    const alertText = alertMessages.length ? `告警: ${alertMessages.join(' / ')}` : '暂无风险告警';
    elements.autoMonitorPlaywrightSummary.style.display = 'block';
    elements.autoMonitorPlaywrightSummary.textContent = `${diagnosisText}；${alertText}`;
}

async function pollAutoRegistrationStatus() {
    try {
        const data = await api.get('/registration/auto-monitor');

        elements.taskStatusRow.style.display = 'grid';
        elements.taskId.textContent = data.current_batch_id || 'auto-registration';
        elements.taskStatus.textContent = data.message || data.status || '-';
        if (elements.taskLastChecked) {
            elements.taskLastChecked.textContent = formatAutoMonitorTimestamp(data.last_checked_at);
        }
        if (elements.taskInventory) {
            const readyCount = data.current_ready_count ?? '-';
            const targetCount = data.target_ready_count ?? '-';
            elements.taskInventory.textContent = `${readyCount} / ${targetCount}`;
        }
        const effectiveStatus = data.batch && data.batch.cancelled && !data.batch.finished
            ? 'cancelling'
            : (data.status || 'pending');
        updateAutoMonitorHeader(effectiveStatus, data.last_checked_at);
        renderAutoMonitorPlaywrightSummary(data.playwright || {}, data.playwright_alerts || {});
        updateTaskStatus(effectiveStatus);

        const logs = data.logs || [];
        for (let i = autoMonitorLastLogIndex; i < logs.length; i++) {
            addLog(getLogType(logs[i]), logs[i]);
        }
        autoMonitorLastLogIndex = logs.length;

        if (data.batch) {
            currentBatch = data.batch;
            activeBatchId = data.batch.batch_id;
            batchCompleted = !!data.batch.finished;
            elements.cancelBtn.disabled = !!data.batch.finished;
            showBatchStatus({ count: data.batch.total });
            updateBatchProgress(data.batch);
            if ((!batchWebSocket || batchWebSocket.readyState === WebSocket.CLOSED) && !data.batch.finished) {
                connectBatchWebSocket(data.batch.batch_id);
            }
        } else {
            currentBatch = null;
            activeBatchId = null;
            elements.cancelBtn.disabled = true;
            elements.batchProgressSection.style.display = 'none';
        }
    } catch (error) {
        console.error('加载自动注册监控失败:', error);
        updateAutoMonitorHeader('error', null);
        renderAutoMonitorPlaywrightSummary({}, {});
        elements.taskStatus.textContent = '自动注册监控获取失败';
        addLog('warning', '[警告] 自动注册监控获取失败');
    }
}

function startAutoRegistrationMonitor() {
    stopAutoRegistrationMonitor();
    pollAutoRegistrationStatus();
    autoMonitorPollingInterval = setInterval(() => {
        pollAutoRegistrationStatus();
    }, 2000);
}

function stopAutoRegistrationMonitor() {
    if (autoMonitorPollingInterval) {
        clearInterval(autoMonitorPollingInterval);
        autoMonitorPollingInterval = null;
    }
}

// 更新批量进度
function updateBatchProgress(data) {
    const progress = ((data.completed / data.total) * 100).toFixed(0);
    elements.batchProgressText.textContent = `${data.completed}/${data.total}`;
    elements.batchProgressPercent.textContent = `${progress}%`;
    elements.progressBar.style.width = `${progress}%`;
    elements.batchSuccess.textContent = data.success;
    elements.batchFailed.textContent = data.failed;
    elements.batchRemaining.textContent = data.total - data.completed;

    // 记录日志（避免重复）
    if (data.completed > 0) {
        const lastSuccess = parseInt(elements.batchSuccess.dataset.last || '0');
        const lastFailed = parseInt(elements.batchFailed.dataset.last || '0');

        if (data.success > lastSuccess) {
            addLog('success', `[成功] 第 ${data.success} 个账号注册成功`);
        }
        if (data.failed > lastFailed) {
            addLog('error', `[失败] 第 ${data.failed} 个账号注册失败`);
        }

        elements.batchSuccess.dataset.last = data.success;
        elements.batchFailed.dataset.last = data.failed;
    }
}

// 加载最近注册的账号
async function loadRecentAccounts() {
    try {
        const data = await api.get('/accounts?page=1&page_size=10');

        if (data.accounts.length === 0) {
            elements.recentAccountsTable.innerHTML = `
                <tr>
                    <td colspan="5">
                        <div class="empty-state" style="padding: var(--spacing-md);">
                            <div class="empty-state-icon">📭</div>
                            <div class="empty-state-title">暂无已注册账号</div>
                        </div>
                    </td>
                </tr>
            `;
            return;
        }

        elements.recentAccountsTable.innerHTML = data.accounts.map(account => `
            <tr data-id="${account.id}">
                <td>${account.id}</td>
                <td>
                    <span style="display:inline-flex;align-items:center;gap:4px;">
                        <span title="${escapeHtml(account.email)}">${escapeHtml(account.email)}</span>
                        <button class="btn-copy-icon copy-email-btn" data-email="${escapeHtml(account.email)}" title="复制邮箱">📋</button>
                    </span>
                </td>
                <td class="password-cell">
                    ${account.password
                        ? `<span style="display:inline-flex;align-items:center;gap:4px;">
                            <span class="password-hidden" title="点击查看">${escapeHtml(account.password.substring(0, 8))}...</span>
                            <button class="btn-copy-icon copy-pwd-btn" data-pwd="${escapeHtml(account.password)}" title="复制密码">📋</button>
                           </span>`
                        : '-'}
                </td>
                <td>
                    ${getStatusIcon(account.status)}
                </td>
            </tr>
        `).join('');

        // 绑定复制按钮事件
        elements.recentAccountsTable.querySelectorAll('.copy-email-btn').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); copyToClipboard(btn.dataset.email); });
        });
        elements.recentAccountsTable.querySelectorAll('.copy-pwd-btn').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); copyToClipboard(btn.dataset.pwd); });
        });

    } catch (error) {
        console.error('加载账号列表失败:', error);
    }
}

function formatHistoryTaskSummary(task) {
    const result = task?.result || {};
    const email = result?.email || task?.result?.email || '-';
    const metadata = result?.metadata || {};
    const diagnostics = getTaskPlaywrightDiagnostics(task);
    const scheme = task?.effective_scheme || metadata?.registration_scheme_label_effective || metadata?.registration_scheme || '-';
    const diagnosis = diagnostics?.diagnosis_label || diagnostics?.diagnosis_category || '';
    const failureReason = diagnostics?.failure_reason || task?.error_message || '';
    const summaryParts = [email, scheme].filter(Boolean);
    if (diagnosis) summaryParts.push(diagnosis);
    else if (failureReason) summaryParts.push(failureReason);
    return summaryParts.join(' / ');
}

function getHistoryTaskDiagnosis(task) {
    const diagnostics = getTaskPlaywrightDiagnostics(task);
    if (diagnostics && typeof diagnostics === 'object') {
        return diagnostics.diagnosis_label || diagnostics.diagnosis_category || diagnostics.failure_reason || '-';
    }
    return task?.error_message || '-';
}

function getTaskStatusBadgeClass(status) {
    if (status === 'failed') return 'failed';
    if (status === 'completed') return 'completed';
    return '';
}

function formatTaskStatusText(status) {
    const mapping = {
        pending: '等待中',
        running: '运行中',
        completed: '已完成',
        failed: '失败',
        cancelled: '已取消',
    };
    return mapping[status] || status || '-';
}

function isPlaywrightFailureTask(task) {
    const result = task?.result || {};
    const metadata = result?.metadata || {};
    const diagnostics = metadata?.playwright_diagnostics;
    return task?.status === 'failed' && diagnostics && typeof diagnostics === 'object';
}

function hasPlaywrightScreenshot(task) {
    return !!(task?.result?.metadata?.playwright_diagnostics?.artifact?.path);
}

function getPlaywrightArtifact(task) {
    return task?.result?.metadata?.playwright_diagnostics?.artifact || null;
}

function getArtifactHref(artifact) {
    if (!artifact?.path) return '';
    return `/api/registration/artifacts/playwright?path=${encodeURIComponent(artifact.path)}`;
}

function openArtifactPreview(artifact, task) {
    if (!artifact?.path || !elements.artifactPreviewOverlay || !elements.artifactPreviewImage) {
        return;
    }

    const href = getArtifactHref(artifact);
    const email = task?.result?.email || '-';
    const sizeKb = Math.round((Number(artifact.size_bytes || 0) / 1024) || 0);
    const createdAt = artifact.created_at
        ? new Date(Number(artifact.created_at) * 1000).toLocaleString('zh-CN', { hour12: false })
        : '-';

    elements.artifactPreviewImage.src = href;
    elements.artifactPreviewImage.alt = `${email} 的失败截图`;
    if (elements.artifactPreviewMeta) {
        elements.artifactPreviewMeta.textContent = `${email} / ${sizeKb} KB / ${createdAt}`;
    }
    if (elements.artifactPreviewDownload) {
        elements.artifactPreviewDownload.href = href;
    }

    elements.artifactPreviewOverlay.classList.add('visible');
    elements.artifactPreviewOverlay.setAttribute('aria-hidden', 'false');
}

function closeArtifactPreview() {
    if (!elements.artifactPreviewOverlay) return;
    elements.artifactPreviewOverlay.classList.remove('visible');
    elements.artifactPreviewOverlay.setAttribute('aria-hidden', 'true');
    if (elements.artifactPreviewImage) {
        elements.artifactPreviewImage.removeAttribute('src');
    }
}

function scrollToPlaywrightDiagnosticsIfVisible() {
    if (!elements.playwrightDiagnostics) return;
    if (!elements.playwrightDiagnostics.classList.contains('visible')) return;
    elements.playwrightDiagnostics.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function hasSavedActiveTask() {
    const saved = sessionStorage.getItem('activeTask');
    if (!saved) return false;
    try {
        const parsed = JSON.parse(saved);
        return !!(parsed?.mode && (parsed?.task_uuid || parsed?.batch_id || parsed?.mode === 'auto'));
    } catch {
        return false;
    }
}

function updateRestoreActiveTaskButton() {
    if (!elements.restoreActiveTaskBtn) return;
    const canRestore = inspectingHistoryTask && hasSavedActiveTask();
    elements.restoreActiveTaskBtn.style.display = 'inline-flex';
    elements.restoreActiveTaskBtn.disabled = !canRestore;
    elements.restoreActiveTaskBtn.title = canRestore
        ? '返回当前正在运行的任务监控视图'
        : (inspectingHistoryTask ? '当前没有可恢复的活动任务' : '先查看一条历史任务后可返回当前任务');
}

function getFilteredHistoryTasks() {
    const filter = elements.historyTaskFilter?.value || 'all';
    if (filter === 'failed') {
        return historyTasks.filter(task => task.status === 'failed');
    }
    if (filter === 'playwright_failed') {
        return historyTasks.filter(isPlaywrightFailureTask);
    }
    if (filter === 'with_screenshot') {
        return historyTasks.filter(hasPlaywrightScreenshot);
    }
    return historyTasks;
}

function renderHistoryTasks() {
    if (!elements.historyTasksTable) return;
    const tasks = getFilteredHistoryTasks();

    if (!tasks.length) {
        elements.historyTasksTable.innerHTML = `
            <tr>
                <td colspan="6">
                    <div class="empty-state" style="padding: var(--spacing-md);">
                        <div class="empty-state-icon">🕘</div>
                        <div class="empty-state-title">当前筛选下暂无历史任务</div>
                    </div>
                </td>
            </tr>`;
        return;
    }

    elements.historyTasksTable.innerHTML = tasks.map(task => {
        const taskIdShort = `${task.task_uuid.substring(0, 8)}...`;
        const completedAt = task.completed_at ? formatDateTime(task.completed_at) : '-';
        const isActive = selectedHistoryTaskUuid === task.task_uuid;
        const statusClass = getTaskStatusBadgeClass(task.status);
        const summary = escapeHtml(formatHistoryTaskSummary(task));
        const diagnosis = escapeHtml(getHistoryTaskDiagnosis(task));
        const hasScreenshot = !!(task?.result?.metadata?.playwright_diagnostics?.artifact?.path);
        const screenshotText = hasScreenshot
            ? `<a href="#" class="artifact-preview-link history-artifact-preview" data-task-uuid="${task.task_uuid}">预览</a>`
            : '无';
        const meta = [task.proxy ? '有代理' : '直连', hasScreenshot ? '有截图' : '无截图'].join(' / ');
        return `
            <tr class="history-task-row ${isActive ? 'active' : ''}" data-task-uuid="${task.task_uuid}">
                <td>
                    <div style="font-weight: 600;">${taskIdShort}</div>
                    <div class="history-task-meta">#${task.id}</div>
                </td>
                <td>
                    <div>${summary}</div>
                    <div class="history-task-meta">${escapeHtml(meta)}</div>
                </td>
                <td>${diagnosis}</td>
                <td>${screenshotText}</td>
                <td>
                    <span class="history-task-badge ${statusClass}">${formatTaskStatusText(task.status)}</span>
                </td>
                <td>${escapeHtml(completedAt)}</td>
            </tr>`;
    }).join('');

    elements.historyTasksTable.querySelectorAll('.history-task-row').forEach(row => {
        row.addEventListener('click', async () => {
            const taskUuid = row.dataset.taskUuid;
            if (!taskUuid) return;
            await loadHistoryTaskDetail(taskUuid);
        });
    });

    elements.historyTasksTable.querySelectorAll('.history-artifact-preview').forEach(link => {
        link.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            const taskUuid = link.dataset.taskUuid;
            const task = historyTasks.find(item => item.task_uuid === taskUuid);
            openArtifactPreview(getPlaywrightArtifact(task), task);
        });
    });
}

async function loadHistoryTasks() {
    try {
        const data = await api.get('/registration/tasks?page=1&page_size=30');
        historyTasks = Array.isArray(data?.tasks) ? data.tasks : [];
        renderHistoryTasks();
        updateRestoreActiveTaskButton();
    } catch (error) {
        console.error('加载历史任务失败:', error);
        if (elements.historyTasksTable) {
            elements.historyTasksTable.innerHTML = `
                <tr>
                    <td colspan="6">
                        <div class="empty-state" style="padding: var(--spacing-md);">
                            <div class="empty-state-icon">⚠️</div>
                            <div class="empty-state-title">加载历史任务失败</div>
                        </div>
                    </td>
                </tr>`;
        }
    }
}

async function loadHistoryTaskDetail(taskUuid) {
    try {
        const data = await api.get(`/registration/tasks/${taskUuid}`);
        selectedHistoryTaskUuid = taskUuid;
        inspectingHistoryTask = true;
        elements.cancelBtn.disabled = true;
        showTaskStatus(data);
        updateTaskStatus(data.status || 'failed');
        elements.batchProgressSection.style.display = 'none';

        const email = data?.result?.email;
        if (email) {
            elements.taskEmail.textContent = email;
        }

        renderHistoryTasks();

        elements.consoleLog.innerHTML = '';
        displayedLogs.clear();

        if (data?.logs) {
            const lines = String(data.logs).split('\n').filter(Boolean);
            for (const line of lines) {
                addLog(getLogType(line), line);
            }
        } else {
            addLog('info', '[系统] 该历史任务没有可用日志');
        }
        updateRestoreActiveTaskButton();
        scrollToPlaywrightDiagnosticsIfVisible();
    } catch (error) {
        console.error('加载任务详情失败:', error);
        toast.error('加载任务详情失败: ' + error.message);
    }
}

async function restoreCurrentActiveTaskView() {
    const restored = await restoreActiveTask();
    if (!restored) {
        toast.warning('当前没有可恢复的活动任务');
        updateRestoreActiveTaskButton();
        return;
    }

    inspectingHistoryTask = false;
    selectedHistoryTaskUuid = null;
    renderHistoryTasks();
    updateRestoreActiveTaskButton();
}

// 开始账号列表轮询
function startAccountsPolling() {
    // 每30秒刷新一次账号列表
    accountsPollingInterval = setInterval(() => {
        loadRecentAccounts();
    }, 30000);
}

function renderTodayStats(total, success, failed, rate) {
    if (elements.todayStatsTotal) {
        elements.todayStatsTotal.textContent = String(Math.max(0, total));
    }
    if (elements.todayStatsSuccess) {
        elements.todayStatsSuccess.textContent = String(Math.max(0, success));
    }
    if (elements.todayStatsFailed) {
        elements.todayStatsFailed.textContent = String(Math.max(0, failed));
    }
    if (elements.todayStatsRate) {
        const safeRate = Math.max(0, rate);
        const rateCard = elements.todayStatsRate.closest('.today-stat-rate');
        elements.todayStatsRate.textContent = `${safeRate.toFixed(1)}%`;
        elements.todayStatsRate.classList.remove('rate-high', 'rate-mid', 'rate-low');
        if (rateCard) {
            rateCard.classList.remove('rate-high', 'rate-mid', 'rate-low');
        }
        if (safeRate >= 70) {
            elements.todayStatsRate.classList.add('rate-high');
            if (rateCard) rateCard.classList.add('rate-high');
        } else if (safeRate < 40) {
            elements.todayStatsRate.classList.add('rate-low');
            if (rateCard) rateCard.classList.add('rate-low');
        } else {
            elements.todayStatsRate.classList.add('rate-mid');
            if (rateCard) rateCard.classList.add('rate-mid');
        }
    }
}

async function loadTodayStats(silent = true) {
    try {
        const data = await api.get('/registration/stats');
        const byStatus = data?.by_status || {};
        const total = Number(data?.today_total ?? data?.today_count ?? 0);
        const success = Number(data?.today_success ?? byStatus.completed ?? 0);
        const failed = Number(data?.today_failed ?? byStatus.failed ?? 0);
        const fallbackRate = total > 0 ? (success / total) * 100 : 0;
        const rate = Number(data?.today_success_rate ?? fallbackRate);
        renderTodayStats(total, success, failed, Number.isFinite(rate) ? rate : 0);
        renderPlaywrightStats(data?.playwright || {});
        renderPlaywrightAlerts(data?.playwright_alerts || {});
    } catch (error) {
        console.error('加载今日统计失败:', error);
        if (!silent) {
            toast.error('加载今日统计失败');
        }
    }
}

function renderPlaywrightAlerts(alerts) {
    if (!elements.pwStatsAlerts) return;
    const messages = Array.isArray(alerts?.messages) ? alerts.messages : [];
    elements.pwStatsAlerts.textContent = messages.length
        ? `告警：${messages.join(' / ')}`
        : '暂无告警';
}

function renderPlaywrightStats(stats) {
    if (elements.pwStatsSamples) elements.pwStatsSamples.textContent = String(Number(stats?.samples || 0));
    if (elements.pwStatsRotate) elements.pwStatsRotate.textContent = String(Number(stats?.rotate_proxy_count || 0));
    if (elements.pwStatsFingerprint) elements.pwStatsFingerprint.textContent = String(Number(stats?.fresh_fingerprint_count || 0));
    if (elements.pwStatsThrottle) elements.pwStatsThrottle.textContent = String(Number(stats?.throttle_count || 0));
    if (elements.pwStatsDiagnosis) {
        const top = Array.isArray(stats?.top_diagnosis) ? stats.top_diagnosis.slice(0, 3) : [];
        elements.pwStatsDiagnosis.textContent = top.length
            ? `高频诊断：${top.map(item => `${item.label}(${item.count})`).join(' / ')}`
            : '暂无 Playwright 样本';
    }
}

function updateTodayStatsResetText() {
    if (!elements.todayStatsReset) return;
    const now = new Date();
    const next = new Date();
    next.setHours(24, 0, 0, 0);
    const remain = Math.max(0, next.getTime() - now.getTime());
    const hours = Math.floor(remain / 3600000);
    const minutes = Math.floor((remain % 3600000) / 60000);
    elements.todayStatsReset.textContent = `重置剩余 ${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
}

function startTodayStatsResetTicker() {
    updateTodayStatsResetText();
    if (todayStatsResetInterval) {
        clearInterval(todayStatsResetInterval);
    }
    todayStatsResetInterval = setInterval(updateTodayStatsResetText, 60000);
}

function startTodayStatsPolling() {
    if (todayStatsPollingInterval) {
        clearInterval(todayStatsPollingInterval);
    }
    todayStatsPollingInterval = setInterval(() => {
        loadTodayStats(true);
    }, 60000);
}

// 添加日志
function addLog(type, message) {
    // 日志去重：使用消息内容的 hash 作为键
    const logKey = `${type}:${message}`;
    if (displayedLogs.has(logKey)) {
        return;  // 已经显示过，跳过
    }
    displayedLogs.add(logKey);

    // 限制去重集合大小，避免内存泄漏
    if (displayedLogs.size > 1000) {
        // 清空一半的记录
        const keys = Array.from(displayedLogs);
        keys.slice(0, 500).forEach(k => displayedLogs.delete(k));
    }

    const line = document.createElement('div');
    line.className = `log-line ${type}`;

    // 添加时间戳
    const timestamp = new Date().toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit'
    });

    line.innerHTML = `<span class="timestamp">[${timestamp}]</span>${escapeHtml(message)}`;
    elements.consoleLog.appendChild(line);

    // 自动滚动到底部
    elements.consoleLog.scrollTop = elements.consoleLog.scrollHeight;

    // 限制日志行数
    const lines = elements.consoleLog.querySelectorAll('.log-line');
    if (lines.length > 500) {
        lines[0].remove();
    }
}

// 获取日志类型
function getLogType(log) {
    if (typeof log !== 'string') return 'info';

    const lowerLog = log.toLowerCase();
    if (lowerLog.includes('error') || lowerLog.includes('失败') || lowerLog.includes('错误')) {
        return 'error';
    }
    if (lowerLog.includes('warning') || lowerLog.includes('警告')) {
        return 'warning';
    }
    if (lowerLog.includes('success') || lowerLog.includes('成功') || lowerLog.includes('完成')) {
        return 'success';
    }
    return 'info';
}

// 重置按钮状态
function resetButtons() {
    elements.startBtn.disabled = false;
    elements.cancelBtn.disabled = isAutoMode;
    stopLogPolling();
    stopBatchPolling();
    if (!isAutoMode) {
        stopAutoRegistrationMonitor();
    }
    clearWebSocketReconnect();
    clearBatchWebSocketReconnect();
    currentTask = null;
    currentBatch = null;
    isBatchMode = false;
    // 重置完成标志
    taskCompleted = false;
    batchCompleted = false;
    // 重置最终状态标志
    taskFinalStatus = null;
    batchFinalStatus = null;
    // 清除活跃任务标识
    activeTaskUuid = null;
    activeBatchId = null;
    // 清除 sessionStorage 持久化状态
    if (!isAutoMode) {
        sessionStorage.removeItem('activeTask');
    }
    // 断开 WebSocket
    disconnectWebSocket();
    disconnectBatchWebSocket();
    // 注意：不重置 isOutlookBatchMode，因为用户可能想继续使用 Outlook 批量模式
}

// HTML 转义
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}


// ============== Outlook 批量注册功能 ==============

// 加载 Outlook 账户列表
async function loadOutlookAccounts() {
    try {
        elements.outlookAccountsContainer.innerHTML = '<div class="loading-placeholder" style="text-align: center; padding: var(--spacing-md); color: var(--text-muted);">加载中...</div>';

        const data = await api.get('/registration/outlook-accounts');
        outlookAccounts = data.accounts || [];

        renderOutlookAccountsList();

        addLog('info', `[系统] 已加载 ${data.total} 个 Outlook 账户 (已注册: ${data.registered_count}, 未注册: ${data.unregistered_count})`);

    } catch (error) {
        console.error('加载 Outlook 账户列表失败:', error);
        elements.outlookAccountsContainer.innerHTML = `<div style="text-align: center; padding: var(--spacing-md); color: var(--text-muted);">加载失败: ${error.message}</div>`;
        addLog('error', `[错误] 加载 Outlook 账户列表失败: ${error.message}`);
    }
}

// 渲染 Outlook 账户列表
function renderOutlookAccountsList() {
    if (outlookAccounts.length === 0) {
        elements.outlookAccountsContainer.innerHTML = '<div style="text-align: center; padding: var(--spacing-md); color: var(--text-muted);">没有可用的 Outlook 账户</div>';
        return;
    }

    const html = outlookAccounts.map(account => `
        <label class="outlook-account-item" style="display: flex; align-items: center; padding: var(--spacing-sm); border-bottom: 1px solid var(--border-light); cursor: pointer; ${account.is_registered ? 'opacity: 0.6;' : ''}" data-id="${account.id}" data-registered="${account.is_registered}">
            <input type="checkbox" class="outlook-account-checkbox" value="${account.id}" ${account.is_registered ? '' : 'checked'} style="margin-right: var(--spacing-sm);">
            <div style="flex: 1;">
                <div style="font-weight: 500;">${escapeHtml(account.email)}</div>
                <div style="font-size: 0.75rem; color: var(--text-muted);">
                    ${account.is_registered
                        ? `<span style="color: var(--success-color);">✓ 已注册</span>`
                        : '<span style="color: var(--primary-color);">未注册</span>'
                    }
                    ${account.has_oauth ? ' | OAuth' : ''}
                </div>
            </div>
        </label>
    `).join('');

    elements.outlookAccountsContainer.innerHTML = html;
}

// 全选
function selectAllOutlookAccounts() {
    const checkboxes = document.querySelectorAll('.outlook-account-checkbox');
    checkboxes.forEach(cb => cb.checked = true);
}

// 只选未注册
function selectUnregisteredOutlook() {
    const items = document.querySelectorAll('.outlook-account-item');
    items.forEach(item => {
        const checkbox = item.querySelector('.outlook-account-checkbox');
        const isRegistered = item.dataset.registered === 'true';
        checkbox.checked = !isRegistered;
    });
}

// 取消全选
function deselectAllOutlookAccounts() {
    const checkboxes = document.querySelectorAll('.outlook-account-checkbox');
    checkboxes.forEach(cb => cb.checked = false);
}

// 处理 Outlook 批量注册
async function handleOutlookBatchRegistration() {
    // 重置批量任务状态
    batchCompleted = false;
    batchFinalStatus = null;
    displayedLogs.clear();  // 清空日志去重集合
    toastShown = false;  // 重置 toast 标志

    // 获取选中的账户
    const selectedIds = [];
    document.querySelectorAll('.outlook-account-checkbox:checked').forEach(cb => {
        selectedIds.push(parseInt(cb.value));
    });

    if (selectedIds.length === 0) {
        toast.error('请选择至少一个 Outlook 账户');
        return;
    }

    const intervalMin = parseInt(elements.outlookIntervalMin.value) || 5;
    const intervalMax = parseInt(elements.outlookIntervalMax.value) || 30;
    const skipRegistered = elements.outlookSkipRegistered.checked;
    const concurrency = parseInt(elements.outlookConcurrencyCount.value) || 3;
    const mode = elements.outlookConcurrencyMode.value || 'pipeline';

    // 禁用开始按钮
    elements.startBtn.disabled = true;
    elements.cancelBtn.disabled = false;

    // 清空日志
    elements.consoleLog.innerHTML = '';

    const requestData = {
        service_ids: selectedIds,
        skip_registered: skipRegistered,
        interval_min: intervalMin,
        interval_max: intervalMax,
        concurrency: Math.min(50, Math.max(1, concurrency)),
        mode: mode,
        auto_upload_cpa: elements.autoUploadCpa ? elements.autoUploadCpa.checked : false,
        cpa_service_ids: elements.autoUploadCpa && elements.autoUploadCpa.checked ? getSelectedServiceIds(elements.cpaServiceSelect) : [],
        auto_upload_sub2api: elements.autoUploadSub2api ? elements.autoUploadSub2api.checked : false,
        sub2api_service_ids: elements.autoUploadSub2api && elements.autoUploadSub2api.checked ? getSelectedServiceIds(elements.sub2apiServiceSelect) : [],
        auto_upload_tm: elements.autoUploadTm ? elements.autoUploadTm.checked : false,
        tm_service_ids: elements.autoUploadTm && elements.autoUploadTm.checked ? getSelectedServiceIds(elements.tmServiceSelect) : [],
    };

    addLog('info', `[系统] 正在启动 Outlook 批量注册 (${selectedIds.length} 个账户)...`);

    try {
        const data = await api.post('/registration/outlook-batch', requestData);

        if (data.to_register === 0) {
            addLog('warning', '[警告] 所有选中的邮箱都已注册，无需重复注册');
            toast.warning('所有选中的邮箱都已注册');
            resetButtons();
            return;
        }

        currentBatch = { batch_id: data.batch_id, ...data, pollingMode: 'outlook_batch' };
        activeBatchId = data.batch_id;  // 保存用于重连
        // 持久化到 sessionStorage，跨页面导航后可恢复
        sessionStorage.setItem('activeTask', JSON.stringify({ batch_id: data.batch_id, mode: isOutlookBatchMode ? 'outlook_batch' : 'batch', total: data.to_register }));
        addLog('info', `[系统] 批量任务已创建: ${data.batch_id}`);
        addLog('info', `[系统] 总数: ${data.total}, 跳过已注册: ${data.skipped}, 待注册: ${data.to_register}`);

        // 初始化批量状态显示
        showBatchStatus({ count: data.to_register });

        // 优先使用 WebSocket
        connectBatchWebSocket(data.batch_id);

    } catch (error) {
        addLog('error', `[错误] 启动失败: ${error.message}`);
        toast.error(error.message);
        resetButtons();
    }
}

// ============== 批量任务 WebSocket 功能 ==============

// 连接批量任务 WebSocket
function connectBatchWebSocket(batchId) {
    activeBatchId = batchId;

    if (batchWebSocket && [WebSocket.OPEN, WebSocket.CONNECTING].includes(batchWebSocket.readyState)) {
        return;
    }

    if (batchWsReconnectTimer) {
        clearTimeout(batchWsReconnectTimer);
        batchWsReconnectTimer = null;
    }
    batchWsManualClose = false;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/api/ws/batch/${batchId}`;

    try {
        const socket = new WebSocket(wsUrl);
        batchWebSocket = socket;

        socket.onopen = () => {
            if (batchWebSocket !== socket) return;
            console.log('批量任务 WebSocket 连接成功');
            clearBatchWebSocketReconnect();
            // 停止轮询（如果有）
            stopBatchPolling();
            // 开始心跳
            startBatchWebSocketHeartbeat();
        };

        socket.onmessage = (event) => {
            if (batchWebSocket !== socket) return;
            const data = JSON.parse(event.data);

            if (data.type === 'log') {
                const logType = getLogType(data.message);
                addLog(logType, data.message);
            } else if (data.type === 'status') {
                // 更新进度
                if (data.total !== undefined) {
                    updateBatchProgress({
                        total: data.total,
                        completed: data.completed || 0,
                        success: data.success || 0,
                        failed: data.failed || 0
                    });
                }

                // 检查是否完成
                if (['completed', 'failed', 'cancelled', 'cancelling'].includes(data.status)) {
                    // 保存最终状态，用于 onclose 判断
                    batchFinalStatus = data.status;
                    batchCompleted = true;

                    // 断开 WebSocket（异步操作）
                    disconnectBatchWebSocket();

                    // 任务完成后再重置按钮
                    resetButtons();

                    // 只显示一次 toast
                    if (!toastShown) {
                        toastShown = true;
                        if (data.status === 'completed') {
                            addLog('success', `[完成] Outlook 批量任务完成！成功: ${data.success}, 失败: ${data.failed}, 跳过: ${data.skipped || 0}`);
                            if (data.success > 0) {
                                toast.success(`Outlook 批量注册完成，成功 ${data.success} 个`);
                                loadRecentAccounts();
                            } else {
                                toast.warning('Outlook 批量注册完成，但没有成功注册任何账号');
                            }
                        } else if (data.status === 'failed') {
                            addLog('error', '[错误] 批量任务执行失败');
                            toast.error('批量任务执行失败');
                        } else if (data.status === 'cancelled' || data.status === 'cancelling') {
                            addLog('warning', '[警告] 批量任务已取消');
                        }
                    }
                }
            } else if (data.type === 'pong') {
                // 心跳响应，忽略
            }
        };

        socket.onclose = (event) => {
            const isCurrentSocket = batchWebSocket === socket;
            if (isCurrentSocket) {
                batchWebSocket = null;
                stopBatchWebSocketHeartbeat();
            }

            console.log('批量任务 WebSocket 连接关闭:', event.code);

            const shouldReconnect = isCurrentSocket &&
                !batchWsManualClose &&
                !batchCompleted &&
                batchFinalStatus === null &&
                activeBatchId === batchId;

            if (shouldReconnect) {
                console.log('批量任务 WebSocket 断开，准备自动重连');
                startCurrentBatchPolling(batchId);
                scheduleBatchWebSocketReconnect(batchId);
            }
        };

        socket.onerror = (error) => {
            if (batchWebSocket !== socket) return;
            console.error('批量任务 WebSocket 错误:', error);
        };

    } catch (error) {
        console.error('批量任务 WebSocket 连接失败:', error);
        startCurrentBatchPolling(batchId);
        scheduleBatchWebSocketReconnect(batchId);
    }
}

// 断开批量任务 WebSocket
function disconnectBatchWebSocket() {
    batchWsManualClose = true;
    clearBatchWebSocketReconnect();
    stopBatchWebSocketHeartbeat();
    if (batchWebSocket) {
        const socket = batchWebSocket;
        batchWebSocket = null;
        socket.close();
    }
}

// 开始批量任务心跳
function startBatchWebSocketHeartbeat() {
    stopBatchWebSocketHeartbeat();
    batchWsHeartbeatInterval = setInterval(() => {
        if (batchWebSocket && batchWebSocket.readyState === WebSocket.OPEN) {
            batchWebSocket.send(JSON.stringify({ type: 'ping' }));
        }
    }, 25000);  // 每 25 秒发送一次心跳
}

// 停止批量任务心跳
function stopBatchWebSocketHeartbeat() {
    if (batchWsHeartbeatInterval) {
        clearInterval(batchWsHeartbeatInterval);
        batchWsHeartbeatInterval = null;
    }
}

// 发送批量任务取消请求
function cancelBatchViaWebSocket() {
    if (batchWebSocket && batchWebSocket.readyState === WebSocket.OPEN) {
        batchWebSocket.send(JSON.stringify({ type: 'cancel' }));
    }
}

// 开始轮询 Outlook 批量状态（降级方案）
function startOutlookBatchPolling(batchId) {
    if (batchPollingInterval) {
        return;
    }

    batchPollingInterval = setInterval(async () => {
        try {
            const data = await api.get(`/registration/outlook-batch/${batchId}`);

            // 更新进度
            updateBatchProgress({
                total: data.total,
                completed: data.completed,
                success: data.success,
                failed: data.failed
            });

            // 输出日志
            if (data.logs && data.logs.length > 0) {
                const lastLogIndex = batchPollingInterval.lastLogIndex || 0;
                for (let i = lastLogIndex; i < data.logs.length; i++) {
                    const log = data.logs[i];
                    const logType = getLogType(log);
                    addLog(logType, log);
                }
                batchPollingInterval.lastLogIndex = data.logs.length;
            }

            // 检查是否完成
            if (data.finished) {
                stopBatchPolling();
                resetButtons();

                // 只显示一次 toast
                if (!toastShown) {
                    toastShown = true;
                    addLog('info', `[完成] Outlook 批量任务完成！成功: ${data.success}, 失败: ${data.failed}, 跳过: ${data.skipped || 0}`);
                    if (data.success > 0) {
                        toast.success(`Outlook 批量注册完成，成功 ${data.success} 个`);
                        loadRecentAccounts();
                    } else {
                        toast.warning('Outlook 批量注册完成，但没有成功注册任何账号');
                    }
                }
            }
        } catch (error) {
            console.error('轮询 Outlook 批量状态失败:', error);
        }
    }, 2000);

    batchPollingInterval.lastLogIndex = 0;
}

// ============== 页面可见性重连机制 ==============

function initVisibilityReconnect() {
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') return;

        if (isAutoMode) {
            pollAutoRegistrationStatus();
        }

        // 页面重新可见时，检查是否需要重连（针对同页面标签切换场景）
        const wsDisconnected = !webSocket || webSocket.readyState === WebSocket.CLOSED;
        const batchWsDisconnected = !batchWebSocket || batchWebSocket.readyState === WebSocket.CLOSED;

        // 单任务重连
        if (activeTaskUuid && !taskCompleted && wsDisconnected) {
            console.log('[重连] 页面重新可见，重连单任务 WebSocket:', activeTaskUuid);
            addLog('info', '[系统] 页面重新激活，正在重连任务监控...');
            connectWebSocket(activeTaskUuid);
        }

        // 批量任务重连
        if (activeBatchId && !batchCompleted && batchWsDisconnected) {
            console.log('[重连] 页面重新可见，重连批量任务 WebSocket:', activeBatchId);
            addLog('info', '[系统] 页面重新激活，正在重连批量任务监控...');
            connectBatchWebSocket(activeBatchId);
        }

        if (isAutoMode && !autoMonitorPollingInterval) {
            addLog('info', '[系统] 页面重新激活，正在恢复自动注册监控...');
            startAutoRegistrationMonitor();
        }
    });
}

// 页面加载时恢复进行中的任务（处理跨页面导航后回到注册页的情况）
async function restoreActiveTask() {
    const saved = sessionStorage.getItem('activeTask');
    if (!saved) return false;

    let state;
    try {
        state = JSON.parse(saved);
    } catch {
        sessionStorage.removeItem('activeTask');
        return false;
    }

    const { mode, task_uuid, batch_id, total } = state;

    if (mode === 'single' && task_uuid) {
        // 查询任务是否仍在运行
        try {
            const data = await api.get(`/registration/tasks/${task_uuid}`);
            if (['completed', 'failed', 'cancelled'].includes(data.status)) {
                sessionStorage.removeItem('activeTask');
                return false;
            }
            // 任务仍在运行，恢复状态
            currentTask = data;
            activeTaskUuid = task_uuid;
            taskCompleted = false;
            taskFinalStatus = null;
            toastShown = false;
            displayedLogs.clear();
            elements.startBtn.disabled = true;
            elements.cancelBtn.disabled = false;
            showTaskStatus(data);
            updateTaskStatus(data.status);
            addLog('info', `[系统] 检测到进行中的任务，正在重连监控... (${task_uuid.substring(0, 8)})`);
            connectWebSocket(task_uuid);
            return true;
        } catch {
            sessionStorage.removeItem('activeTask');
            return false;
        }
    } else if ((mode === 'batch' || mode === 'outlook_batch') && batch_id) {
        // 查询批量任务是否仍在运行
        const endpoint = mode === 'outlook_batch'
            ? `/registration/outlook-batch/${batch_id}`
            : `/registration/batch/${batch_id}`;
        try {
            const data = await api.get(endpoint);
            if (data.finished) {
                sessionStorage.removeItem('activeTask');
                return false;
            }
            // 批量任务仍在运行，恢复状态
            currentBatch = { batch_id, ...data, pollingMode: mode };
            activeBatchId = batch_id;
            isOutlookBatchMode = (mode === 'outlook_batch');
            batchCompleted = false;
            batchFinalStatus = null;
            toastShown = false;
            displayedLogs.clear();
            elements.startBtn.disabled = true;
            elements.cancelBtn.disabled = false;
            showBatchStatus({ count: total || data.total });
            updateBatchProgress(data);
            addLog('info', `[系统] 检测到进行中的批量任务，正在重连监控... (${batch_id.substring(0, 8)})`);
            connectBatchWebSocket(batch_id);
            return true;
        } catch {
            sessionStorage.removeItem('activeTask');
            return false;
        }
    } else if (mode === 'auto') {
        try {
            const data = await api.get('/registration/auto-monitor');
            if (!data.enabled) {
                sessionStorage.removeItem('activeTask');
                return false;
            }

            if (elements.regMode) {
                elements.regMode.value = 'auto';
                handleModeChange({ target: elements.regMode });
            }

            currentTask = null;
            activeTaskUuid = null;
            taskCompleted = false;
            taskFinalStatus = null;
            batchCompleted = !!data.batch?.finished;
            batchFinalStatus = data.batch?.finished ? (data.batch?.cancelled ? 'cancelled' : 'completed') : null;
            elements.startBtn.disabled = false;
            elements.cancelBtn.disabled = !data.batch || !!data.batch.finished;
            displayedLogs.clear();
            autoMonitorLastLogIndex = 0;
            elements.consoleLog.innerHTML = '';
            addLog('info', '[系统] 已恢复自动注册监控状态');
            await pollAutoRegistrationStatus();
            startAutoRegistrationMonitor();
            return true;
        } catch {
            sessionStorage.removeItem('activeTask');
            return false;
        }
    }

    return false;
}


async function refreshOutlookRegistrationStatus() {
    try {
        const ids = outlookAccounts.map(item => item.id).filter(Boolean);
        const data = await api.post('/registration/outlook/check-accounts', { service_ids: ids });
        outlookAccounts = data.accounts || [];
        renderOutlookAccountsList();
        addLog('info', `[系统] Outlook 账户状态已刷新 (已注册: ${data.registered_count}, 未注册: ${data.unregistered_count})`);
        toast.success('Outlook 账户状态已刷新');
    } catch (error) {
        console.error('刷新 Outlook 账户状态失败:', error);
        toast.error('刷新 Outlook 账户状态失败: ' + error.message);
    }
}
