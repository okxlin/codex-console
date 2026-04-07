# ABCard Runtime Tests

这个目录用于验证 `registration_entry_flow=abcard` 时的新行为：

- 建号后优先尝试当前会话复用直取
- 失败时补充 `abcard_diagnostics` / `abcard_stoploss`
- 运行数据落在独立 runtime 目录，避免污染主数据

## 默认测试资源

- 代理优先级：
  1. `socks5://127.0.0.1:31156`
  2. `socks5://127.0.0.1:31152`
- 邮箱资源参考：`..\..\..\test-config\yyds-mail-use.md`

## 使用方式

先确认当前仓库虚拟环境已创建并装好依赖：

```powershell
./.venv/Scripts/python -m pip install -r requirements.txt
```

执行真实 ABCard runtime 测试：

```powershell
powershell -ExecutionPolicy Bypass -File .\runtime-tests\abcard\run_real_abcard_registration.ps1
```

也可以直接运行 Python：

```powershell
$env:RUNTIME_TEST_PROXY = 'socks5://127.0.0.1:31156'
./.venv/Scripts/python .\runtime-tests\abcard\run_real_abcard_registration.py
```

## 输出

- 数据目录：`runtime-tests/abcard/data`
- 日志目录：`runtime-tests/abcard/logs`
- 结果文件：`runtime-tests/abcard/logs/real_abcard_registration_result*.json`
