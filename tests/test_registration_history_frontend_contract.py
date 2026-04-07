from pathlib import Path


def test_index_template_contains_history_task_controls():
    template = Path("templates/index.html").read_text(encoding="utf-8")

    assert 'id="history-task-filter"' in template
    assert 'value="all">全部任务<' in template
    assert 'value="failed">失败任务<' in template
    assert 'value="playwright_failed">Playwright 失败<' in template
    assert 'value="with_screenshot">只看有截图<' in template
    assert 'id="restore-active-task-btn"' in template
    assert 'disabled title="当前没有可恢复的活动任务"' in template
    assert '>返回当前任务<' in template
    assert '>诊断<' in template
    assert '>截图<' in template
    assert 'id="history-tasks-table"' in template
    assert 'id="artifact-preview-overlay"' in template
    assert 'id="artifact-preview-image"' in template
    assert 'id="artifact-preview-download"' in template


def test_app_js_contains_history_task_render_contract():
    script = Path("static/js/app.js").read_text(encoding="utf-8")

    assert 'function getTaskPlaywrightDiagnostics(task)' in script
    assert 'function getHistoryTaskDiagnosis(task)' in script
    assert 'function hasPlaywrightScreenshot(task)' in script
    assert 'function getPlaywrightArtifact(task)' in script
    assert 'function openArtifactPreview(artifact, task)' in script
    assert 'function closeArtifactPreview()' in script
    assert 'function renderPlaywrightDiagnostics(diagnostics, taskContext = null)' in script
    assert 'function scrollToPlaywrightDiagnosticsIfVisible()' in script
    assert "elements.restoreActiveTaskBtn.style.display = 'inline-flex';" in script
    assert 'elements.restoreActiveTaskBtn.disabled = !canRestore;' in script
    assert "if (filter === 'with_screenshot')" in script
    assert 'return historyTasks.filter(hasPlaywrightScreenshot);' in script
    assert 'function restoreCurrentActiveTaskView()' in script
    assert "toast.warning('当前没有可恢复的活动任务')" in script


def test_app_js_keeps_history_detail_when_restore_fails():
    script = Path("static/js/app.js").read_text(encoding="utf-8")

    restore_section_start = script.index('async function restoreCurrentActiveTaskView()')
    restore_section_end = script.index('// 开始账号列表轮询')
    restore_section = script[restore_section_start:restore_section_end]

    assert 'const restored = await restoreActiveTask();' in restore_section
    assert 'if (!restored) {' in restore_section
    assert "toast.warning('当前没有可恢复的活动任务')" in restore_section
    assert 'inspectingHistoryTask = false;' in restore_section
    assert 'selectedHistoryTaskUuid = null;' in restore_section
    assert restore_section.index('if (!restored) {') < restore_section.index('inspectingHistoryTask = false;')


def test_app_js_contains_history_log_placeholder_for_empty_logs():
    script = Path("static/js/app.js").read_text(encoding="utf-8")

    assert "elements.consoleLog.innerHTML = '';" in script
    assert "displayedLogs.clear();" in script
    assert "addLog('info', '[系统] 该历史任务没有可用日志');" in script
    assert 'scrollToPlaywrightDiagnosticsIfVisible();' in script


def test_app_js_contains_artifact_preview_entry_points():
    script = Path("static/js/app.js").read_text(encoding="utf-8")

    assert 'history-artifact-preview' in script
    assert "id=\"playwright-artifact-preview\"" in script or "id=\"playwright-artifact-preview\"".replace('\\', '') in script
    assert 'openArtifactPreview(getPlaywrightArtifact(task), task);' in script
    assert 'openArtifactPreview(artifact, taskContext || currentTask);' in script


def test_app_js_prefers_playwright_summary_for_history_detail():
    script = Path("static/js/app.js").read_text(encoding="utf-8")

    assert 'const summary = metadata?.playwright_diagnosis_summary;' in script
    assert 'renderPlaywrightDiagnostics(getTaskPlaywrightDiagnostics(task), task);' in script
