# Runtime Tests

这个目录用于在源码目录外隔离真实运行测试，避免污染主数据目录。

## 目录约定

- `data/`: 测试专用数据库与运行数据
- `logs/`: 测试专用日志
- `run_real_playwright_registration.py`: Playwright 模式真实注册脚本
- `run_real_playwright_registration.ps1`: Windows 启动脚本
- `.env.example`: 可选环境变量模板

## 使用方式

先确认项目虚拟环境已创建并装好依赖：

```powershell
./.venv/Scripts/python -m pip install -r requirements.txt
```

执行 Playwright 真实注册测试：

```powershell
powershell -ExecutionPolicy Bypass -File .\runtime-tests\run_real_playwright_registration.ps1
```

## 说明

- 默认使用 `tempmail` 真实创建邮箱并尝试完整注册。
- 默认强制 `registration_entry_flow=playwright`。
- 默认将 `APP_DATA_DIR` 指向 `runtime-tests/data`。
- 默认将 `APP_LOGS_DIR` 指向 `runtime-tests/logs`。
- 默认将浏览器失败截图、日志、数据库都落在当前副本目录内。
- 该脚本会直接发起真实注册请求，可能产生真实账号数据。
- 如需代理，可设置 `RUNTIME_TEST_PROXY`。

## 示例

```powershell
$env:RUNTIME_TEST_PROXY = 'socks5://127.0.0.1:31152'
./.venv/Scripts/python .\runtime-tests\run_real_playwright_registration.py
```
